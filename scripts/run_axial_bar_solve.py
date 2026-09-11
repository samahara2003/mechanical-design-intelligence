"""Build and run the C3D10 axial-bar linear-static CalculiX model."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from axial_bar_definition import (
    AXIAL_BAR_VOLUME,
    AXIAL_FIXED_FACE,
    AXIAL_FORCE,
    AXIAL_LOAD_FACE,
    axial_bar_analysis_definition,
)
from calculix_adapter import (
    CalculixAdapterError,
    render_axial_linear_static_deck,
    translate_boundary_condition,
    translate_nodal_force_representation,
    validate_axial_analysis_definition,
)
from calculix_results import CalculixResultParseError, parse_calculix_dat
from engineering_postprocessing import vector_magnitude
from engineering_domain import AnalysisDefinition, GeometrySelection
from run_cantilever_solve import (
    SolveError,
    as_calculix_c3d10,
    map_surface_faces,
    read_msh,
)
from surface_load_mapping import (
    consistent_quadratic_face_loads,
    map_uniform_force_to_c3d10_faces,
)


AREA_M2 = 0.0025
RESULTANT_FORCE_N = AXIAL_FORCE.magnitude_n
COORDINATE_TOLERANCE_M = 1.0e-8
AXIAL_PHYSICAL_TAG_BY_SELECTION = {
    AXIAL_BAR_VOLUME: 1,
    AXIAL_FIXED_FACE: 2,
    AXIAL_LOAD_FACE: 3,
}


def resolve_mesh_elements(
    selection: GeometrySelection,
    msh_element_type: int,
    elements: list[dict],
) -> list[dict]:
    """Resolve an axial named geometry selection through its Gmsh physical tag."""
    try:
        physical_tag = AXIAL_PHYSICAL_TAG_BY_SELECTION[selection]
    except KeyError as error:
        raise SolveError(f"No axial mesh resolution exists for {selection}") from error
    resolved = [
        element
        for element in elements
        if element["type"] == msh_element_type and element["physical_tag"] == physical_tag
    ]
    if not resolved:
        raise SolveError(f"Geometry selection {selection.region_name!r} resolved to no mesh entities")
    return resolved


def verify_export_connectivity(path: Path, volumes: list[dict]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(
            index
            for index, line in enumerate(lines)
            if line.replace(" ", "").upper().startswith("*ELEMENT,TYPE=C3D10")
        ) + 1
    except StopIteration as error:
        raise SolveError(f"Gmsh CalculiX export contains no C3D10 block: {path}") from error
    exported = {}
    for line in lines[start:]:
        if line.startswith("*"):
            break
        fields = [int(value.strip()) for value in line.split(",") if value.strip()]
        if fields:
            exported[fields[0]] = fields[1:]
    expected = {element["id"]: element["nodes"] for element in volumes}
    if exported != expected:
        raise SolveError("Converted C3D10 connectivity does not match Gmsh's CalculiX export")


def prepare_model(mesh_path: Path, analysis: AnalysisDefinition) -> tuple[dict, str]:
    validate_axial_analysis_definition(analysis)
    nodes, elements = read_msh(mesh_path)
    volumes = [
        as_calculix_c3d10(element)
        for element in resolve_mesh_elements(AXIAL_BAR_VOLUME, 11, elements)
    ]
    fixed_faces = resolve_mesh_elements(AXIAL_FIXED_FACE, 9, elements)
    load_faces = resolve_mesh_elements(AXIAL_LOAD_FACE, 9, elements)
    verify_export_connectivity(mesh_path.with_name("axial_bar_mesh.inp"), volumes)
    fixed_nodes = sorted({node for face in fixed_faces for node in face["nodes"]})
    load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
    if any(abs(nodes[node][0]) > COORDINATE_TOLERANCE_M for node in fixed_nodes):
        raise SolveError("Fixed node set contains a node away from x=0")
    if any(abs(nodes[node][0] - 1.0) > COORDINATE_TOLERANCE_M for node in load_nodes):
        raise SolveError("Axial-load node set contains a node away from x=1")
    mapped_surface = map_surface_faces(load_faces, volumes, "C3D10")
    nodal_loads, area, traction = map_uniform_force_to_c3d10_faces(
        analysis.loads[0],
        load_faces,
        nodes,
        AREA_M2,
    )
    resultant = tuple(sum(vector[axis] for vector in nodal_loads.values()) for axis in range(3))
    if not math.isclose(area, AREA_M2, rel_tol=1.0e-10, abs_tol=1.0e-12):
        raise SolveError(f"Integrated axial-load area is {area}, expected {AREA_M2}")
    if not all(
        math.isclose(resultant[axis], (RESULTANT_FORCE_N, 0.0, 0.0)[axis], abs_tol=1.0e-8)
        for axis in range(3)
    ):
        raise SolveError(f"Integrated load is {resultant}, expected (1000, 0, 0) N")

    boundary = translate_boundary_condition(
        analysis.boundary_conditions[0],
        AXIAL_FIXED_FACE,
        fixed_nodes,
        "FIXED",
    )
    concentrated_loads = translate_nodal_force_representation(
        analysis.loads[0], AXIAL_LOAD_FACE, nodal_loads
    )
    deck = render_axial_linear_static_deck(
        analysis,
        nodes,
        volumes,
        boundary,
        load_nodes,
        mapped_surface,
        concentrated_loads,
    )
    model = {
        "node_count": len(nodes),
        "volume_element_type": "C3D10",
        "volume_element_count": len(volumes),
        "fixed_face_element_count": len(fixed_faces),
        "fixed_node_count": len(fixed_nodes),
        "load_face_element_count": len(load_faces),
        "load_node_count": len(load_nodes),
        "load_face_area_m2": area,
        "traction_pa": list(traction),
        "integrated_resultant_n": list(resultant),
        "calculix_load_representation": "consistent C3D10-face nodal loads in global +X",
        "connectivity_verified_against_gmsh_export": True,
    }
    return model, deck


def inspect_dat(path: Path, nodes: dict[int, tuple[float, float, float]]) -> dict:
    numerical_result = parse_calculix_dat(path)
    maximum_displacement = max(
        numerical_result.displacements,
        key=lambda item: vector_magnitude(item.displacement_m),
    )
    maximum_node = maximum_displacement.node_id
    maximum_vector = maximum_displacement.displacement_m.as_tuple()
    if numerical_result.reaction_resultant_n is None:
        raise SolveError("No total fixed-support reaction found in DAT")
    reaction = numerical_result.reaction_resultant_n.as_tuple()
    return {
        "maximum_displacement_node": maximum_node,
        "maximum_displacement_vector_m": list(maximum_vector),
        "maximum_displacement_m": vector_magnitude(maximum_displacement.displacement_m),
        "maximum_displacement_node_x_m": nodes[maximum_node][0],
        "maximum_at_loaded_end": math.isclose(
            nodes[maximum_node][0], 1.0, abs_tol=COORDINATE_TOLERANCE_M
        ),
        "direction_consistent_with_positive_x_load": maximum_vector[0] > 0.0,
        "fixed_reaction_n": list(reaction),
        "nodal_reaction_count": len(numerical_result.reactions),
        "integration_point_stress_count": len(numerical_result.integration_point_stresses),
        "integration_point_stress_output_present": bool(
            numerical_result.integration_point_stresses
        ),
        "reaction_balances_applied_load": math.isclose(
            reaction[0], -RESULTANT_FORCE_N, rel_tol=1.0e-8, abs_tol=1.0e-5
        )
        and abs(reaction[1]) <= 1.0e-5
        and abs(reaction[2]) <= 1.0e-5,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/axial_bar"),
        help="directory containing axial_bar.msh (default: artifacts/axial_bar)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    mesh_path = output_dir / "axial_bar.msh"
    if not mesh_path.is_file():
        print(f"error: mesh not found; run generate_axial_bar_mesh.py first: {mesh_path}", file=sys.stderr)
        return 1
    ccx = shutil.which("ccx")
    if ccx is None:
        print("error: ccx was not found on PATH", file=sys.stderr)
        return 1
    job_name = "axial_bar_static"
    for path in output_dir.glob(f"{job_name}.*"):
        path.unlink()
    try:
        mesh_summary_path = output_dir / "axial_bar_mesh_summary.json"
        if not mesh_summary_path.is_file():
            raise SolveError(f"Mesh summary is missing: {mesh_summary_path}")
        mesh_summary = json.loads(mesh_summary_path.read_text(encoding="utf-8"))
        version_result = subprocess.run(
            [ccx, "-v"], capture_output=True, check=False, text=True, timeout=10
        )
        version_output = "\n".join((version_result.stdout, version_result.stderr))
        version_match = re.search(r"This is Version\s+([\d.]+)", version_output)
        if version_match is None:
            raise SolveError("Unable to identify the CalculiX version")
        analysis = axial_bar_analysis_definition(
            mesh_summary["gmsh_version"], version_match.group(1)
        )
        nodes, _ = read_msh(mesh_path)
        model, deck = prepare_model(mesh_path, analysis)
        deck_path = output_dir / f"{job_name}.inp"
        deck_path.write_text(deck, encoding="ascii")
        result = subprocess.run(
            [ccx, "-i", job_name],
            cwd=output_dir,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        stdout_path = output_dir / f"{job_name}.stdout.txt"
        stderr_path = output_dir / f"{job_name}.stderr.txt"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        dat_path = output_dir / f"{job_name}.dat"
        frd_path = output_dir / f"{job_name}.frd"
        sta_path = output_dir / f"{job_name}.sta"
        for required in (dat_path, frd_path, sta_path):
            if not required.is_file() or required.stat().st_size == 0:
                raise SolveError(f"Expected solver artifact is missing or empty: {required}")
        sanity = inspect_dat(dat_path, nodes)
        sanity["solver_completed"] = "JOB FINISHED" in result.stdout.upper()
        sanity["solver_warnings"] = [
            line.strip() for line in result.stdout.splitlines() if "warning" in line.lower()
        ]
        if not all(
            sanity[key]
            for key in (
                "maximum_at_loaded_end",
                "direction_consistent_with_positive_x_load",
                "reaction_balances_applied_load",
                "integration_point_stress_output_present",
                "solver_completed",
            )
        ):
            raise SolveError(f"Axial-bar solve sanity failed: {sanity}")
    except (
        OSError,
        subprocess.TimeoutExpired,
        CalculixAdapterError,
        CalculixResultParseError,
        SolveError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = {
        "status": "ok",
        "ccx_executable": ccx,
        "ccx_exit_code": result.returncode,
        "model": model,
        "sanity": sanity,
        "artifacts": {
            "input": str(deck_path),
            "dat": str(dat_path),
            "frd": str(frd_path),
            "sta": str(sta_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
