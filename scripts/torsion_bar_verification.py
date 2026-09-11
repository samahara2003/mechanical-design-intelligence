"""Run the single-mesh C3D10 square-bar torsion verification benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from axial_bar_verification import (
    AxialVerificationError,
    compare_reference,
    enrich_stress_records,
    weighted_component_summary,
)
from cantilever_mesh_convergence import ConvergenceError, run_json
from cantilever_stress_verification import c3d10_shape_weights, solve_linear_system, von_mises
from cantilever_verification import VerificationError, read_displacements
from run_cantilever_solve import as_calculix_c3d10, read_msh, triangle_area


BENCHMARK_ID = "square-bar-torsion-c3d10-v1"
LENGTH_M = 1.0
SIDE_M = 0.05
YOUNGS_MODULUS_PA = 200.0e9
POISSONS_RATIO = 0.30
TORQUE_N_M = 100.0
MESH_SIZE_M = 0.0125
INTERIOR_X_M = 0.5
TWIST_RATE_SECTION_X_M = (0.25, 0.50, 0.75)
SECTION_CENTER_YZ_M = (SIDE_M / 2.0, SIDE_M / 2.0)
COMPONENT_NAMES = ("sigma_xx", "sigma_yy", "sigma_zz", "sigma_xy", "sigma_xz", "sigma_yz")
TETRAHEDRON_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
# Positive seven-point Dunavant rule, exact through polynomial degree five.
SECTION_TRIANGLE_RULE = (
    ((1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0), 0.225),
    ((0.059715871789770, 0.470142064105115, 0.470142064105115), 0.132394152788506),
    ((0.470142064105115, 0.059715871789770, 0.470142064105115), 0.132394152788506),
    ((0.470142064105115, 0.470142064105115, 0.059715871789770), 0.132394152788506),
    ((0.797426985353087, 0.101286507323456, 0.101286507323456), 0.125939180544827),
    ((0.101286507323456, 0.797426985353087, 0.101286507323456), 0.125939180544827),
    ((0.101286507323456, 0.101286507323456, 0.797426985353087), 0.125939180544827),
)


class TorsionVerificationError(RuntimeError):
    """Raised when deterministic torsion evidence cannot be established."""


def shear_modulus(youngs_modulus_pa: float, poissons_ratio: float) -> float:
    if youngs_modulus_pa <= 0.0 or not -1.0 < poissons_ratio < 0.5:
        raise ValueError("Material properties are outside the isotropic elastic range")
    return youngs_modulus_pa / (2.0 * (1.0 + poissons_ratio))


def square_torsional_constant(side_m: float, coefficient: float = 0.1406) -> float:
    if side_m <= 0.0 or coefficient <= 0.0:
        raise ValueError("Square side and torsional-constant coefficient must be positive")
    return coefficient * side_m**4


def analytical_twist(
    torque_n_m: float, length_m: float, shear_modulus_pa: float, torsional_constant_m4: float
) -> float:
    if length_m <= 0.0 or shear_modulus_pa <= 0.0 or torsional_constant_m4 <= 0.0:
        raise ValueError("Twist reference requires positive length, modulus, and torsional constant")
    return torque_n_m * length_m / (shear_modulus_pa * torsional_constant_m4)


def fit_section_rotation(
    node_ids: list[int],
    nodes: dict[int, tuple[float, float, float]],
    displacements: dict[int, tuple[float, float, float]],
    center_yz_m: tuple[float, float] = SECTION_CENTER_YZ_M,
) -> dict:
    """Fit UY=ty-theta*z' and UZ=tz+theta*y' by equal-node least squares."""
    if len(node_ids) < 2:
        raise ValueError("At least two section nodes are required")
    observations = []
    for node in node_ids:
        if node not in nodes or node not in displacements:
            raise ValueError(f"Section data is missing node {node}")
        _, y, z = nodes[node]
        _, uy, uz = displacements[node]
        observations.append({"y_m": y, "z_m": z, "uy_m": uy, "uz_m": uz, "weight_m2": 1.0})
    result = fit_rotation_observations(observations, center_yz_m)
    result.update(
        {
            "node_count": len(node_ids),
            "method": "equal-node least squares of UY=ty-theta*(z-zc), UZ=tz+theta*(y-yc)",
        }
    )
    return result


def fit_rotation_observations(
    observations: list[dict],
    center_yz_m: tuple[float, float] = SECTION_CENTER_YZ_M,
) -> dict:
    """Fit translation and rotation to weighted transverse displacement observations."""
    if len(observations) < 2 or any(item["weight_m2"] <= 0.0 for item in observations):
        raise ValueError("At least two positive-weight rotation observations are required")
    normal = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    rows = []
    for item in observations:
        y_offset = item["y_m"] - center_yz_m[0]
        z_offset = item["z_m"] - center_yz_m[1]
        for design, observed in (
            ([1.0, 0.0, -z_offset], item["uy_m"]),
            ([0.0, 1.0, y_offset], item["uz_m"]),
        ):
            weight = item["weight_m2"]
            rows.append((design, observed, weight))
            for i in range(3):
                rhs[i] += weight * design[i] * observed
                for j in range(3):
                    normal[i][j] += weight * design[i] * design[j]
    coefficients = solve_linear_system(normal, rhs)
    residuals = [
        (observed - sum(design[i] * coefficients[i] for i in range(3)), weight)
        for design, observed, weight in rows
    ]
    total_row_weight = sum(weight for _, weight in residuals)
    return {
        "rotation_rad": coefficients[2],
        "fitted_rigid_translation_y_m": coefficients[0],
        "fitted_rigid_translation_z_m": coefficients[1],
        "transverse_equation_count": len(rows),
        "residual_rms_m": math.sqrt(
            sum(weight * residual**2 for residual, weight in residuals) / total_row_weight
        ),
        "residual_max_abs_m": max(abs(residual) for residual, _ in residuals),
    }


def tetrahedron_plane_polygon(
    corners: list[tuple[float, float, float]], section_x_m: float, tolerance_m: float = 1.0e-12
) -> list[tuple[float, float, float]]:
    """Return the ordered convex YZ polygon where a straight tetrahedron meets x=constant."""
    points = []
    for first, second in TETRAHEDRON_EDGES:
        start, end = corners[first], corners[second]
        start_delta = start[0] - section_x_m
        end_delta = end[0] - section_x_m
        candidates = []
        if abs(start_delta) <= tolerance_m:
            candidates.append(start)
        if abs(end_delta) <= tolerance_m:
            candidates.append(end)
        if start_delta * end_delta < 0.0:
            fraction = (section_x_m - start[0]) / (end[0] - start[0])
            candidates.append(tuple(start[axis] + fraction * (end[axis] - start[axis]) for axis in range(3)))
        for point in candidates:
            if not any(math.dist(point, existing) <= tolerance_m for existing in points):
                points.append(point)
    if len(points) < 3:
        return []
    center_y = statistics.fmean(point[1] for point in points)
    center_z = statistics.fmean(point[2] for point in points)
    return sorted(points, key=lambda point: math.atan2(point[2] - center_z, point[1] - center_y))


def tetrahedron_natural_coordinates(
    point: tuple[float, float, float], corners: list[tuple[float, float, float]]
) -> tuple[float, float, float]:
    origin = corners[0]
    matrix = [
        [corners[column][row] - origin[row] for column in range(1, 4)]
        for row in range(3)
    ]
    return tuple(solve_linear_system(matrix, [point[row] - origin[row] for row in range(3)]))


def interpolate_c3d10_displacement(
    point: tuple[float, float, float],
    element_nodes: list[int],
    nodes: dict[int, tuple[float, float, float]],
    displacements: dict[int, tuple[float, float, float]],
) -> tuple[float, float, float]:
    natural = tetrahedron_natural_coordinates(point, [nodes[node] for node in element_nodes[:4]])
    weights = c3d10_shape_weights(natural)
    return tuple(
        sum(weights[index] * displacements[node][axis] for index, node in enumerate(element_nodes))
        for axis in range(3)
    )


def extract_interior_section_rotation(
    section_x_m: float,
    volumes: dict[int, dict],
    nodes: dict[int, tuple[float, float, float]],
    displacements: dict[int, tuple[float, float, float]],
) -> dict:
    """Area-integrate an exact-X rotation fit through the C3D10 displacement field."""
    observations = []
    intersected_elements = 0
    triangle_count = 0
    for element in volumes.values():
        corners = [nodes[node] for node in element["nodes"][:4]]
        polygon = tetrahedron_plane_polygon(corners, section_x_m)
        if not polygon:
            continue
        intersected_elements += 1
        for index in range(1, len(polygon) - 1):
            triangle = (polygon[0], polygon[index], polygon[index + 1])
            area = triangle_area(*triangle)
            if area <= 0.0:
                continue
            triangle_count += 1
            for barycentric, rule_weight in SECTION_TRIANGLE_RULE:
                point = tuple(
                    sum(barycentric[vertex] * triangle[vertex][axis] for vertex in range(3))
                    for axis in range(3)
                )
                displacement = interpolate_c3d10_displacement(
                    point, element["nodes"], nodes, displacements
                )
                observations.append(
                    {
                        "x_m": point[0],
                        "y_m": point[1],
                        "z_m": point[2],
                        "uy_m": displacement[1],
                        "uz_m": displacement[2],
                        "weight_m2": area * rule_weight,
                    }
                )
    if not observations:
        raise TorsionVerificationError(f"No tetrahedra intersect section x={section_x_m}")
    section_area = sum(item["weight_m2"] for item in observations)
    if not math.isclose(section_area, SIDE_M**2, rel_tol=1.0e-9, abs_tol=1.0e-12):
        raise TorsionVerificationError(
            f"Section x={section_x_m} integrates to {section_area} m^2, expected {SIDE_M**2}"
        )
    result = fit_rotation_observations(observations)
    actual_x = sum(item["weight_m2"] * item["x_m"] for item in observations) / section_area
    result.update(
        {
            "requested_x_m": section_x_m,
            "actual_evaluated_x_m": actual_x,
            "evaluated_x_range_m": [
                min(item["x_m"] for item in observations),
                max(item["x_m"] for item in observations),
            ],
            "intersected_element_count": intersected_elements,
            "integration_triangle_count": triangle_count,
            "quadrature_point_count": len(observations),
            "integrated_section_area_m2": section_area,
            "method": "exact x-plane/tetrahedron intersections, C3D10 interpolation, and area-weighted degree-five triangle quadrature",
        }
    )
    return result


def fit_twist_rate(section_rotations: list[dict]) -> dict:
    if len(section_rotations) < 2:
        raise ValueError("At least two section rotations are required")
    x_values = [section["actual_evaluated_x_m"] for section in section_rotations]
    theta_values = [section["rotation_rad"] for section in section_rotations]
    x_mean = statistics.fmean(x_values)
    theta_mean = statistics.fmean(theta_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0.0:
        raise ValueError("Section positions must not all coincide")
    beta = sum(
        (x_values[index] - x_mean) * (theta_values[index] - theta_mean)
        for index in range(len(x_values))
    ) / denominator
    alpha = theta_mean - beta * x_mean
    residuals = [theta_values[index] - (alpha + beta * x_values[index]) for index in range(len(x_values))]
    total_variation = sum((value - theta_mean) ** 2 for value in theta_values)
    residual_sum = sum(value**2 for value in residuals)
    return {
        "alpha_rad": alpha,
        "beta_rad_per_m": beta,
        "equation": "theta(x) = alpha + beta*x",
        "section_count": len(section_rotations),
        "residual_rms_rad": math.sqrt(residual_sum / len(residuals)),
        "residual_max_abs_rad": max(abs(value) for value in residuals),
        "r_squared": 1.0 - residual_sum / total_variation if total_variation else None,
    }


def interior_twist_rate_evidence(
    volumes: dict[int, dict],
    nodes: dict[int, tuple[float, float, float]],
    displacements: dict[int, tuple[float, float, float]],
    analytical_rate_rad_per_m: float,
) -> dict:
    sections = [
        extract_interior_section_rotation(section_x, volumes, nodes, displacements)
        for section_x in TWIST_RATE_SECTION_X_M
    ]
    linear_fit = fit_twist_rate(sections)
    linear_fit["analytical_twist_rate_rad_per_m"] = analytical_rate_rad_per_m
    linear_fit["analytical_comparison"] = compare_reference(
        linear_fit["beta_rad_per_m"], analytical_rate_rad_per_m
    )
    return {
        "quantity": "interior section rotation field and fitted twist rate",
        "predeclared_section_x_m": list(TWIST_RATE_SECTION_X_M),
        "section_rotations": sections,
        "linear_fit": linear_fit,
        "interpretation": "cleaner Saint-Venant QoI than total free-end rotation because all fitted sections are away from both end faces",
        "acceptance_threshold": None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="extend an existing torsion artifact from its mesh and DAT without rerunning Gmsh or CalculiX",
    )
    return parser.parse_args()


def extend_existing_artifact(repository: Path, output_dir: Path) -> dict:
    artifact_path = output_dir / "torsion_bar_verification.json"
    mesh_path = output_dir / "torsion_bar.msh"
    dat_path = output_dir / "torsion_bar_static.dat"
    for required in (artifact_path, mesh_path, dat_path):
        if not required.is_file() or required.stat().st_size == 0:
            raise TorsionVerificationError(f"Existing artifact required by --reuse-existing is missing: {required}")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    nodes, raw_elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in raw_elements
        if element["type"] == 11 and element["physical_tag"] == 1
    }
    displacements = read_displacements(dat_path)
    analytical_rate = TORQUE_N_M / (
        artifact["material"]["shear_modulus_pa"]
        * artifact["analytical_reference"]["torsional_constant_m4"]
    )
    artifact["analytical_reference"]["twist_rate_rad_per_m"] = analytical_rate
    artifact["interior_twist_rate"] = interior_twist_rate_evidence(
        volumes, nodes, displacements, analytical_rate
    )
    artifact["post_processing_update"] = {
        "solver_rerun": False,
        "source": "existing torsion_bar.msh and torsion_bar_static.dat",
        "prior_numerical_evidence_preserved": True,
    }
    processor = repository / "scripts" / "torsion_bar_verification.py"
    artifact["provenance"]["interior_twist_post_processor"] = {
        "path": str(processor),
        "sha256": sha256(processor),
    }
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact


def warping_summary(
    node_ids: list[int], displacements: dict[int, tuple[float, float, float]]
) -> dict:
    values = [displacements[node][0] for node in node_ids]
    mean = statistics.fmean(values)
    return {
        "node_count": len(values),
        "minimum_ux_m": min(values),
        "maximum_ux_m": max(values),
        "mean_ux_m": mean,
        "rms_ux_m": math.sqrt(statistics.fmean(value**2 for value in values)),
        "rms_variation_about_mean_m": math.sqrt(statistics.fmean((value - mean) ** 2 for value in values)),
        "method": "equal-node summary over unique nodes on the x=1 surface",
    }


def scalar_weighted_summary(samples: list[dict], key: str) -> dict:
    total_weight = sum(sample["weight_m3"] for sample in samples)
    values = [sample[key] for sample in samples]
    mean = sum(sample["weight_m3"] * sample[key] for sample in samples) / total_weight
    return {
        "mean": mean,
        "minimum": min(values),
        "maximum": max(values),
        "rms": math.sqrt(sum(sample["weight_m3"] * sample[key] ** 2 for sample in samples) / total_weight),
    }


def shear_diagnostics(samples: list[dict]) -> dict:
    if not samples:
        raise TorsionVerificationError("Cannot diagnose an empty stress region")
    total_weight = sum(sample["weight_m3"] for sample in samples)
    longitudinal_shear_rms = math.sqrt(
        sum(sample["weight_m3"] * (sample["stress_pa"][3] ** 2 + sample["stress_pa"][4] ** 2) for sample in samples) / total_weight
    )
    other_component_rms = math.sqrt(
        sum(sample["weight_m3"] * sum(sample["stress_pa"][index] ** 2 for index in (0, 1, 2, 5)) for sample in samples) / total_weight
    )
    quadrants = {}
    for y_label, y_test in (("y_below", lambda y: y < SECTION_CENTER_YZ_M[0]), ("y_above", lambda y: y >= SECTION_CENTER_YZ_M[0])):
        for z_label, z_test in (("z_below", lambda z: z < SECTION_CENTER_YZ_M[1]), ("z_above", lambda z: z >= SECTION_CENTER_YZ_M[1])):
            selected = [sample for sample in samples if y_test(sample["coordinates_m"][1]) and z_test(sample["coordinates_m"][2])]
            quadrants[f"{y_label}_{z_label}"] = {
                "sample_count": len(selected),
                "stress_pa": weighted_component_summary(selected, "stress_pa", COMPONENT_NAMES),
            }
    stress = weighted_component_summary(samples, "stress_pa", COMPONENT_NAMES)
    return {
        "longitudinal_shear_rms_pa": longitudinal_shear_rms,
        "normal_and_transverse_shear_rms_pa": other_component_rms,
        "longitudinal_shear_dominance_ratio": longitudinal_shear_rms / other_component_rms if other_component_rms else None,
        "sigma_xy_changes_sign": stress["sigma_xy"]["minimum"] < 0.0 < stress["sigma_xy"]["maximum"],
        "sigma_xz_changes_sign": stress["sigma_xz"]["minimum"] < 0.0 < stress["sigma_xz"]["maximum"],
        "centroid_tied_quadrants": quadrants,
    }


def region_record(samples: list[dict], selection: str) -> dict:
    for sample in samples:
        sample["von_mises_pa"] = von_mises(sample["stress_pa"])
    return {
        "selection": selection,
        "sample_count": len(samples),
        "x_range_m": [min(sample["coordinates_m"][0] for sample in samples), max(sample["coordinates_m"][0] for sample in samples)],
        "weighting": "equal rule weights mapped as one quarter of straight tetrahedron volume",
        "stress_pa": weighted_component_summary(samples, "stress_pa", COMPONENT_NAMES),
        "von_mises_pa": scalar_weighted_summary(samples, "von_mises_pa"),
        "shear_diagnostics": shear_diagnostics(samples),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parents[1]
    scripts = repository / "scripts"
    output_dir = repository / "artifacts" / "torsion_bar"
    started = time.perf_counter()
    if args.reuse_existing:
        try:
            artifact = extend_existing_artifact(repository, output_dir)
        except (OSError, KeyError, ValueError, TorsionVerificationError, VerificationError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {**artifact, "verification_artifact": str(output_dir / "torsion_bar_verification.json")},
                indent=2,
            )
        )
        return 0
    try:
        mesh, mesh_runtime = run_json(
            [sys.executable, str(scripts / "generate_torsion_bar_mesh.py"), "--output-dir", str(output_dir)], repository
        )
        solve, solve_runtime = run_json(
            [sys.executable, str(scripts / "run_torsion_bar_solve.py"), "--output-dir", str(output_dir)], repository
        )
        if solve["sanity"]["solver_warnings"]:
            raise TorsionVerificationError(f"CalculiX warnings: {solve['sanity']['solver_warnings']}")
        mesh_path = output_dir / "torsion_bar.msh"
        dat_path = output_dir / "torsion_bar_static.dat"
        nodes, raw_elements = read_msh(mesh_path)
        load_faces = [element for element in raw_elements if element["type"] == 9 and element["physical_tag"] == 3]
        load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
        displacements = read_displacements(dat_path)
        rotation_fit = fit_section_rotation(load_nodes, nodes, displacements)
        warping = warping_summary(load_nodes, displacements)

        modulus = shear_modulus(YOUNGS_MODULUS_PA, POISSONS_RATIO)
        torsional_constant = square_torsional_constant(SIDE_M)
        reference_twist = analytical_twist(TORQUE_N_M, LENGTH_M, modulus, torsional_constant)
        reference_twist_rate = TORQUE_N_M / (modulus * torsional_constant)
        rotation_fit["analytical_twist_rad"] = reference_twist
        rotation_fit["analytical_comparison"] = compare_reference(rotation_fit["rotation_rad"], reference_twist)

        all_samples, volumes, _ = enrich_stress_records(mesh_path, dat_path)
        interior_twist = interior_twist_rate_evidence(
            volumes, nodes, displacements, reference_twist_rate
        )
        intersecting_ids = {
            element_id for element_id, element in volumes.items()
            if min(nodes[node][0] for node in element["nodes"][:4]) <= INTERIOR_X_M <= max(nodes[node][0] for node in element["nodes"][:4])
        }
        interior_samples = [sample for sample in all_samples if sample["element_id"] in intersecting_ids]
        interior = region_record(interior_samples, "all four raw integration points of C3D10 elements whose corner-node X range intersects x=0.5 m")
        interior["intersecting_element_count"] = len(intersecting_ids)
        support_samples = [sample for sample in all_samples if sample["coordinates_m"][0] <= SIDE_M]
        support = region_record(support_samples, "raw integration points with x <= 0.05 m (one section width)")
        support["qualitative_comparison_with_interior"] = {
            "longitudinal_shear_rms_ratio": support["shear_diagnostics"]["longitudinal_shear_rms_pa"] / interior["shear_diagnostics"]["longitudinal_shear_rms_pa"],
            "other_component_rms_ratio": support["shear_diagnostics"]["normal_and_transverse_shear_rms_pa"] / interior["shear_diagnostics"]["normal_and_transverse_shear_rms_pa"],
            "interpretation": "ratios characterize the predeclared support band; they are not acceptance criteria or a singularity test",
        }

        stdout_path = output_dir / "torsion_bar_static.stdout.txt"
        solver_stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        version_match = re.search(r"CalculiX Version\s+([\d.]+)", solver_stdout)
        if version_match is None or "JOB FINISHED" not in solver_stdout.upper():
            raise TorsionVerificationError("CalculiX version or completion marker is missing")
        paths = {
            "step": output_dir / "torsion_bar.step",
            "mesh": mesh_path,
            "gmsh_calculix_export": output_dir / "torsion_bar_mesh.inp",
            "solver_input": output_dir / "torsion_bar_static.inp",
            "solver_dat": dat_path,
            "solver_frd": output_dir / "torsion_bar_static.frd",
            "solver_stdout": stdout_path,
        }
        polar_second_moment = 2.0 * SIDE_M**4 / 12.0
        artifact = {
            "status": "square-bar torsion verification completed",
            "benchmark_id": BENCHMARK_ID,
            "units": "SI",
            "geometry_m": {"length": LENGTH_M, "side_y": SIDE_M, "side_z": SIDE_M},
            "material": {"model": "linear elastic isotropic", "youngs_modulus_pa": YOUNGS_MODULUS_PA, "poissons_ratio": POISSONS_RATIO, "shear_modulus_pa": modulus},
            "boundary_condition": "entire x=0 face fixed in UX, UY, UZ",
            "load": {
                "requested_torque_n_m": [TORQUE_N_M, 0.0, 0.0],
                "traction_field": solve["model"]["traction_field"],
                "traction_scale_pa_per_m": solve["model"]["traction_scale_pa_per_m"],
                "surface_integration": solve["model"]["quadrature"],
                "representation": solve["model"]["calculix_load_representation"],
                "integrated_force_resultant_n": solve["model"]["integrated_resultant_n"],
                "integrated_moment_about_loaded_face_centroid_n_m": solve["model"]["integrated_moment_about_face_centroid_n_m"],
            },
            "mesh": {"characteristic_size_m": MESH_SIZE_M, "element_type": "C3D10", "node_count": mesh["mesh"]["node_count"], "element_count": mesh["mesh"]["volume_element_count"]},
            "analytical_reference": {
                "model": "Saint-Venant uniform torsion",
                "torsional_constant_formula": "Jt = 0.1406*a^4 for a square",
                "torsional_constant_coefficient": 0.1406,
                "torsional_constant_m4": torsional_constant,
                "polar_second_moment_m4": polar_second_moment,
                "polar_moment_warning": "Iy+Iz is not the Saint-Venant torsional constant for a non-circular section",
                "twist_formula": "theta = T*L/(G*Jt)",
                "twist_rad": reference_twist,
                "twist_rate_rad_per_m": reference_twist_rate,
            },
            "equilibrium": {
                "applied_force_resultant_n": solve["model"]["integrated_resultant_n"],
                "applied_moment_n_m": solve["model"]["integrated_moment_about_face_centroid_n_m"],
                "fixed_support_reaction_force_n": solve["sanity"]["fixed_reaction_n"],
                "fixed_support_reaction_moment_about_loaded_face_centroid_n_m": solve["sanity"]["fixed_reaction_moment_about_loaded_face_centroid_n_m"],
                "balances_applied_torque": solve["sanity"]["reaction_balances_applied_torque"],
            },
            "free_end_rotation": rotation_fit,
            "free_end_warping": warping,
            "interior_twist_rate": interior_twist,
            "stress_representation": {
                "source": "CalculiX DAT requested by *EL PRINT, ELSET=TORSION_BAR with S",
                "quantity": "six global true (Cauchy) stress components at four C3D10 integration points",
                "post_processing": "not extrapolated to nodes and not nodally averaged; von Mises is derived from each raw tensor",
            },
            "interior_region": interior,
            "support_end_effect_region": support,
            "software": {"gmsh_version": mesh["gmsh_version"], "calculix_version": version_match.group(1)},
            "runtimes_seconds": {"mesh_generation": mesh_runtime, "solve": solve_runtime, "total": time.perf_counter() - started},
            "provenance": {name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()},
            "post_processing_update": {
                "solver_rerun": True,
                "source": "newly generated torsion mesh and solver DAT",
                "prior_numerical_evidence_preserved": True,
            },
            "solver_warnings": solve["sanity"]["solver_warnings"],
            "acceptance_threshold": None,
            "limitations": [
                "single mesh; no torsion mesh-convergence evidence",
                "fully fixed support perturbs local Saint-Venant warping and stress behavior",
                "linear end traction is resultant-equivalent but is not the exact square-section Saint-Venant end traction",
                "free-end UX is inspected as warping evidence without analytical warping-function verification",
                "interior stress checks are qualitative and do not use a circular-shaft shear formula",
                "analytical agreement is not physical validation or general solver verification",
            ],
        }
        artifact_path = output_dir / "torsion_bar_verification.json"
        artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    except (
        OSError,
        KeyError,
        ValueError,
        subprocess.TimeoutExpired,
        AxialVerificationError,
        TorsionVerificationError,
        ConvergenceError,
        VerificationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({**artifact, "verification_artifact": str(artifact_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
