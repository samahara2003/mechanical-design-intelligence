"""Controlled reusable execution boundary for the known mounting-bracket case."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from analysis_provenance import AnalysisProvenance, build_analysis_provenance
from analysis_results import AnalysisResult, ResolvedAnalysisContext, build_analysis_result
from bracket_definition import (
    BASELINE_MESH_SIZE_M,
    BRACKET_FIXED_BOUNDARY,
    BRACKET_FORCE,
    BRACKET_MATERIAL,
)
from bracket_verification import GAUSS_NATURAL_COORDINATES, interpolate_coordinates
from calculix_results import parse_calculix_dat
from engineering_domain import AnalysisDefinition, MeshElementType
from numerical_results import Vector3
from run_bracket_solve import execute_bracket_solve
from run_cantilever_solve import as_calculix_c3d10, read_msh


class ControlledBracketError(RuntimeError):
    """Raised when input leaves the deliberately narrow V0 bracket contract."""


@dataclass(frozen=True)
class BracketExecution:
    result: AnalysisResult
    provenance: AnalysisProvenance
    generated_artifacts: dict[str, Path]
    mesh_summary: dict
    solve_summary: dict


def validate_controlled_bracket_definition(definition: AnalysisDefinition) -> None:
    """Reject arbitrary CAD/physics while V0 supports only the proven bracket."""
    if definition.material != BRACKET_MATERIAL:
        raise ControlledBracketError("worker V0 supports only the bracket material snapshot")
    if definition.loads != (BRACKET_FORCE,):
        raise ControlledBracketError("worker V0 supports only the bracket load intent")
    if definition.boundary_conditions != (BRACKET_FIXED_BOUNDARY,):
        raise ControlledBracketError("worker V0 supports only the bracket boundary condition")
    if (
        definition.mesh.element_type is not MeshElementType.C3D10
        or definition.mesh.characteristic_size_m != BASELINE_MESH_SIZE_M
        or definition.mesh.mesher_identifier != "Gmsh/OpenCASCADE"
    ):
        raise ControlledBracketError("worker V0 supports only the baseline C3D10 bracket mesh")
    if (
        definition.solver.solver_identifier != "CalculiX"
        or definition.required_factor_of_safety is not None
    ):
        raise ControlledBracketError("worker V0 supports only the verified CalculiX bracket solve")


def execute_controlled_bracket(
    repository: Path,
    step_path: Path,
    output_dir: Path,
    definition: AnalysisDefinition,
) -> BracketExecution:
    """Run uploaded STEP through the existing bracket Engineering Core path."""
    validate_controlled_bracket_definition(definition)
    output_dir.mkdir(parents=True, exist_ok=True)
    mesh_command = [
        sys.executable,
        str(repository / "scripts" / "generate_bracket_mesh.py"),
        "--output-dir",
        str(output_dir),
        "--step-path",
        str(step_path),
        "--mesh-size",
        str(definition.mesh.characteristic_size_m),
    ]
    completed = subprocess.run(
        mesh_command, cwd=repository, capture_output=True, text=True, timeout=180
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise ControlledBracketError(
            f"Gmsh bracket preparation failed: {(detail[-1] if detail else 'no detail')[:500]}"
        )
    mesh_summary = json.loads(
        (output_dir / "bracket_mesh_summary.json").read_text(encoding="utf-8")
    )
    if mesh_summary["gmsh_warnings"]:
        raise ControlledBracketError("Gmsh emitted warnings for the controlled bracket")
    solve_summary = execute_bracket_solve(output_dir, definition)
    if solve_summary["sanity"]["solver_warnings"]:
        raise ControlledBracketError("CalculiX emitted warnings for the controlled bracket")

    mesh_path = output_dir / "bracket.msh"
    dat_path = output_dir / "bracket_static.dat"
    nodes, elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in elements
        if element["type"] == 11 and element["physical_tag"] == 1
    }
    numerical = parse_calculix_dat(dat_path)
    ip_locations = {}
    for stress in numerical.integration_point_stresses:
        element = volumes.get(stress.element_id)
        if element is None or stress.integration_point not in range(1, 5):
            raise ControlledBracketError("raw stress cannot be mapped to a C3D10 point")
        ip_locations[(stress.element_id, stress.integration_point)] = Vector3(
            *interpolate_coordinates(
                element["nodes"],
                nodes,
                GAUSS_NATURAL_COORDINATES[stress.integration_point - 1],
            )
        )
    result = build_analysis_result(
        definition,
        numerical,
        ResolvedAnalysisContext(
            mesh_summary["mesh"]["node_count"],
            mesh_summary["mesh"]["volume_element_count"],
            Vector3(*solve_summary["model"]["integrated_resultant_n"]),
        ),
        node_locations_m={node: Vector3(*point) for node, point in nodes.items()},
        integration_point_locations_m=ip_locations,
    )
    provenance = build_analysis_provenance(
        definition,
        cad_step_path=step_path,
        mesh_path=mesh_path,
        solver_input_path=output_dir / "bracket_static.inp",
        solver_dat_path=dat_path,
        solver_frd_path=output_dir / "bracket_static.frd",
        gmsh_version=mesh_summary["gmsh_version"],
        calculix_version=solve_summary["ccx_version"],
    )
    return BracketExecution(
        result=result,
        provenance=provenance,
        generated_artifacts={
            "mesh": mesh_path,
            "solver_input": output_dir / "bracket_static.inp",
            "solver_dat": dat_path,
            "solver_frd": output_dir / "bracket_static.frd",
        },
        mesh_summary=mesh_summary,
        solve_summary=solve_summary,
    )
