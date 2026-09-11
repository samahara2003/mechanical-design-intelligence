"""Verify C3D10 cantilever section bending stress using integration-point output."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from calculix_results import CalculixResultParseError, parse_calculix_dat
from cantilever_mesh_convergence import LEVELS, run_json
from engineering_postprocessing import von_mises_stress_pa
from numerical_results import NumericalResult, StressTensor
from run_cantilever_solve import as_calculix_c3d10, read_msh


SECTION_X_M = 0.2
WIDTH_M = 0.05
HEIGHT_M = 0.05
LENGTH_M = 1.0
FORCE_N = 1000.0
SECOND_MOMENT_M4 = WIDTH_M * HEIGHT_M**3 / 12.0
REFERENCE_OUTER_FIBER_PA = 38.4e6
GAUSS_LOW = 0.138196601125011
GAUSS_HIGH = 0.585410196624968
GAUSS_NATURAL_COORDINATES = (
    (GAUSS_LOW, GAUSS_LOW, GAUSS_LOW),
    (GAUSS_HIGH, GAUSS_LOW, GAUSS_LOW),
    (GAUSS_LOW, GAUSS_HIGH, GAUSS_LOW),
    (GAUSS_LOW, GAUSS_LOW, GAUSS_HIGH),
)
ESTABLISHED_CENTROID_UZ_M = {
    "coarse": -0.003191318918668,
    "medium": -0.003192322994337,
    "fine": -0.003193498343309,
    "finer": -0.003194353096108,
}
C3D10_EDGE_MIDSIDES = (
    (0, 1, 4),
    (1, 2, 5),
    (2, 0, 6),
    (0, 3, 7),
    (1, 3, 8),
    (2, 3, 9),
)


class StressVerificationError(RuntimeError):
    """Raised when stress evidence cannot be extracted reproducibly."""


def c3d10_shape_weights(natural: tuple[float, float, float]) -> tuple[float, ...]:
    xi, eta, zeta = natural
    barycentric = (1.0 - xi - eta - zeta, xi, eta, zeta)
    first, second, third, fourth = barycentric
    return (
        first * (2.0 * first - 1.0),
        second * (2.0 * second - 1.0),
        third * (2.0 * third - 1.0),
        fourth * (2.0 * fourth - 1.0),
        4.0 * first * second,
        4.0 * second * third,
        4.0 * third * first,
        4.0 * first * fourth,
        4.0 * second * fourth,
        4.0 * third * fourth,
    )


def interpolate_coordinates(
    element_nodes: list[int],
    nodes: dict[int, tuple[float, float, float]],
    natural: tuple[float, float, float],
) -> tuple[float, float, float]:
    if len(element_nodes) != 10:
        raise ValueError("C3D10 coordinate interpolation requires ten nodes")
    weights = c3d10_shape_weights(natural)
    return tuple(
        sum(weights[index] * nodes[node_id][axis] for index, node_id in enumerate(element_nodes))
        for axis in range(3)
    )


def tetrahedron_volume(
    corners: list[tuple[float, float, float]],
) -> float:
    if len(corners) != 4:
        raise ValueError("tetrahedron volume requires four corners")
    a, b, c, d = corners
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    ad = tuple(d[index] - a[index] for index in range(3))
    cross = (
        ac[1] * ad[2] - ac[2] * ad[1],
        ac[2] * ad[0] - ac[0] * ad[2],
        ac[0] * ad[1] - ac[1] * ad[0],
    )
    return abs(sum(ab[index] * cross[index] for index in range(3))) / 6.0


def read_integration_point_stresses(path: Path) -> list[dict]:
    header = re.compile(
        r"stresses \(elem, integ\.pnt\.,sxx,syy,szz,sxy,sxz,syz\)", re.IGNORECASE
    )
    row = re.compile(
        r"\s*(\d+)\s+(\d+)\s+" + r"\s+".join([r"([-+\d.Ee]+)"] * 6) + r"\s*$"
    )
    records: list[dict] = []
    reading = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if header.search(line):
            reading = True
            continue
        if not reading:
            continue
        match = row.match(line)
        if match:
            values = [float(value) for value in match.groups()[2:]]
            records.append(
                {
                    "element_id": int(match.group(1)),
                    "integration_point": int(match.group(2)),
                    "stress_pa": values,
                }
            )
        elif records and line.strip():
            break
    if not records:
        raise StressVerificationError(f"No integration-point stress table found in {path}")
    return records


def solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1.0e-20:
            raise StressVerificationError("Section reconstruction matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def reconstruction_basis(coordinates: tuple[float, float, float]) -> tuple[float, ...]:
    x_centered = coordinates[0] - SECTION_X_M
    y_centered = coordinates[1] - WIDTH_M / 2.0
    z_centered = coordinates[2] - HEIGHT_M / 2.0
    return (1.0, x_centered, y_centered, z_centered, x_centered * z_centered)


def fit_section_field(samples: list[dict]) -> dict:
    if len(samples) < 5:
        raise StressVerificationError("At least five stress samples are required")
    size = 5
    normal = [[0.0] * size for _ in range(size)]
    right = [0.0] * size
    total_weight = 0.0
    weighted_mean_numerator = 0.0
    for sample in samples:
        basis = reconstruction_basis(tuple(sample["coordinates_m"]))
        weight = float(sample["weight_m3"])
        stress = float(sample["stress_pa"][0])
        total_weight += weight
        weighted_mean_numerator += weight * stress
        for row in range(size):
            right[row] += weight * basis[row] * stress
            for column in range(size):
                normal[row][column] += weight * basis[row] * basis[column]
    coefficients = solve_linear_system(normal, right)
    residuals = []
    weighted_squared_residual = 0.0
    weighted_total_variation = 0.0
    weighted_mean = weighted_mean_numerator / total_weight
    for sample in samples:
        basis = reconstruction_basis(tuple(sample["coordinates_m"]))
        fitted = sum(coefficients[index] * basis[index] for index in range(size))
        residual = float(sample["stress_pa"][0]) - fitted
        residuals.append(residual)
        weighted_squared_residual += float(sample["weight_m3"]) * residual**2
        weighted_total_variation += float(sample["weight_m3"]) * (
            float(sample["stress_pa"][0]) - weighted_mean
        ) ** 2
    intercept, axial_slope, width_slope, depth_slope, axial_depth_interaction = coefficients
    upper = intercept + depth_slope * HEIGHT_M / 2.0
    lower = intercept - depth_slope * HEIGHT_M / 2.0
    neutral_axis_z = None if depth_slope == 0.0 else HEIGHT_M / 2.0 - intercept / depth_slope
    outer_magnitude = (abs(upper) + abs(lower)) / 2.0
    disagreement = abs(outer_magnitude - REFERENCE_OUTER_FIBER_PA)
    return {
        "basis": "1, (x-section_x), (y-width/2), (z-height/2), (x-section_x)*(z-height/2)",
        "weighting": "each integration point weighted by one quarter of its parent tetrahedron volume",
        "coefficients": {
            "section_intercept_pa": intercept,
            "axial_slope_pa_per_m": axial_slope,
            "width_slope_pa_per_m": width_slope,
            "section_depth_slope_pa_per_m": depth_slope,
            "axial_depth_interaction_pa_per_m2": axial_depth_interaction,
        },
        "weighted_rmse_pa": math.sqrt(weighted_squared_residual / total_weight),
        "maximum_absolute_residual_pa": max(abs(value) for value in residuals),
        "weighted_r_squared": (
            None
            if weighted_total_variation == 0.0
            else 1.0 - weighted_squared_residual / weighted_total_variation
        ),
        "neutral_axis_sigma_xx_pa": intercept,
        "reconstructed_neutral_axis_z_m": neutral_axis_z,
        "upper_outer_fiber_sigma_xx_pa": upper,
        "lower_outer_fiber_sigma_xx_pa": lower,
        "outer_fibers_have_opposite_signs": upper * lower < 0.0,
        "outer_fiber_magnitude_pa": outer_magnitude,
        "absolute_euler_bernoulli_difference_pa": disagreement,
        "relative_euler_bernoulli_disagreement": disagreement / REFERENCE_OUTER_FIBER_PA,
        "percent_euler_bernoulli_disagreement": disagreement / REFERENCE_OUTER_FIBER_PA * 100.0,
    }


def simple_z_cross_check(samples: list[dict]) -> dict:
    """Fit raw sigma_xx against z alone with equal sample weighting."""
    if len(samples) < 2:
        raise StressVerificationError("At least two stress samples are required")
    centered_z = [sample["coordinates_m"][2] - HEIGHT_M / 2.0 for sample in samples]
    stresses = [float(sample["stress_pa"][0]) for sample in samples]
    mean_z = sum(centered_z) / len(centered_z)
    mean_stress = sum(stresses) / len(stresses)
    denominator = sum((value - mean_z) ** 2 for value in centered_z)
    if denominator == 0.0:
        raise StressVerificationError("Simple z-only cross-check has no depth variation")
    slope = sum(
        (centered_z[index] - mean_z) * (stresses[index] - mean_stress)
        for index in range(len(samples))
    ) / denominator
    intercept = mean_stress - slope * mean_z
    residuals = [
        stresses[index] - (intercept + slope * centered_z[index])
        for index in range(len(samples))
    ]
    total_variation = sum((stress - mean_stress) ** 2 for stress in stresses)
    squared_residual = sum(value**2 for value in residuals)
    upper = intercept + slope * HEIGHT_M / 2.0
    lower = intercept - slope * HEIGHT_M / 2.0
    magnitude = (abs(upper) + abs(lower)) / 2.0
    disagreement = abs(magnitude - REFERENCE_OUTER_FIBER_PA)
    return {
        "method": "equal-weight ordinary least squares of raw section-patch sigma_xx versus centered z only",
        "shared_assumption": "linear variation with z; this is a sensitivity cross-check, not independent proof of linearity",
        "excluded_main_fit_terms": [
            "axial position",
            "width position",
            "axial-depth interaction",
            "tetrahedron-volume weighting",
        ],
        "intercept_pa": intercept,
        "slope_pa_per_m": slope,
        "upper_outer_fiber_sigma_xx_pa": upper,
        "lower_outer_fiber_sigma_xx_pa": lower,
        "outer_fiber_magnitude_pa": magnitude,
        "percent_euler_bernoulli_disagreement": disagreement / REFERENCE_OUTER_FIBER_PA * 100.0,
        "rmse_pa": math.sqrt(squared_residual / len(samples)),
        "r_squared": None if total_variation == 0.0 else 1.0 - squared_residual / total_variation,
    }


def raw_depth_band_evidence(samples: list[dict]) -> dict:
    bands = {
        "lower_quarter": lambda z: z <= HEIGHT_M / 4.0,
        "neutral_quarter": lambda z: abs(z - HEIGHT_M / 2.0) <= HEIGHT_M / 8.0,
        "upper_quarter": lambda z: z >= 3.0 * HEIGHT_M / 4.0,
    }
    summaries = {}
    for name, contains in bands.items():
        values = [float(item["stress_pa"][0]) for item in samples if contains(item["coordinates_m"][2])]
        if not values:
            raise StressVerificationError(f"No raw integration points in depth band {name}")
        summaries[name] = {
            "sample_count": len(values),
            "mean_sigma_xx_pa": sum(values) / len(values),
            "minimum_sigma_xx_pa": min(values),
            "maximum_sigma_xx_pa": max(values),
        }
    return {
        "method": "unweighted summaries of raw integration-point sigma_xx in fixed geometric depth bands",
        "bands_m": {
            "lower_quarter": [0.0, HEIGHT_M / 4.0],
            "neutral_quarter": [3.0 * HEIGHT_M / 8.0, 5.0 * HEIGHT_M / 8.0],
            "upper_quarter": [3.0 * HEIGHT_M / 4.0, HEIGHT_M],
        },
        "summaries": summaries,
    }


def maximum_midside_deviation(
    volumes: dict[int, dict], nodes: dict[int, tuple[float, float, float]]
) -> float:
    maximum = 0.0
    for element in volumes.values():
        connectivity = element["nodes"]
        for first, second, midside in C3D10_EDGE_MIDSIDES:
            expected = tuple(
                (nodes[connectivity[first]][axis] + nodes[connectivity[second]][axis]) / 2.0
                for axis in range(3)
            )
            actual = nodes[connectivity[midside]]
            maximum = max(
                maximum,
                math.sqrt(sum((actual[axis] - expected[axis]) ** 2 for axis in range(3))),
            )
    return maximum


def von_mises(stress: list[float]) -> float:
    return von_mises_stress_pa(StressTensor(*stress))


def enrich_integration_point_stresses(
    mesh_path: Path, numerical_result: NumericalResult
) -> tuple[list[dict], dict[int, dict], dict[int, tuple[float, float, float]]]:
    """Attach audited C3D10 locations/weights to solver-neutral raw stresses."""
    nodes, raw_elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in raw_elements
        if element["type"] == 11 and element["physical_tag"] == 1
    }
    records = numerical_result.integration_point_stresses
    expected_records = len(volumes) * 4
    if len(records) != expected_records:
        raise StressVerificationError(
            f"Found {len(records)} stress records; expected {expected_records} for four-point C3D10 integration"
        )
    enriched = []
    for record in records:
        element = volumes.get(record.element_id)
        if element is None or record.integration_point not in range(1, 5):
            raise StressVerificationError("Stress record does not map to a C3D10 integration point")
        coordinates = interpolate_coordinates(
            element["nodes"], nodes, GAUSS_NATURAL_COORDINATES[record.integration_point - 1]
        )
        volume = tetrahedron_volume([nodes[node] for node in element["nodes"][:4]])
        stress_pa = list(record.stress_pa.as_tuple())
        enriched.append(
            {
                "element_id": record.element_id,
                "integration_point": record.integration_point,
                "stress_pa": stress_pa,
                "coordinates_m": list(coordinates),
                "weight_m3": volume / 4.0,
                "von_mises_pa": von_mises_stress_pa(record.stress_pa),
            }
        )
    return enriched, volumes, nodes


def extract_stress_evidence(mesh_path: Path, dat_path: Path) -> dict:
    numerical_result = parse_calculix_dat(dat_path)
    enriched, volumes, nodes = enrich_integration_point_stresses(
        mesh_path, numerical_result
    )
    intersecting_ids = {
        element_id
        for element_id, element in volumes.items()
        if min(nodes[node][0] for node in element["nodes"][:4]) <= SECTION_X_M
        <= max(nodes[node][0] for node in element["nodes"][:4])
    }
    section_samples = [item for item in enriched if item["element_id"] in intersecting_ids]
    if not section_samples:
        raise StressVerificationError("No C3D10 elements intersect the frozen section")
    reconstruction = fit_section_field(section_samples)
    cross_check = simple_z_cross_check(section_samples)
    raw_bands = raw_depth_band_evidence(section_samples)
    sample_x_min = min(item["coordinates_m"][0] for item in section_samples)
    sample_x_max = max(item["coordinates_m"][0] for item in section_samples)
    closest_upper_distance = min(HEIGHT_M - item["coordinates_m"][2] for item in section_samples)
    closest_lower_distance = min(item["coordinates_m"][2] for item in section_samples)

    minimum_sxx = min(enriched, key=lambda item: item["stress_pa"][0])
    maximum_sxx = max(enriched, key=lambda item: item["stress_pa"][0])
    maximum_mises = max(enriched, key=lambda item: item["von_mises_pa"])
    diagnostic = {}
    for name, item, value in (
        ("minimum_sigma_xx", minimum_sxx, minimum_sxx["stress_pa"][0]),
        ("maximum_sigma_xx", maximum_sxx, maximum_sxx["stress_pa"][0]),
        ("maximum_von_mises", maximum_mises, maximum_mises["von_mises_pa"]),
    ):
        diagnostic[name] = {
            "value_pa": value,
            "stress_components_pa": item["stress_pa"],
            "element_id": item["element_id"],
            "integration_point": item["integration_point"],
            "coordinates_m": item["coordinates_m"],
        }
    return {
        "stress_representation": {
            "quantity": "true (Cauchy) stress in global rectangular coordinates",
            "source": "CalculiX .dat output requested with *EL PRINT, ELSET=BEAM and S",
            "evaluation_location": "four C3D10 integration points per element",
            "post_processing": "raw integration-point output; not extrapolated to nodes and not nodally averaged",
            "component_order": ["sxx", "syy", "szz", "sxy", "sxz", "syz"],
        },
        "section": {
            "x_m": SECTION_X_M,
            "selection": "all four integration points of every tetrahedron whose corner-node x range intersects x=0.2 m",
            "intersecting_element_count": len(intersecting_ids),
            "sample_count": len(section_samples),
            "sample_x_range_m": [sample_x_min, sample_x_max],
            "sample_x_span_m": sample_x_max - sample_x_min,
            "outer_fiber_extrapolation": {
                "classification": "extrapolated from interior integration-point samples; not directly evaluated",
                "closest_upper_sample_distance_m": closest_upper_distance,
                "closest_lower_sample_distance_m": closest_lower_distance,
            },
            "reconstruction": reconstruction,
            "simple_z_only_cross_check": cross_check,
            "raw_depth_band_evidence": raw_bands,
        },
        "geometry_mapping_audit": {
            "maximum_midside_deviation_from_edge_midpoint_m": maximum_midside_deviation(
                volumes, nodes
            ),
            "volume_weighting_applicability": "constant-Jacobian straight-sided C3D10 geometry in this benchmark",
        },
        "diagnostic_global_integration_point_peaks": diagnostic,
    }


def add_successive_changes(results: list[dict]) -> None:
    previous = None
    for result in results:
        current = result["section"]["reconstruction"]["outer_fiber_magnitude_pa"]
        if previous is None:
            absolute = relative = percent = None
        else:
            absolute = abs(current - previous)
            relative = absolute / abs(previous)
            percent = relative * 100.0
        result["successive_outer_fiber_change_pa"] = absolute
        result["successive_relative_change"] = relative
        result["successive_percent_change"] = percent
        previous = current


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    study_root = repository / "artifacts" / "cantilever" / "stress_verification"
    study_path = repository / "artifacts" / "cantilever" / "cantilever_stress_verification.json"
    scripts = repository / "scripts"
    results = []
    started = time.perf_counter()
    try:
        for level, mesh_size in LEVELS:
            level_started = time.perf_counter()
            output_dir = study_root / level
            mesh, mesh_runtime = run_json(
                [
                    sys.executable,
                    str(scripts / "generate_cantilever_mesh.py"),
                    "--output-dir",
                    str(output_dir),
                    "--mesh-size",
                    repr(mesh_size),
                    "--element-type",
                    "C3D10",
                ],
                repository,
            )
            solve, solve_runtime = run_json(
                [sys.executable, str(scripts / "run_cantilever_solve.py"), "--output-dir", str(output_dir)],
                repository,
            )
            displacement, displacement_runtime = run_json(
                [sys.executable, str(scripts / "cantilever_verification.py"), "--output-dir", str(output_dir)],
                repository,
            )
            if solve["sanity"]["solver_warnings"]:
                raise StressVerificationError(
                    f"CalculiX warnings for {level}: {solve['sanity']['solver_warnings']}"
                )
            if not solve["sanity"]["reaction_balances_applied_z_load"]:
                raise StressVerificationError(f"Reaction equilibrium failed for {level}")
            centroid_uz = displacement["fea"]["tip_uz_m"]
            if not math.isclose(
                centroid_uz, ESTABLISHED_CENTROID_UZ_M[level], rel_tol=0.0, abs_tol=5.0e-10
            ):
                raise StressVerificationError(
                    f"Displacement regression failed for {level}: {centroid_uz}"
                )
            extraction_started = time.perf_counter()
            evidence = extract_stress_evidence(
                output_dir / "cantilever.msh", output_dir / "cantilever_static.dat"
            )
            extraction_runtime = time.perf_counter() - extraction_started
            results.append(
                {
                    "level": level,
                    "mesh_size_m": mesh_size,
                    "node_count": mesh["mesh"]["node_count"],
                    "c3d10_element_count": mesh["mesh"]["volume_element_count"],
                    **evidence,
                    "equilibrium": {
                        "applied_resultant_n": solve["model"]["integrated_resultant_n"],
                        "fixed_reaction_n": solve["sanity"]["fixed_reaction_n"],
                    },
                    "displacement_regression": {
                        "centroid_uz_m": centroid_uz,
                        "established_centroid_uz_m": ESTABLISHED_CENTROID_UZ_M[level],
                        "within_absolute_tolerance_m": 5.0e-10,
                    },
                    "solver_warnings": solve["sanity"]["solver_warnings"],
                    "gmsh_version": mesh["gmsh_version"],
                    "calculix_version": displacement["provenance"]["calculix_version"],
                    "runtimes_seconds": {
                        "mesh_generation": mesh_runtime,
                        "solve": solve_runtime,
                        "displacement_verification": displacement_runtime,
                        "stress_extraction": extraction_runtime,
                        "total": time.perf_counter() - level_started,
                    },
                    "provenance": displacement["provenance"],
                }
            )
            extrapolation = results[-1]["section"]["outer_fiber_extrapolation"]
            extrapolation["closest_upper_distance_over_mesh_size"] = (
                extrapolation["closest_upper_sample_distance_m"] / mesh_size
            )
            extrapolation["closest_lower_distance_over_mesh_size"] = (
                extrapolation["closest_lower_sample_distance_m"] / mesh_size
            )
            results[-1]["section"]["sample_x_span_over_mesh_size"] = (
                results[-1]["section"]["sample_x_span_m"] / mesh_size
            )
        add_successive_changes(results)
        study = {
            "status": "C3D10 section stress verification study completed",
            "benchmark_id": "cantilever-c3d10-section-sxx-v1",
            "units": "SI",
            "frozen_section_x_m": SECTION_X_M,
            "section_depths_from_fixed_face": SECTION_X_M / HEIGHT_M,
            "section_selected_before_results": True,
            "analytical": {
                "model": "Euler-Bernoulli cantilever with end transverse resultant",
                "equation": "sigma_xx(z_centered) = -M_y*z_centered/I",
                "moment_magnitude_at_section_nm": FORCE_N * (LENGTH_M - SECTION_X_M),
                "internal_moment_y_at_section_nm": -FORCE_N * (LENGTH_M - SECTION_X_M),
                "sign_convention": "+X beam axis, +Z upper fiber, applied force in -Z; upper fiber is tensile",
                "second_moment_m4": SECOND_MOMENT_M4,
                "upper_outer_fiber_sigma_xx_pa": REFERENCE_OUTER_FIBER_PA,
                "lower_outer_fiber_sigma_xx_pa": -REFERENCE_OUTER_FIBER_PA,
                "outer_fiber_magnitude_pa": REFERENCE_OUTER_FIBER_PA,
            },
            "acceptance_threshold": None,
            "results": results,
            "limitations": [
                "Euler-Bernoulli and three-dimensional elasticity are non-identical mathematical models",
                "x/h=4 is a frozen benchmark section, not a universal Saint-Venant boundary",
                "the reconstruction extrapolates from interior integration points to exact outer fibers",
                "global integration-point peaks are diagnostic and are not the verification quantity",
                "the study does not establish general stress convergence, singularity, or physical validation",
            ],
            "total_runtime_seconds": time.perf_counter() - started,
        }
        study_path.parent.mkdir(parents=True, exist_ok=True)
        study_path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    except (
        OSError,
        KeyError,
        ValueError,
        subprocess.TimeoutExpired,
        CalculixResultParseError,
        StressVerificationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("C3D10 integration-point section-stress study (no acceptance threshold)")
    print(
        f"{'level':<8} {'samples':>8} {'upper MPa':>12} {'lower MPa':>12} "
        f"{'magnitude':>12} {'EB diff %':>11} {'change %':>10} {'R^2':>10}"
    )
    for result in results:
        fit = result["section"]["reconstruction"]
        change = result["successive_percent_change"]
        print(
            f"{result['level']:<8} {result['section']['sample_count']:>8d} "
            f"{fit['upper_outer_fiber_sigma_xx_pa'] / 1.0e6:>12.6f} "
            f"{fit['lower_outer_fiber_sigma_xx_pa'] / 1.0e6:>12.6f} "
            f"{fit['outer_fiber_magnitude_pa'] / 1.0e6:>12.6f} "
            f"{fit['percent_euler_bernoulli_disagreement']:>11.6f} "
            f"{('-' if change is None else f'{change:.6f}'):>10} "
            f"{fit['weighted_r_squared']:>10.7f}"
        )
    print(f"\nArtifact: {study_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
