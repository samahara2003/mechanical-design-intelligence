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

from cantilever_verification import VerificationError, read_displacements
from run_cantilever_solve import (
    SolveError,
    as_calculix_c3d10,
    map_surface_faces,
    read_msh,
    triangle_area,
    wrapped_ids,
)


YOUNGS_MODULUS_PA = 200.0e9
POISSONS_RATIO = 0.30
AREA_M2 = 0.0025
RESULTANT_FORCE_N = 1000.0
TRACTION_PA = RESULTANT_FORCE_N / AREA_M2
COORDINATE_TOLERANCE_M = 1.0e-8


def consistent_quadratic_face_loads(
    faces: list[dict],
    nodes: dict[int, tuple[float, float, float]],
    traction_pa: tuple[float, float, float],
) -> tuple[dict[int, tuple[float, float, float]], float]:
    nodal: dict[int, list[float]] = {}
    total_area = 0.0
    for face in faces:
        if len(face["nodes"]) != 6:
            raise SolveError("Axial load face must use six-node quadratic triangles")
        area = triangle_area(*(nodes[node] for node in face["nodes"][:3]))
        total_area += area
        for node in face["nodes"][3:]:
            vector = nodal.setdefault(node, [0.0, 0.0, 0.0])
            for axis in range(3):
                vector[axis] += traction_pa[axis] * area / 3.0
    return {node: tuple(vector) for node, vector in nodal.items()}, total_area


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


def prepare_model(mesh_path: Path) -> tuple[dict, str]:
    nodes, elements = read_msh(mesh_path)
    volumes = [
        as_calculix_c3d10(element)
        for element in elements
        if element["type"] == 11 and element["physical_tag"] == 1
    ]
    fixed_faces = [
        element for element in elements if element["type"] == 9 and element["physical_tag"] == 2
    ]
    load_faces = [
        element for element in elements if element["type"] == 9 and element["physical_tag"] == 3
    ]
    if not volumes or not fixed_faces or not load_faces:
        raise SolveError("Axial-bar volume, fixed, and axial-load groups must be nonempty")
    verify_export_connectivity(mesh_path.with_name("axial_bar_mesh.inp"), volumes)
    fixed_nodes = sorted({node for face in fixed_faces for node in face["nodes"]})
    load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
    if any(abs(nodes[node][0]) > COORDINATE_TOLERANCE_M for node in fixed_nodes):
        raise SolveError("Fixed node set contains a node away from x=0")
    if any(abs(nodes[node][0] - 1.0) > COORDINATE_TOLERANCE_M for node in load_nodes):
        raise SolveError("Axial-load node set contains a node away from x=1")
    mapped_surface = map_surface_faces(load_faces, volumes, "C3D10")
    nodal_loads, area = consistent_quadratic_face_loads(
        load_faces, nodes, (TRACTION_PA, 0.0, 0.0)
    )
    resultant = tuple(sum(vector[axis] for vector in nodal_loads.values()) for axis in range(3))
    if not math.isclose(area, AREA_M2, rel_tol=1.0e-10, abs_tol=1.0e-12):
        raise SolveError(f"Integrated axial-load area is {area}, expected {AREA_M2}")
    if not all(
        math.isclose(resultant[axis], (RESULTANT_FORCE_N, 0.0, 0.0)[axis], abs_tol=1.0e-8)
        for axis in range(3)
    ):
        raise SolveError(f"Integrated load is {resultant}, expected (1000, 0, 0) N")

    lines = [
        "*HEADING",
        "Mechanical Design Intelligence - axial bar C3D10 verification",
        "*NODE, NSET=ALLNODES",
    ]
    lines.extend(
        f"{node}, {x:.16g}, {y:.16g}, {z:.16g}"
        for node, (x, y, z) in sorted(nodes.items())
    )
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=AXIAL_BAR")
    lines.extend(f"{element['id']}, " + ", ".join(map(str, element["nodes"])) for element in volumes)
    lines.append("*NSET, NSET=FIXED")
    lines.extend(wrapped_ids(fixed_nodes))
    lines.append("*NSET, NSET=AXIAL_LOAD_NODES")
    lines.extend(wrapped_ids(load_nodes))
    face_sets: dict[str, list[int]] = {}
    for element_id, face_label in mapped_surface:
        face_sets.setdefault(face_label, []).append(element_id)
    for face_label, element_ids in sorted(face_sets.items()):
        lines.append(f"*ELSET, ELSET=AXIAL_LOAD_{face_label}")
        lines.extend(wrapped_ids(sorted(element_ids)))
    lines.append("*SURFACE, NAME=AXIAL_LOAD_FACE, TYPE=ELEMENT")
    lines.extend(f"AXIAL_LOAD_{label}, {label}" for label in sorted(face_sets))
    lines.extend(
        [
            "*MATERIAL, NAME=STEEL",
            "*ELASTIC",
            f"{YOUNGS_MODULUS_PA:.16g}, {POISSONS_RATIO}",
            "*SOLID SECTION, ELSET=AXIAL_BAR, MATERIAL=STEEL",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            "FIXED, 1, 3, 0",
            "*CLOAD",
        ]
    )
    for node, vector in sorted(nodal_loads.items()):
        for degree, value in enumerate(vector, start=1):
            if value != 0.0:
                lines.append(f"{node}, {degree}, {value:.16g}")
    lines.extend(
        [
            "*NODE FILE",
            "U, RF",
            "*EL FILE",
            "S",
            "*EL PRINT, ELSET=AXIAL_BAR",
            "S",
            "*NODE PRINT, NSET=ALLNODES",
            "U",
            "*NODE PRINT, NSET=FIXED, TOTALS=YES",
            "RF",
            "*END STEP",
            "",
        ]
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
        "traction_pa": [TRACTION_PA, 0.0, 0.0],
        "integrated_resultant_n": list(resultant),
        "calculix_load_representation": "consistent C3D10-face nodal loads in global +X",
        "connectivity_verified_against_gmsh_export": True,
    }
    return model, "\n".join(lines)


def inspect_dat(path: Path, nodes: dict[int, tuple[float, float, float]]) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    displacements = read_displacements(path)
    maximum_node, maximum_vector = max(
        displacements.items(), key=lambda item: math.sqrt(sum(value**2 for value in item[1]))
    )
    reaction_match = re.search(
        r"total force \(fx,fy,fz\) for set FIXED.*?\n\s*"
        r"([-+\d.Ee]+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)",
        text,
        re.IGNORECASE,
    )
    if reaction_match is None:
        raise SolveError("No total fixed-support reaction found in DAT")
    reaction = tuple(float(value) for value in reaction_match.groups())
    return {
        "maximum_displacement_node": maximum_node,
        "maximum_displacement_vector_m": list(maximum_vector),
        "maximum_displacement_m": math.sqrt(sum(value**2 for value in maximum_vector)),
        "maximum_displacement_node_x_m": nodes[maximum_node][0],
        "maximum_at_loaded_end": math.isclose(
            nodes[maximum_node][0], 1.0, abs_tol=COORDINATE_TOLERANCE_M
        ),
        "direction_consistent_with_positive_x_load": maximum_vector[0] > 0.0,
        "fixed_reaction_n": list(reaction),
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
        nodes, _ = read_msh(mesh_path)
        model, deck = prepare_model(mesh_path)
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
        sanity["integration_point_stress_output_present"] = "stresses (elem, integ.pnt." in dat_path.read_text(
            encoding="utf-8", errors="replace"
        )
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
    except (OSError, subprocess.TimeoutExpired, SolveError, ValueError, VerificationError) as error:
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
