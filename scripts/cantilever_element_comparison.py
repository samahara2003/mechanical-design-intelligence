"""Compare C3D4 and C3D10 cantilever displacement across fixed mesh sizes."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from cantilever_mesh_convergence import LEVELS, ConvergenceError, add_successive_changes, run_level


FORMULATIONS = ("C3D4", "C3D10")


def group_formulation_results(results: list[dict]) -> dict[str, list[dict]]:
    grouped = {}
    for formulation in FORMULATIONS:
        formulation_results = [
            result for result in results if result["formulation"] == formulation
        ]
        grouped[formulation] = add_successive_changes(formulation_results)
    unknown = {result["formulation"] for result in results} - set(FORMULATIONS)
    if unknown:
        raise ValueError(f"unsupported formulations: {sorted(unknown)}")
    return grouped


def print_table(grouped: dict[str, list[dict]]) -> None:
    print("C3D4/C3D10 displacement-study results (no acceptance threshold)")
    print(
        f"{'type':<7} {'level':<8} {'size (m)':>12} {'nodes':>8} {'elements':>9} "
        f"{'centroid UZ (m)':>18} {'EB diff %':>11} {'change %':>11} {'total s':>9}"
    )
    for formulation in FORMULATIONS:
        for result in grouped[formulation]:
            change = result["percent_change_from_previous"]
            change_text = "-" if change is None else f"{change:.6f}"
            print(
                f"{formulation:<7} {result['level']:<8} {result['mesh_size_m']:>12.8f} "
                f"{result['node_count']:>8d} {result['volume_element_count']:>9d} "
                f"{result['centroid_uz_m']:>18.12g} "
                f"{result['percent_analytical_error']:>11.6f} {change_text:>11} "
                f"{result['runtimes_seconds']['total']:>9.3f}"
            )


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    study_root = repository / "artifacts" / "cantilever" / "element_comparison"
    study_path = repository / "artifacts" / "cantilever" / "cantilever_element_comparison.json"
    raw_results = []
    started = time.perf_counter()
    try:
        for formulation in FORMULATIONS:
            for level, mesh_size_m in LEVELS:
                raw_results.append(
                    run_level(
                        repository,
                        study_root / formulation.lower() / level,
                        level,
                        mesh_size_m,
                        formulation,
                    )
                )
        grouped = group_formulation_results(raw_results)
        for formulation, results in grouped.items():
            nodes = [result["node_count"] for result in results]
            elements = [result["volume_element_count"] for result in results]
            if nodes != sorted(nodes) or len(set(nodes)) != len(nodes):
                raise ConvergenceError(f"{formulation} node counts do not strictly increase: {nodes}")
            if elements != sorted(elements) or len(set(elements)) != len(elements):
                raise ConvergenceError(
                    f"{formulation} element counts do not strictly increase: {elements}"
                )

        study = {
            "status": "element formulation comparison completed",
            "benchmark_id": "cantilever-c3d4-c3d10-tip-uz-v1",
            "independent_variables": [
                "tetrahedral element formulation/order",
                "uniform Gmsh characteristic mesh size",
            ],
            "concepts": {
                "h_refinement": "reducing characteristic element size within a formulation",
                "element_order_increase": "increasing approximation order from C3D4 to C3D10",
                "not_hp_adaptivity": True,
            },
            "fixed_invariants": {
                "geometry_m": {"length": 1.0, "width": 0.05, "height": 0.05},
                "material": {"youngs_modulus_pa": 200.0e9, "poissons_ratio": 0.30},
                "resultant_force_n": [0.0, 0.0, -1000.0],
                "load_distribution": "400000 Pa uniform -Z traction as consistent nodal loads",
                "boundary_condition": "entire x=0 face fixed in UX, UY, UZ",
                "solver_settings": "CalculiX linear static",
                "quantity_of_interest": {
                    "description": "global UZ at free-end cross-section centroid",
                    "location_m": [1.0, 0.025, 0.025],
                },
                "analytical_displacement_magnitude_m": 0.0032,
            },
            "mesh_levels": [{"name": name, "mesh_size_m": size} for name, size in LEVELS],
            "successive_relative_change_denominator": "absolute previous mesh UZ within the same formulation",
            "acceptance_threshold": None,
            "results_by_formulation": grouped,
            "total_runtime_seconds": time.perf_counter() - started,
        }
        study_path.parent.mkdir(parents=True, exist_ok=True)
        study_path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    except (OSError, KeyError, ValueError, ConvergenceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print_table(grouped)
    print(f"\nArtifact: {study_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
