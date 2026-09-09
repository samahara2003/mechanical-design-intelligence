"""Run the four-level C3D10 cantilever displacement convergence study."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path


LEVELS = (
    ("coarse", 0.025),
    ("medium", 0.025 / math.sqrt(2.0)),
    ("fine", 0.0125),
    ("finer", 0.0125 / math.sqrt(2.0)),
)


class ConvergenceError(RuntimeError):
    """Raised when a study level does not preserve the experiment invariants."""


def successive_change(current_uz_m: float, previous_uz_m: float | None) -> tuple[float | None, float | None]:
    if previous_uz_m is None:
        return None, None
    if previous_uz_m == 0.0:
        raise ValueError("previous displacement must be nonzero")
    absolute_change = abs(current_uz_m - previous_uz_m)
    return absolute_change, absolute_change / abs(previous_uz_m)


def add_successive_changes(results: list[dict]) -> list[dict]:
    enriched = []
    previous_uz_m = None
    for result in results:
        current = dict(result)
        absolute, relative = successive_change(current["centroid_uz_m"], previous_uz_m)
        current["absolute_change_from_previous_m"] = absolute
        current["relative_change_from_previous"] = relative
        current["percent_change_from_previous"] = None if relative is None else relative * 100.0
        enriched.append(current)
        previous_uz_m = current["centroid_uz_m"]
    return enriched


def run_json(command: list[str], cwd: Path) -> tuple[dict, float]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
    )
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        raise ConvergenceError(
            f"Command failed with exit {result.returncode}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout), elapsed
    except json.JSONDecodeError as error:
        raise ConvergenceError(
            f"Command did not return JSON: {' '.join(command)}\n{result.stdout}"
        ) from error


def print_table(results: list[dict]) -> None:
    print("Convergence-study results (no acceptance threshold)")
    print(
        f"{'level':<8} {'size (m)':>12} {'nodes':>9} {'C3D10':>9} "
        f"{'centroid UZ (m)':>18} {'analytical err %':>17} {'change %':>12}"
    )
    for result in results:
        change = result["percent_change_from_previous"]
        change_text = "-" if change is None else f"{change:.6f}"
        print(
            f"{result['level']:<8} {result['mesh_size_m']:>12.8f} "
            f"{result['node_count']:>9d} {result['volume_element_count']:>9d} "
            f"{result['centroid_uz_m']:>18.12g} "
            f"{result['percent_analytical_error']:>17.9f} {change_text:>12}"
        )


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    scripts = repository / "scripts"
    study_root = repository / "artifacts" / "cantilever" / "convergence"
    study_path = repository / "artifacts" / "cantilever" / "cantilever_mesh_convergence.json"
    raw_results = []
    study_started = time.perf_counter()
    try:
        for level, mesh_size_m in LEVELS:
            output_dir = study_root / level
            level_started = time.perf_counter()
            mesh, mesh_runtime = run_json(
                [
                    sys.executable,
                    str(scripts / "generate_cantilever_mesh.py"),
                    "--output-dir",
                    str(output_dir),
                    "--mesh-size",
                    repr(mesh_size_m),
                ],
                repository,
            )
            solve, solve_runtime = run_json(
                [
                    sys.executable,
                    str(scripts / "run_cantilever_solve.py"),
                    "--output-dir",
                    str(output_dir),
                ],
                repository,
            )
            verification, verification_runtime = run_json(
                [
                    sys.executable,
                    str(scripts / "cantilever_verification.py"),
                    "--output-dir",
                    str(output_dir),
                ],
                repository,
            )
            warnings = solve["sanity"]["solver_warnings"]
            if warnings:
                raise ConvergenceError(f"CalculiX warnings at {level}: {warnings}")
            if solve["model"]["integrated_resultant_n"][2] >= 0.0:
                raise ConvergenceError(f"Applied load has the wrong sign at {level}")
            if not solve["sanity"]["reaction_balances_applied_z_load"]:
                raise ConvergenceError(f"Support reaction does not balance the load at {level}")
            if not verification["comparison"]["fea_sign_is_negative_z"]:
                raise ConvergenceError(f"Centroid displacement has the wrong sign at {level}")

            raw_results.append(
                {
                    "level": level,
                    "mesh_size_m": mesh_size_m,
                    "node_count": mesh["mesh"]["node_count"],
                    "volume_element_type": mesh["mesh"]["volume_element_type"],
                    "volume_element_count": mesh["mesh"]["volume_element_count"],
                    "fixed_face_element_count": solve["model"]["fixed_face_element_count"],
                    "load_face_element_count": solve["model"]["load_face_element_count"],
                    "centroid_uz_m": verification["fea"]["tip_uz_m"],
                    "centroid_displacement_magnitude_m": abs(verification["fea"]["tip_uz_m"]),
                    "analytical_displacement_magnitude_m": verification["analytical"][
                        "tip_displacement_magnitude_m"
                    ],
                    "absolute_analytical_difference_m": verification["comparison"][
                        "absolute_error_m"
                    ],
                    "relative_analytical_error": verification["comparison"]["relative_error"],
                    "percent_analytical_error": verification["comparison"]["percent_error"],
                    "integrated_resultant_n": solve["model"]["integrated_resultant_n"],
                    "fixed_reaction_n": solve["sanity"]["fixed_reaction_n"],
                    "interpolation": verification["fea"]["selection"],
                    "runtimes_seconds": {
                        "mesh_generation": mesh_runtime,
                        "solver": solve_runtime,
                        "verification": verification_runtime,
                        "total": time.perf_counter() - level_started,
                    },
                    "gmsh_version": mesh["gmsh_version"],
                    "calculix_version": verification["provenance"]["calculix_version"],
                    "provenance": verification["provenance"],
                    "solver_warnings": warnings,
                }
            )

        results = add_successive_changes(raw_results)
        node_counts = [result["node_count"] for result in results]
        element_counts = [result["volume_element_count"] for result in results]
        if node_counts != sorted(node_counts) or len(set(node_counts)) != len(node_counts):
            raise ConvergenceError(f"Node counts do not strictly increase: {node_counts}")
        if element_counts != sorted(element_counts) or len(set(element_counts)) != len(element_counts):
            raise ConvergenceError(f"Element counts do not strictly increase: {element_counts}")

        study = {
            "status": "convergence study completed",
            "benchmark_id": "cantilever-c3d10-tip-uz-convergence-v1",
            "quantity_of_interest": {
                "description": "global UZ at free-end cross-section centroid",
                "location_m": [1.0, 0.025, 0.025],
            },
            "varied_parameter": "uniform Gmsh characteristic mesh size",
            "fixed_invariants": {
                "geometry_m": {"length": 1.0, "width": 0.05, "height": 0.05},
                "material": {"youngs_modulus_pa": 200.0e9, "poissons_ratio": 0.30},
                "resultant_force_n": [0.0, 0.0, -1000.0],
                "load_distribution": "400000 Pa uniform -Z traction as consistent nodal loads",
                "boundary_condition": "entire x=0 face fixed in UX, UY, UZ",
                "element_type": "C3D10",
                "solver_settings": "linear static",
            },
            "refinement": {
                "factor_between_sizes": math.sqrt(2.0),
                "successive_relative_change_denominator": "absolute previous-mesh centroid UZ",
                "levels": [{"name": name, "mesh_size_m": size} for name, size in LEVELS],
            },
            "acceptance_threshold": None,
            "results": results,
            "total_runtime_seconds": time.perf_counter() - study_started,
        }
        study_path.parent.mkdir(parents=True, exist_ok=True)
        study_path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired, KeyError, ConvergenceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print_table(results)
    print(f"\nArtifact: {study_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
