"""Run the single-mesh C3D10 axial-bar analytical verification benchmark."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from axial_bar_definition import (
    AXIAL_FORCE,
    AXIAL_MATERIAL,
    AXIAL_MESH_SIZE_M,
    axial_bar_analysis_definition,
)
from cantilever_mesh_convergence import ConvergenceError, run_json
from cantilever_stress_verification import (
    GAUSS_NATURAL_COORDINATES,
    interpolate_coordinates,
    read_integration_point_stresses,
    tetrahedron_volume,
)
from cantilever_verification import (
    BARYCENTRIC_TOLERANCE,
    INTERPOLATION_AGREEMENT_M,
    VerificationError,
    barycentric_yz,
    quadratic_triangle_weights,
)
from calculix_results import CalculixResultParseError, parse_calculix_dat
from engineering_domain import analysis_definition_to_dict
from numerical_results import NumericalResult
from run_cantilever_solve import as_calculix_c3d10, read_msh


BENCHMARK_ID = "axial-bar-c3d10-v1"
LENGTH_M = 1.0
WIDTH_M = 0.05
HEIGHT_M = 0.05
AREA_M2 = WIDTH_M * HEIGHT_M
YOUNGS_MODULUS_PA = AXIAL_MATERIAL.youngs_modulus_pa
POISSONS_RATIO = AXIAL_MATERIAL.poissons_ratio
FORCE_N = AXIAL_FORCE.magnitude_n
MESH_SIZE_M = AXIAL_MESH_SIZE_M
INTERIOR_X_M = 0.5
TIP_POINT_M = (LENGTH_M, WIDTH_M / 2.0, HEIGHT_M / 2.0)
COORDINATE_TOLERANCE_M = 1.0e-8
COMPONENT_NAMES = ("sigma_xx", "sigma_yy", "sigma_zz", "sigma_xy", "sigma_xz", "sigma_yz")


class AxialVerificationError(RuntimeError):
    """Raised when axial-bar evidence cannot be established deterministically."""


def axial_references(
    force_n: float,
    area_m2: float,
    length_m: float,
    youngs_modulus_pa: float,
    poissons_ratio: float,
) -> dict:
    if force_n <= 0.0 or area_m2 <= 0.0 or length_m <= 0.0 or youngs_modulus_pa <= 0.0:
        raise ValueError("Axial reference inputs must be positive")
    if not -1.0 < poissons_ratio < 0.5:
        raise ValueError("Poisson ratio is outside the isotropic elastic range")
    stress = force_n / area_m2
    axial_strain = stress / youngs_modulus_pa
    return {
        "area_m2": area_m2,
        "nominal_sigma_xx_pa": stress,
        "epsilon_xx": axial_strain,
        "epsilon_yy": -poissons_ratio * axial_strain,
        "epsilon_zz": -poissons_ratio * axial_strain,
        "elongation_m": force_n * length_m / (area_m2 * youngs_modulus_pa),
    }


def isotropic_strain_from_stress(
    stress_pa: list[float] | tuple[float, ...],
    youngs_modulus_pa: float,
    poissons_ratio: float,
) -> tuple[float, float, float]:
    if len(stress_pa) != 6 or youngs_modulus_pa <= 0.0:
        raise ValueError("Six stress components and positive Young's modulus are required")
    sxx, syy, szz = stress_pa[:3]
    return (
        (sxx - poissons_ratio * (syy + szz)) / youngs_modulus_pa,
        (syy - poissons_ratio * (sxx + szz)) / youngs_modulus_pa,
        (szz - poissons_ratio * (sxx + syy)) / youngs_modulus_pa,
    )


def compare_reference(value: float, reference: float) -> dict:
    if reference == 0.0:
        raise ValueError("Comparison reference must be nonzero")
    difference = abs(value - reference)
    relative = difference / abs(reference)
    return {
        "absolute_difference": difference,
        "relative_disagreement": relative,
        "percent_disagreement": relative * 100.0,
    }


def interpolate_face_displacement(
    nodes: dict[int, tuple[float, float, float]],
    load_faces: list[dict],
    displacements: dict[int, tuple[float, float, float]],
) -> tuple[tuple[float, float, float], dict]:
    candidates = []
    for face in load_faces:
        if len(face["nodes"]) != 6:
            raise AxialVerificationError("Free-end face must use six-node triangles")
        if any(abs(nodes[node][0] - LENGTH_M) > COORDINATE_TOLERANCE_M for node in face["nodes"]):
            raise AxialVerificationError("Free-end surface contains a node away from x=1")
        barycentric = barycentric_yz(TIP_POINT_M, [nodes[node] for node in face["nodes"][:3]])
        if all(value >= -BARYCENTRIC_TOLERANCE for value in barycentric):
            weights = quadratic_triangle_weights(barycentric)
            missing = [node for node in face["nodes"] if node not in displacements]
            if missing:
                raise AxialVerificationError(f"Displacement output is missing nodes {missing}")
            vector = tuple(
                sum(
                    weights[index] * displacements[node][axis]
                    for index, node in enumerate(face["nodes"])
                )
                for axis in range(3)
            )
            candidates.append(
                {
                    "surface_element_id": face["id"],
                    "barycentric_coordinates": list(barycentric),
                    "shape_weights": list(weights),
                    "displacement_m": list(vector),
                }
            )
    if not candidates:
        raise AxialVerificationError("Free-end centroid is not contained in the face mesh")
    for axis in range(3):
        values = [candidate["displacement_m"][axis] for candidate in candidates]
        if max(values) - min(values) > INTERPOLATION_AGREEMENT_M:
            raise AxialVerificationError(f"Centroid interpolation candidates disagree on axis {axis}")
    result = tuple(
        sum(candidate["displacement_m"][axis] for candidate in candidates) / len(candidates)
        for axis in range(3)
    )
    return result, {
        "method": "six-node quadratic triangle interpolation",
        "target_m": list(TIP_POINT_M),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def weighted_component_summary(samples: list[dict], value_key: str, names: tuple[str, ...]) -> dict:
    if not samples:
        raise AxialVerificationError("Cannot summarize an empty integration-point region")
    total_weight = sum(sample["weight_m3"] for sample in samples)
    summary = {}
    for index, name in enumerate(names):
        values = [sample[value_key][index] for sample in samples]
        mean = sum(sample["weight_m3"] * sample[value_key][index] for sample in samples) / total_weight
        variance = sum(
            sample["weight_m3"] * (sample[value_key][index] - mean) ** 2 for sample in samples
        ) / total_weight
        rms = math.sqrt(
            sum(sample["weight_m3"] * sample[value_key][index] ** 2 for sample in samples)
            / total_weight
        )
        summary[name] = {
            "mean": mean,
            "minimum": min(values),
            "maximum": max(values),
            "standard_deviation": math.sqrt(variance),
            "rms": rms,
        }
    return summary


def enrich_stress_records(mesh_path: Path, dat_path: Path) -> tuple[list[dict], dict[int, dict], dict]:
    nodes, raw_elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in raw_elements
        if element["type"] == 11 and element["physical_tag"] == 1
    }
    records = read_integration_point_stresses(dat_path)
    if len(records) != 4 * len(volumes):
        raise AxialVerificationError(
            f"Found {len(records)} integration-point records for {len(volumes)} C3D10 elements"
        )
    enriched = []
    for record in records:
        element = volumes.get(record["element_id"])
        if element is None or record["integration_point"] not in range(1, 5):
            raise AxialVerificationError("Stress record cannot be mapped to a C3D10 integration point")
        coordinates = interpolate_coordinates(
            element["nodes"], nodes, GAUSS_NATURAL_COORDINATES[record["integration_point"] - 1]
        )
        volume = tetrahedron_volume([nodes[node] for node in element["nodes"][:4]])
        strain = isotropic_strain_from_stress(
            record["stress_pa"], YOUNGS_MODULUS_PA, POISSONS_RATIO
        )
        enriched.append(
            {
                **record,
                "coordinates_m": list(coordinates),
                "weight_m3": volume / 4.0,
                "reconstructed_normal_strain": list(strain),
            }
        )
    return enriched, volumes, nodes


def enrich_numerical_stress_records(
    mesh_path: Path, numerical_result: NumericalResult
) -> tuple[list[dict], dict[int, dict], dict]:
    """Add mesh-derived coordinates and benchmark strain to neutral stress values."""
    nodes, raw_elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in raw_elements
        if element["type"] == 11 and element["physical_tag"] == 1
    }
    records = numerical_result.integration_point_stresses
    if len(records) != 4 * len(volumes):
        raise AxialVerificationError(
            f"Found {len(records)} integration-point records for {len(volumes)} C3D10 elements"
        )
    enriched = []
    for record in records:
        element = volumes.get(record.element_id)
        if element is None or record.integration_point not in range(1, 5):
            raise AxialVerificationError("Stress record cannot be mapped to a C3D10 integration point")
        coordinates = interpolate_coordinates(
            element["nodes"],
            nodes,
            GAUSS_NATURAL_COORDINATES[record.integration_point - 1],
        )
        volume = tetrahedron_volume([nodes[node] for node in element["nodes"][:4]])
        stress_pa = list(record.stress_pa.as_tuple())
        strain = isotropic_strain_from_stress(stress_pa, YOUNGS_MODULUS_PA, POISSONS_RATIO)
        enriched.append(
            {
                "element_id": record.element_id,
                "integration_point": record.integration_point,
                "stress_pa": stress_pa,
                "coordinates_m": list(coordinates),
                "weight_m3": volume / 4.0,
                "reconstructed_normal_strain": list(strain),
            }
        )
    return enriched, volumes, nodes


def region_record(samples: list[dict], method: str) -> dict:
    return {
        "selection": method,
        "sample_count": len(samples),
        "x_range_m": [
            min(sample["coordinates_m"][0] for sample in samples),
            max(sample["coordinates_m"][0] for sample in samples),
        ],
        "stress_pa": weighted_component_summary(samples, "stress_pa", COMPONENT_NAMES),
        "normal_strain": weighted_component_summary(
            samples, "reconstructed_normal_strain", ("epsilon_xx", "epsilon_yy", "epsilon_zz")
        ),
    }


def non_axial_stress_rms(samples: list[dict]) -> float:
    total_weight = sum(sample["weight_m3"] for sample in samples)
    return math.sqrt(
        sum(
            sample["weight_m3"] * sum(component**2 for component in sample["stress_pa"][1:])
            for sample in samples
        )
        / total_weight
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    scripts = repository / "scripts"
    output_dir = repository / "artifacts" / "axial_bar"
    started = time.perf_counter()
    try:
        mesh, mesh_runtime = run_json(
            [sys.executable, str(scripts / "generate_axial_bar_mesh.py"), "--output-dir", str(output_dir)],
            repository,
        )
        solve, solve_runtime = run_json(
            [sys.executable, str(scripts / "run_axial_bar_solve.py"), "--output-dir", str(output_dir)],
            repository,
        )
        if solve["sanity"]["solver_warnings"]:
            raise AxialVerificationError(f"CalculiX warnings: {solve['sanity']['solver_warnings']}")
        mesh_path = output_dir / "axial_bar.msh"
        dat_path = output_dir / "axial_bar_static.dat"
        nodes, raw_elements = read_msh(mesh_path)
        load_faces = [
            element
            for element in raw_elements
            if element["type"] == 9 and element["physical_tag"] == 3
        ]
        numerical_result = parse_calculix_dat(dat_path)
        displacements = numerical_result.displacement_tuples_by_node()
        centroid_displacement, interpolation = interpolate_face_displacement(
            nodes, load_faces, displacements
        )
        references = axial_references(
            FORCE_N, AREA_M2, LENGTH_M, YOUNGS_MODULUS_PA, POISSONS_RATIO
        )
        displacement_comparison = compare_reference(
            centroid_displacement[0], references["elongation_m"]
        )

        all_samples, volumes, _ = enrich_numerical_stress_records(mesh_path, numerical_result)
        intersecting_ids = {
            element_id
            for element_id, element in volumes.items()
            if min(nodes[node][0] for node in element["nodes"][:4]) <= INTERIOR_X_M
            <= max(nodes[node][0] for node in element["nodes"][:4])
        }
        interior_samples = [
            sample for sample in all_samples if sample["element_id"] in intersecting_ids
        ]
        interior = region_record(
            interior_samples,
            "all four integration points of C3D10 elements whose corner-node X range intersects x=0.5 m",
        )
        interior["intersecting_element_count"] = len(intersecting_ids)
        interior["weighting"] = "equal reference quadrature weights mapped as one quarter of straight tetrahedron volume"
        interior["sigma_xx_reference_comparison"] = compare_reference(
            interior["stress_pa"]["sigma_xx"]["mean"], references["nominal_sigma_xx_pa"]
        )
        interior["strain_reference_comparisons"] = {
            name: compare_reference(interior["normal_strain"][name]["mean"], references[name])
            for name in ("epsilon_xx", "epsilon_yy", "epsilon_zz")
        }

        support_samples = [sample for sample in all_samples if sample["coordinates_m"][0] <= 0.05]
        load_samples = [sample for sample in all_samples if sample["coordinates_m"][0] >= 0.95]
        boundary_regions = {
            "support_one_depth": region_record(support_samples, "raw integration points with x <= 0.05 m"),
            "loaded_end_one_depth": region_record(load_samples, "raw integration points with x >= 0.95 m"),
        }
        boundary_regions["support_one_depth"]["non_axial_stress_rms_pa"] = non_axial_stress_rms(
            support_samples
        )
        boundary_regions["loaded_end_one_depth"]["non_axial_stress_rms_pa"] = non_axial_stress_rms(
            load_samples
        )
        interior["non_axial_stress_rms_pa"] = non_axial_stress_rms(interior_samples)

        stdout_path = output_dir / "axial_bar_static.stdout.txt"
        solver_stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        version_match = re.search(r"CalculiX Version\s+([\d.]+)", solver_stdout)
        if version_match is None or "JOB FINISHED" not in solver_stdout.upper():
            raise AxialVerificationError("CalculiX version or completion marker is missing")
        analysis_definition = axial_bar_analysis_definition(
            mesh["gmsh_version"], version_match.group(1)
        )
        paths = {
            "step": output_dir / "axial_bar.step",
            "mesh": mesh_path,
            "gmsh_calculix_export": output_dir / "axial_bar_mesh.inp",
            "solver_input": output_dir / "axial_bar_static.inp",
            "solver_dat": dat_path,
            "solver_frd": output_dir / "axial_bar_static.frd",
            "solver_stdout": stdout_path,
        }
        artifact = {
            "status": "axial-bar verification completed",
            "benchmark_id": BENCHMARK_ID,
            "units": "SI",
            "analysis_definition": analysis_definition_to_dict(analysis_definition),
            "geometry_m": {"length": LENGTH_M, "width": WIDTH_M, "height": HEIGHT_M},
            "material": {
                "model": "linear elastic isotropic",
                "youngs_modulus_pa": YOUNGS_MODULUS_PA,
                "poissons_ratio": POISSONS_RATIO,
            },
            "load": {
                "traction_pa": [FORCE_N / AREA_M2, 0.0, 0.0],
                "requested_resultant_n": [FORCE_N, 0.0, 0.0],
                "integrated_resultant_n": solve["model"]["integrated_resultant_n"],
                "representation": solve["model"]["calculix_load_representation"],
            },
            "boundary_condition": "entire x=0 face fixed in UX, UY, UZ",
            "numerical_result": {
                "source": "CalculiX DAT parsed once into an immutable solver-neutral snapshot",
                "displacement_count": len(numerical_result.displacements),
                "reaction_count": len(numerical_result.reactions),
                "integration_point_stress_count": len(
                    numerical_result.integration_point_stresses
                ),
                "frd_role": "retained solver artifact; not an authoritative verification source",
            },
            "mesh": {
                "characteristic_size_m": MESH_SIZE_M,
                "element_type": "C3D10",
                "node_count": mesh["mesh"]["node_count"],
                "element_count": mesh["mesh"]["volume_element_count"],
            },
            "analytical_references": references,
            "equilibrium": {
                "applied_resultant_n": solve["model"]["integrated_resultant_n"],
                "fixed_support_reaction_n": solve["sanity"]["fixed_reaction_n"],
                "balances_applied_load": solve["sanity"]["reaction_balances_applied_load"],
            },
            "free_end_centroid_displacement": {
                "quantity": "global displacement vector at (1.0, 0.025, 0.025) m",
                "value_m": list(centroid_displacement),
                "ux_reference_m": references["elongation_m"],
                "ux_comparison": displacement_comparison,
                "interpolation": interpolation,
            },
            "stress_representation": {
                "source": "CalculiX DAT requested by *EL PRINT, ELSET=AXIAL_BAR with S",
                "quantity": "six global true (Cauchy) stress components",
                "location": "four C3D10 integration points per element",
                "post_processing": "not extrapolated to nodes and not nodally averaged",
            },
            "interior_region": interior,
            "strain_reconstruction": {
                "method": "normal strains reconstructed per raw stress tensor using isotropic linear-elastic compliance",
                "equations": {
                    "epsilon_xx": "(sigma_xx - nu*(sigma_yy + sigma_zz))/E",
                    "epsilon_yy": "(sigma_yy - nu*(sigma_xx + sigma_zz))/E",
                    "epsilon_zz": "(sigma_zz - nu*(sigma_xx + sigma_yy))/E",
                },
                "limitation": "not an independent constitutive-law verification",
            },
            "boundary_effect_inspection": boundary_regions,
            "software": {
                "gmsh_version": mesh["gmsh_version"],
                "calculix_version": version_match.group(1),
            },
            "runtimes_seconds": {
                "mesh_generation": mesh_runtime,
                "solve": solve_runtime,
                "total": time.perf_counter() - started,
            },
            "provenance": {
                name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()
            },
            "solver_warnings": solve["sanity"]["solver_warnings"],
            "acceptance_threshold": None,
            "limitations": [
                "single mesh; no axial mesh-convergence evidence",
                "fully fixed support suppresses local Poisson contraction and perturbs the nearby 3D field",
                "stress comparison applies only to the predeclared x=0.5 m interior patch",
                "strain is reconstructed through the same constitutive model supplied to CalculiX",
                "analytical agreement is not physical validation or general solver verification",
            ],
        }
        artifact_path = output_dir / "axial_bar_verification.json"
        artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    except (
        OSError,
        KeyError,
        ValueError,
        subprocess.TimeoutExpired,
        AxialVerificationError,
        CalculixResultParseError,
        ConvergenceError,
        VerificationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps({**artifact, "verification_artifact": str(artifact_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
