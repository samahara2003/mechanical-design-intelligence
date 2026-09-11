"""Resolve, render, and solve one C3D10 mounting-bracket analysis."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from bracket_definition import (
    BRACKET_FIXED_BOUNDARY,
    BRACKET_FORCE,
    BRACKET_LOAD_FACE,
    BRACKET_MOUNTING_FACES,
    BRACKET_VOLUME,
    bracket_analysis_definition,
)
from calculix_adapter import (
    CalculixAdapterError,
    render_c3d10_linear_static_deck,
    translate_boundary_condition,
    translate_nodal_force_representation,
    validate_c3d10_linear_static_analysis_definition,
)
from calculix_results import CalculixResultParseError, parse_calculix_dat
from engineering_domain import AnalysisDefinition, GeometrySelection
from engineering_postprocessing import vector_magnitude
from generate_bracket_mesh import (
    LOAD_FACE_AREA_M2,
    LOAD_PAD_X_MAX_M,
    MOUNTING_HOLE_CENTRES_M,
    MOUNTING_HOLE_RADIUS_M,
)
from run_axial_bar_solve import verify_export_connectivity
from run_cantilever_solve import SolveError, as_calculix_c3d10, map_surface_faces, read_msh
from surface_load_mapping import map_uniform_force_to_c3d10_faces


PHYSICAL_TAG_BY_SELECTION = {
    BRACKET_VOLUME: 1,
    BRACKET_MOUNTING_FACES: 2,
    BRACKET_LOAD_FACE: 3,
}
COORDINATE_TOLERANCE_M = 2.0e-7


def resolve_mesh_elements(
    selection: GeometrySelection, msh_element_type: int, elements: list[dict]
) -> list[dict]:
    try:
        tag = PHYSICAL_TAG_BY_SELECTION[selection]
    except KeyError as error:
        raise SolveError(f"No bracket mesh resolution exists for {selection}") from error
    resolved = [
        element
        for element in elements
        if element["type"] == msh_element_type and element["physical_tag"] == tag
    ]
    if not resolved:
        raise SolveError(f"Bracket selection {selection.region_name!r} resolved to no entities")
    return resolved


def _on_mounting_bore(point: tuple[float, float, float]) -> bool:
    x, y, _ = point
    return any(
        abs(math.hypot(x - cx, y - cy) - MOUNTING_HOLE_RADIUS_M)
        <= COORDINATE_TOLERANCE_M
        for cx, cy in MOUNTING_HOLE_CENTRES_M
    )


def prepare_model(mesh_path: Path, analysis: AnalysisDefinition) -> tuple[dict, str]:
    validate_c3d10_linear_static_analysis_definition(analysis)
    nodes, elements = read_msh(mesh_path)
    volumes = [
        as_calculix_c3d10(element)
        for element in resolve_mesh_elements(BRACKET_VOLUME, 11, elements)
    ]
    fixed_faces = resolve_mesh_elements(BRACKET_MOUNTING_FACES, 9, elements)
    load_faces = resolve_mesh_elements(BRACKET_LOAD_FACE, 9, elements)
    verify_export_connectivity(mesh_path.with_name("bracket_mesh.inp"), volumes)
    fixed_nodes = sorted({node for face in fixed_faces for node in face["nodes"]})
    load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
    if any(not _on_mounting_bore(nodes[node]) for node in fixed_nodes):
        raise SolveError("Fixed selection contains a node away from the two mounting bores")
    if any(abs(nodes[node][0] - LOAD_PAD_X_MAX_M) > COORDINATE_TOLERANCE_M for node in load_nodes):
        raise SolveError("Load selection contains a node away from the load-pad end face")
    mapped_surface = map_surface_faces(load_faces, volumes, "C3D10")
    nodal_forces, area, traction = map_uniform_force_to_c3d10_faces(
        analysis.loads[0], load_faces, nodes, LOAD_FACE_AREA_M2
    )
    resultant = tuple(sum(vector[axis] for vector in nodal_forces.values()) for axis in range(3))
    if not math.isclose(area, LOAD_FACE_AREA_M2, rel_tol=1.0e-9, abs_tol=1.0e-12):
        raise SolveError(f"Integrated load-pad area {area} differs from {LOAD_FACE_AREA_M2}")
    if any(
        not math.isclose(actual, expected, rel_tol=1.0e-12, abs_tol=1.0e-8)
        for actual, expected in zip(resultant, BRACKET_FORCE.vector_n)
    ):
        raise SolveError(f"Integrated bracket force {resultant} differs from domain intent")
    boundary = translate_boundary_condition(
        BRACKET_FIXED_BOUNDARY, BRACKET_MOUNTING_FACES, fixed_nodes, "FIXED"
    )
    loads = translate_nodal_force_representation(
        BRACKET_FORCE, BRACKET_LOAD_FACE, nodal_forces
    )
    deck = render_c3d10_linear_static_deck(
        analysis,
        nodes,
        volumes,
        boundary,
        load_nodes,
        mapped_surface,
        loads,
        heading="Mechanical Design Intelligence - mounting bracket integration case",
        volume_set_name="BRACKET",
        load_node_set_name="LOAD_PAD_NODES",
        load_face_set_prefix="LOAD_PAD_",
        load_surface_name="LOAD_PAD_FACE",
    )
    return {
        "node_count": len(nodes),
        "volume_element_count": len(volumes),
        "fixed_face_element_count": len(fixed_faces),
        "fixed_node_count": len(fixed_nodes),
        "load_face_element_count": len(load_faces),
        "load_node_count": len(load_nodes),
        "load_face_area_m2": area,
        "traction_pa": list(traction),
        "integrated_resultant_n": list(resultant),
        "load_representation": "uniform vector traction integrated consistently over C3D10 faces",
        "connectivity_verified_against_gmsh_export": True,
    }, deck


def inspect_result(dat_path: Path, nodes: dict[int, tuple[float, float, float]]) -> dict:
    result = parse_calculix_dat(dat_path)
    maximum = max(result.displacements, key=lambda value: vector_magnitude(value.displacement_m))
    fixed_ids = {reaction.node_id for reaction in result.reactions}
    fixed_displacements = [
        value for value in result.displacements if value.node_id in fixed_ids
    ]
    if result.reaction_resultant_n is None:
        raise SolveError("CalculiX DAT contains no support reaction resultant")
    reaction = result.reaction_resultant_n.as_tuple()
    return {
        "maximum_displacement_node": maximum.node_id,
        "maximum_displacement_m": vector_magnitude(maximum.displacement_m),
        "maximum_displacement_vector_m": list(maximum.displacement_m.as_tuple()),
        "maximum_displacement_location_m": list(nodes[maximum.node_id]),
        "maximum_moves_with_positive_x_load": maximum.displacement_m.x > 0.0,
        "maximum_fixed_node_displacement_m": max(
            (vector_magnitude(value.displacement_m) for value in fixed_displacements), default=0.0
        ),
        "fixed_reaction_n": list(reaction),
        "reaction_balances_applied_load": all(
            math.isclose(reaction[i], -BRACKET_FORCE.vector_n[i], rel_tol=1.0e-8, abs_tol=2.0e-5)
            for i in range(3)
        ),
        "displacement_count": len(result.displacements),
        "reaction_count": len(result.reactions),
        "integration_point_stress_count": len(result.integration_point_stresses),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    output_dir = parse_args().output_dir.resolve()
    mesh_path = output_dir / "bracket.msh"
    ccx = shutil.which("ccx")
    if ccx is None or not mesh_path.is_file():
        print("error: ccx or bracket mesh is unavailable", file=sys.stderr)
        return 1
    try:
        mesh_summary = json.loads((output_dir / "bracket_mesh_summary.json").read_text(encoding="utf-8"))
        version_output = subprocess.run([ccx, "-v"], capture_output=True, text=True, timeout=10)
        version_match = re.search(r"This is Version\s+([\d.]+)", version_output.stdout + version_output.stderr)
        if version_match is None:
            raise SolveError("Unable to identify CalculiX version")
        analysis = bracket_analysis_definition(
            mesh_summary["gmsh_version"], version_match.group(1), mesh_summary["mesh_size_m"]
        )
        nodes, _ = read_msh(mesh_path)
        model, deck = prepare_model(mesh_path, analysis)
        job_name = "bracket_static"
        for old in output_dir.glob(f"{job_name}.*"):
            old.unlink()
        deck_path = output_dir / f"{job_name}.inp"
        deck_path.write_text(deck, encoding="ascii")
        completed = subprocess.run(
            [ccx, "-i", job_name], cwd=output_dir, capture_output=True, text=True, timeout=180
        )
        stdout_path = output_dir / f"{job_name}.stdout.txt"
        stderr_path = output_dir / f"{job_name}.stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        dat_path = output_dir / f"{job_name}.dat"
        frd_path = output_dir / f"{job_name}.frd"
        if any(not path.is_file() or path.stat().st_size == 0 for path in (dat_path, frd_path)):
            raise SolveError("CalculiX did not create nonempty DAT and FRD results")
        sanity = inspect_result(dat_path, nodes)
        sanity["solver_completed"] = "JOB FINISHED" in completed.stdout.upper()
        sanity["solver_warnings"] = [
            line.strip() for line in completed.stdout.splitlines() if "warning" in line.lower()
        ]
        if not sanity["solver_completed"] or not sanity["reaction_balances_applied_load"]:
            raise SolveError(f"Bracket solve sanity failed: {sanity}")
    except (OSError, ValueError, subprocess.TimeoutExpired, SolveError, CalculixAdapterError, CalculixResultParseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    summary = {
        "status": "ok", "ccx_version": version_match.group(1), "ccx_exit_code": completed.returncode,
        "model": model, "sanity": sanity,
        "artifacts": {"input": str(deck_path), "dat": str(dat_path), "frd": str(frd_path), "stdout": str(stdout_path), "stderr": str(stderr_path)},
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
