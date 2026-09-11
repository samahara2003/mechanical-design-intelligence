"""Compare cantilever free-end centroid UZ with Euler-Bernoulli theory."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

from analysis_results import (
    AnalysisResultBuildError,
    ResolvedAnalysisContext,
    analysis_result_to_dict,
    build_analysis_result,
)
from calculix_results import CalculixResultParseError, parse_calculix_dat
from cantilever_definition import cantilever_analysis_definition
from cantilever_stress_verification import enrich_integration_point_stresses
from engineering_domain import analysis_definition_to_dict
from numerical_results import Vector3
from run_cantilever_solve import read_msh
from surface_load_mapping import map_uniform_force_to_c3d10_faces


BENCHMARK_ID = "cantilever-eb-tip-uz-v1"
LENGTH_M = 1.0
WIDTH_M = 0.05
HEIGHT_M = 0.05
YOUNGS_MODULUS_PA = 200.0e9
POISSONS_RATIO = 0.30
RESULTANT_FORCE_N = 1000.0
TRACTION_PA = 400000.0
AREA_M2 = 0.0025
TIP_POINT_M = (LENGTH_M, WIDTH_M / 2.0, HEIGHT_M / 2.0)
COORDINATE_TOLERANCE_M = 1.0e-8
BARYCENTRIC_TOLERANCE = 1.0e-10
INTERPOLATION_AGREEMENT_M = 1.0e-10


class VerificationError(RuntimeError):
    """Raised when the requested verification quantity cannot be established."""


def rectangular_second_moment(width_m: float, height_m: float) -> float:
    if width_m <= 0.0 or height_m <= 0.0:
        raise ValueError("cross-section dimensions must be positive")
    return width_m * height_m**3 / 12.0


def cantilever_tip_displacement(
    force_n: float, length_m: float, youngs_modulus_pa: float, second_moment_m4: float
) -> float:
    if force_n < 0.0:
        raise ValueError("force magnitude must be nonnegative")
    if length_m <= 0.0 or youngs_modulus_pa <= 0.0 or second_moment_m4 <= 0.0:
        raise ValueError("length, Young's modulus, and second moment must be positive")
    return force_n * length_m**3 / (3.0 * youngs_modulus_pa * second_moment_m4)


def compare_displacements(fea_uz_m: float, analytical_magnitude_m: float) -> dict[str, float | bool]:
    if analytical_magnitude_m <= 0.0:
        raise ValueError("analytical displacement magnitude must be positive")
    absolute_error = abs(abs(fea_uz_m) - abs(analytical_magnitude_m))
    relative_error = absolute_error / abs(analytical_magnitude_m)
    return {
        "absolute_error_m": absolute_error,
        "relative_error": relative_error,
        "percent_error": relative_error * 100.0,
        "fea_sign_is_negative_z": fea_uz_m < 0.0,
    }


def read_displacements(path: Path) -> dict[int, tuple[float, float, float]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    displacements: dict[int, tuple[float, float, float]] = {}
    reading = False
    for line in lines:
        if re.search(r"displacements \(vx,vy,vz\)", line, re.IGNORECASE):
            reading = True
            continue
        if reading:
            match = re.match(
                r"\s*(\d+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)\s*$",
                line,
            )
            if match:
                displacements[int(match.group(1))] = tuple(
                    float(value) for value in match.groups()[1:]
                )
            elif displacements and line.strip():
                break
    if not displacements:
        raise VerificationError(f"Expected nodal displacement output is missing from {path}")
    return displacements


def barycentric_yz(
    point: tuple[float, float, float],
    corners: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    y, z = point[1], point[2]
    y1, z1 = corners[0][1], corners[0][2]
    y2, z2 = corners[1][1], corners[1][2]
    y3, z3 = corners[2][1], corners[2][2]
    denominator = (y2 - y3) * (z1 - z3) + (z3 - z2) * (y1 - y3)
    if abs(denominator) <= 1.0e-20:
        raise VerificationError("Degenerate triangle encountered on the load face")
    first = ((y2 - y3) * (z - z3) + (z3 - z2) * (y - y3)) / denominator
    second = ((y3 - y1) * (z - z3) + (z1 - z3) * (y - y3)) / denominator
    return first, second, 1.0 - first - second


def quadratic_triangle_weights(barycentric: tuple[float, float, float]) -> tuple[float, ...]:
    first, second, third = barycentric
    return (
        first * (2.0 * first - 1.0),
        second * (2.0 * second - 1.0),
        third * (2.0 * third - 1.0),
        4.0 * first * second,
        4.0 * second * third,
        4.0 * third * first,
    )


def linear_triangle_weights(barycentric: tuple[float, float, float]) -> tuple[float, ...]:
    return barycentric


def interpolate_tip_uz(
    nodes: dict[int, tuple[float, float, float]],
    load_faces: list[dict],
    displacements: dict[int, tuple[float, float, float]],
) -> tuple[float, dict]:
    direct_nodes = [
        node_id
        for node_id, coordinates in nodes.items()
        if all(
            abs(coordinates[index] - TIP_POINT_M[index]) <= COORDINATE_TOLERANCE_M
            for index in range(3)
        )
    ]
    if len(direct_nodes) > 1:
        raise VerificationError(f"Multiple mesh nodes match the tip point: {direct_nodes}")
    if direct_nodes:
        node_id = direct_nodes[0]
        if node_id not in displacements:
            raise VerificationError(f"Displacement output is missing for tip node {node_id}")
        return displacements[node_id][2], {"method": "direct node", "node_id": node_id}

    candidates = []
    for face in load_faces:
        face_node_count = len(face["nodes"])
        if face_node_count not in (3, 6):
            raise VerificationError(
                f"Load-face element {face['id']} is not a supported three- or six-node triangle"
            )
        if any(
            abs(nodes[node_id][0] - LENGTH_M) > COORDINATE_TOLERANCE_M
            for node_id in face["nodes"]
        ):
            raise VerificationError(f"Load-face element {face['id']} is not on x=L")
        barycentric = barycentric_yz(
            TIP_POINT_M, [nodes[node_id] for node_id in face["nodes"][:3]]
        )
        if all(value >= -BARYCENTRIC_TOLERANCE for value in barycentric):
            missing = [node_id for node_id in face["nodes"] if node_id not in displacements]
            if missing:
                raise VerificationError(
                    f"Displacement output is missing face nodes {missing} for element {face['id']}"
                )
            weights = (
                linear_triangle_weights(barycentric)
                if face_node_count == 3
                else quadratic_triangle_weights(barycentric)
            )
            uz = sum(
                weights[index] * displacements[node_id][2]
                for index, node_id in enumerate(face["nodes"])
            )
            candidates.append(
                {
                    "surface_element_id": face["id"],
                    "node_ids": face["nodes"],
                    "barycentric_coordinates": list(barycentric),
                    "shape_weights": list(weights),
                    "uz_m": uz,
                }
            )
    if not candidates:
        raise VerificationError("The free-end centroid is not contained in the load-face mesh")
    values = [candidate["uz_m"] for candidate in candidates]
    if max(values) - min(values) > INTERPOLATION_AGREEMENT_M:
        raise VerificationError(
            "Adjacent load-face interpolations disagree at the free-end centroid: "
            f"{values}"
        )
    return sum(values) / len(values), {
        "method": (
            "three-node linear triangle interpolation"
            if len(load_faces[0]["nodes"]) == 3
            else "six-node quadratic triangle interpolation"
        ),
        "target_m": list(TIP_POINT_M),
        "direct_node_present": False,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/cantilever"),
        help="directory containing mesh and solve artifacts (default: artifacts/cantilever)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    mesh_path = output_dir / "cantilever.msh"
    mesh_summary_path = output_dir / "cantilever_mesh_summary.json"
    deck_path = output_dir / "cantilever_static.inp"
    dat_path = output_dir / "cantilever_static.dat"
    frd_path = output_dir / "cantilever_static.frd"
    stdout_path = output_dir / "cantilever_static.stdout.txt"
    record_path = output_dir / "cantilever_verification.json"
    try:
        for required in (mesh_path, mesh_summary_path, deck_path, dat_path, frd_path, stdout_path):
            if not required.is_file() or required.stat().st_size == 0:
                raise VerificationError(f"Required artifact is missing or empty: {required}")
        mesh_summary = json.loads(mesh_summary_path.read_text(encoding="utf-8"))
        if mesh_summary["artifacts"]["msh_sha256"] != sha256(mesh_path):
            raise VerificationError("Mesh checksum does not match its generation summary")
        if mesh_summary.get("units") != "SI (metres)":
            raise VerificationError("Mesh units are missing or ambiguous")

        nodes, elements = read_msh(mesh_path)
        element_type = mesh_summary["mesh"]["volume_element_type"]
        surface_type = {"C3D4": 2, "C3D10": 9}.get(element_type)
        if surface_type is None:
            raise VerificationError(f"Unsupported element type: {element_type}")
        load_faces = [
            element
            for element in elements
            if element["type"] == surface_type and element["physical_tag"] == 3
        ]
        if not load_faces:
            raise VerificationError(f"No load-face elements were found for {element_type}")
        numerical_result = parse_calculix_dat(dat_path)
        displacements = numerical_result.displacement_tuples_by_node()
        fea_uz_m, selection = interpolate_tip_uz(nodes, load_faces, displacements)

        second_moment_m4 = rectangular_second_moment(WIDTH_M, HEIGHT_M)
        analytical_magnitude_m = cantilever_tip_displacement(
            RESULTANT_FORCE_N, LENGTH_M, YOUNGS_MODULUS_PA, second_moment_m4
        )
        if not math.isclose(analytical_magnitude_m, 0.0032, rel_tol=1.0e-12):
            raise VerificationError(
                f"Analytical formula produced {analytical_magnitude_m}, not the independently expected 0.0032 m"
            )
        comparison = compare_displacements(fea_uz_m, analytical_magnitude_m)
        if not comparison["fea_sign_is_negative_z"]:
            raise VerificationError(f"FEA tip UZ has the wrong sign: {fea_uz_m}")

        solver_stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        solver_match = re.search(r"CalculiX Version\s+([\d.]+)", solver_stdout)
        if solver_match is None or "JOB FINISHED" not in solver_stdout.upper():
            raise VerificationError("Solver version or successful completion is missing from stdout")

        analysis_definition = analysis_result = None
        if element_type == "C3D10":
            analysis_definition = cantilever_analysis_definition(
                mesh_summary["gmsh_version"],
                solver_match.group(1),
                mesh_summary["mesh_size_m"],
            )
            mapped_nodal_forces, integrated_area, _ = map_uniform_force_to_c3d10_faces(
                analysis_definition.loads[0], load_faces, nodes, AREA_M2
            )
            if not math.isclose(integrated_area, AREA_M2, rel_tol=1.0e-10, abs_tol=1.0e-12):
                raise VerificationError("Reusable force mapping did not recover the loaded area")
            mapped_applied_resultant = Vector3(
                *(
                    sum(vector[axis] for vector in mapped_nodal_forces.values())
                    for axis in range(3)
                )
            )
            enriched_stresses, _, _ = enrich_integration_point_stresses(
                mesh_path, numerical_result
            )
            analysis_result = build_analysis_result(
                analysis_definition,
                numerical_result,
                ResolvedAnalysisContext(
                    node_count=mesh_summary["mesh"]["node_count"],
                    element_count=mesh_summary["mesh"]["volume_element_count"],
                    integrated_applied_resultant_n=mapped_applied_resultant,
                ),
                node_locations_m={
                    node_id: Vector3(*coordinates) for node_id, coordinates in nodes.items()
                },
                integration_point_locations_m={
                    (sample["element_id"], sample["integration_point"]): Vector3(
                        *sample["coordinates_m"]
                    )
                    for sample in enriched_stresses
                },
            )

        record = {
            "status": "comparison completed",
            "benchmark_id": BENCHMARK_ID,
            "units": "SI",
            "geometry_m": {"length": LENGTH_M, "width": WIDTH_M, "height": HEIGHT_M},
            "material": {
                "youngs_modulus_pa": YOUNGS_MODULUS_PA,
                "poissons_ratio": POISSONS_RATIO,
            },
            "load": {
                "resultant_n": [0.0, 0.0, -RESULTANT_FORCE_N],
                "uniform_traction_pa": [0.0, 0.0, -TRACTION_PA],
            },
            "boundary_condition": "entire x=0 face fixed in UX, UY, UZ",
            "element_type": mesh_summary["mesh"]["volume_element_type"],
            "mesh_size_m": mesh_summary["mesh_size_m"],
            "node_count": mesh_summary["mesh"]["node_count"],
            "volume_element_count": mesh_summary["mesh"]["volume_element_count"],
            "analytical": {
                "model": "Euler-Bernoulli cantilever with end transverse resultant",
                "second_moment_m4": second_moment_m4,
                "tip_displacement_magnitude_m": analytical_magnitude_m,
                "loading_direction": "-Z",
            },
            "fea": {
                "quantity": "global UZ at free-end cross-section centroid",
                "tip_uz_m": fea_uz_m,
                "selection": selection,
            },
            "comparison": comparison,
            "acceptance_threshold": None,
            "provenance": {
                "gmsh_version": mesh_summary["gmsh_version"],
                "calculix_version": solver_match.group(1),
                "mesh": {"path": str(mesh_path), "sha256": sha256(mesh_path)},
                "solver_input": {"path": str(deck_path), "sha256": sha256(deck_path)},
                "solver_dat": {"path": str(dat_path), "sha256": sha256(dat_path)},
                "solver_frd": {"path": str(frd_path), "sha256": sha256(frd_path)},
            },
            "limitations": [
                "single mesh density; no mesh convergence evidence",
                "Euler-Bernoulli theory is an idealized beam model while FEA uses a 3D solid",
                "fixed-end local effects are not assessed by this tip-displacement comparison",
                "stress verification is not established",
            ],
        }
        if analysis_definition is not None and analysis_result is not None:
            record["analysis_definition"] = analysis_definition_to_dict(analysis_definition)
            record["analysis_result"] = analysis_result_to_dict(analysis_result)
            record["limitations"][-1] = (
                "global raw stress is diagnostic; formal section-stress verification remains separate"
            )
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        AnalysisResultBuildError,
        CalculixResultParseError,
        VerificationError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps({**record, "verification_artifact": str(record_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
