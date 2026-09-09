"""Build and run the first CalculiX cantilever sanity solve."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path


YOUNGS_MODULUS_PA = 200.0e9
POISSONS_RATIO = 0.30
RESULTANT_FORCE_N = 1000.0
TRACTION_PA = 400000.0
AREA_M2 = 0.0025
COORDINATE_TOLERANCE_M = 1.0e-8

# CalculiX face labels and corresponding C3D10 local node indices. The first
# three nodes in each tuple are the corner nodes; the final three are midside.
C3D10_FACES = {
    "S1": (0, 1, 2, 4, 5, 6),
    "S2": (0, 3, 1, 7, 8, 4),
    "S3": (1, 3, 2, 8, 9, 5),
    "S4": (2, 3, 0, 9, 7, 6),
}


def as_calculix_c3d10(element: dict) -> dict:
    """Convert Gmsh type-11 edge-node ordering to CalculiX C3D10 ordering."""
    connectivity = element["nodes"]
    if len(connectivity) != 10:
        raise SolveError(f"Element {element['id']} does not have 10-node connectivity")
    return {**element, "nodes": connectivity[:8] + [connectivity[9], connectivity[8]]}


class SolveError(RuntimeError):
    """Raised when solve preparation or execution is not trustworthy."""


def read_msh(path: Path) -> tuple[dict[int, tuple[float, float, float]], list[dict]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        node_start = lines.index("$Nodes")
        element_start = lines.index("$Elements")
    except ValueError as error:
        raise SolveError(f"Expected ASCII MSH 2.2 sections were not found in {path}") from error

    node_count = int(lines[node_start + 1])
    nodes = {}
    for line in lines[node_start + 2 : node_start + 2 + node_count]:
        node_id, x, y, z = line.split()
        nodes[int(node_id)] = (float(x), float(y), float(z))

    element_count = int(lines[element_start + 1])
    elements = []
    for line in lines[element_start + 2 : element_start + 2 + element_count]:
        fields = [int(value) for value in line.split()]
        tag_count = fields[2]
        elements.append(
            {
                "id": fields[0],
                "type": fields[1],
                "physical_tag": fields[3] if tag_count else None,
                "nodes": fields[3 + tag_count :],
            }
        )
    return nodes, elements


def triangle_area(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> float:
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * math.sqrt(sum(component * component for component in cross))


def map_surface_faces(
    surface_elements: list[dict], volume_elements: list[dict]
) -> list[tuple[int, str]]:
    candidates: dict[frozenset[int], tuple[int, str, frozenset[int]]] = {}
    for element in volume_elements:
        connectivity = element["nodes"]
        for label, indices in C3D10_FACES.items():
            face_nodes = frozenset(connectivity[index] for index in indices)
            corner_nodes = frozenset(connectivity[index] for index in indices[:3])
            candidates[corner_nodes] = (element["id"], label, face_nodes)

    mapped = []
    for surface in surface_elements:
        corner_nodes = frozenset(surface["nodes"][:3])
        match = candidates.get(corner_nodes)
        if match is None:
            raise SolveError(f"Surface element {surface['id']} has no C3D10 parent face")
        volume_id, face_label, expected_nodes = match
        if frozenset(surface["nodes"]) != expected_nodes:
            raise SolveError(
                f"Surface element {surface['id']} connectivity does not match "
                f"C3D10 element {volume_id} {face_label}"
            )
        mapped.append((volume_id, face_label))
    if len(set(mapped)) != len(mapped):
        raise SolveError("A load surface was mapped to a volume face more than once")
    return mapped


def wrapped_ids(values: list[int], width: int = 16) -> list[str]:
    return [", ".join(str(value) for value in values[index : index + width]) for index in range(0, len(values), width)]


def prepare_model(mesh_path: Path) -> tuple[dict, str]:
    nodes, elements = read_msh(mesh_path)
    volumes = [
        as_calculix_c3d10(item)
        for item in elements
        if item["type"] == 11 and item["physical_tag"] == 1
    ]
    fixed_faces = [item for item in elements if item["type"] == 9 and item["physical_tag"] == 2]
    load_faces = [item for item in elements if item["type"] == 9 and item["physical_tag"] == 3]
    if not volumes or not fixed_faces or not load_faces:
        raise SolveError("The beam, fixed, and load C3D10 mesh groups must all be nonempty")
    if any(len(element["nodes"]) != 10 for element in volumes):
        raise SolveError("A beam volume element does not have C3D10 connectivity")
    if any(len(element["nodes"]) != 6 for element in fixed_faces + load_faces):
        raise SolveError("An end-face element does not have six-node triangle connectivity")

    fixed_nodes = sorted({node for face in fixed_faces for node in face["nodes"]})
    load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
    if any(abs(nodes[node][0]) > COORDINATE_TOLERANCE_M for node in fixed_nodes):
        raise SolveError("The fixed node set contains a node away from x=0")
    if any(abs(nodes[node][0] - 1.0) > COORDINATE_TOLERANCE_M for node in load_nodes):
        raise SolveError("The load node set contains a node away from x=1 m")

    load_surface = map_surface_faces(load_faces, volumes)
    nodal_forces: dict[int, float] = {}
    integrated_area = 0.0
    for face in load_faces:
        area = triangle_area(*(nodes[node] for node in face["nodes"][:3]))
        integrated_area += area
        # For a six-node quadratic triangle under constant traction, the
        # consistent corner-node integrals are zero and each midside integral
        # is one third of the triangle area.
        for node in face["nodes"][3:]:
            nodal_forces[node] = nodal_forces.get(node, 0.0) - TRACTION_PA * area / 3.0

    resultant_z = sum(nodal_forces.values())
    if not math.isclose(integrated_area, AREA_M2, rel_tol=1.0e-10, abs_tol=1.0e-12):
        raise SolveError(f"Integrated load-face area is {integrated_area}, expected {AREA_M2}")
    if not math.isclose(resultant_z, -RESULTANT_FORCE_N, rel_tol=1.0e-10, abs_tol=1.0e-8):
        raise SolveError(f"Integrated Z load is {resultant_z} N, expected -1000 N")

    lines = [
        "*HEADING",
        "Mechanical Design Intelligence - cantilever C3D10 sanity solve",
        "*NODE, NSET=ALLNODES",
    ]
    lines.extend(f"{node}, {x:.16g}, {y:.16g}, {z:.16g}" for node, (x, y, z) in sorted(nodes.items()))
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=BEAM")
    lines.extend(f"{item['id']}, " + ", ".join(map(str, item["nodes"])) for item in volumes)
    lines.append("*NSET, NSET=FIXED")
    lines.extend(wrapped_ids(fixed_nodes))
    lines.append("*NSET, NSET=LOAD_NODES")
    lines.extend(wrapped_ids(load_nodes))
    load_face_sets: dict[str, list[int]] = {}
    for element_id, face_label in load_surface:
        load_face_sets.setdefault(face_label, []).append(element_id)
    for face_label, element_ids in sorted(load_face_sets.items()):
        lines.append(f"*ELSET, ELSET=LOAD_{face_label}")
        lines.extend(wrapped_ids(sorted(element_ids)))
    lines.append("*SURFACE, NAME=LOAD_FACE, TYPE=ELEMENT")
    lines.extend(f"LOAD_{face_label}, {face_label}" for face_label in sorted(load_face_sets))
    lines.extend(
        [
            "*MATERIAL, NAME=STEEL",
            "*ELASTIC",
            f"{YOUNGS_MODULUS_PA:.16g}, {POISSONS_RATIO}",
            "*SOLID SECTION, ELSET=BEAM, MATERIAL=STEEL",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            "FIXED, 1, 3, 0",
            "*CLOAD",
        ]
    )
    lines.extend(f"{node}, 3, {force:.16g}" for node, force in sorted(nodal_forces.items()))
    lines.extend(
        [
            "*NODE FILE",
            "U, RF",
            "*EL FILE",
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
        "load_face_area_m2": integrated_area,
        "traction_pa": [0.0, 0.0, -TRACTION_PA],
        "integrated_resultant_n": [0.0, 0.0, resultant_z],
        "calculix_load_representation": "consistent C3D10-face nodal loads",
    }
    return model, "\n".join(lines)


def inspect_dat(path: Path, nodes: dict[int, tuple[float, float, float]]) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    displacement_header = re.compile(r"displacements \(vx,vy,vz\)", re.IGNORECASE)
    lines = text.splitlines()
    displacements: dict[int, tuple[float, float, float]] = {}
    reading = False
    for line in lines:
        if displacement_header.search(line):
            reading = True
            continue
        if reading:
            match = re.match(
                r"\s*(\d+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)\s*$",
                line,
            )
            if match:
                displacements[int(match.group(1))] = tuple(float(value) for value in match.groups()[1:])
            elif displacements and line.strip():
                break
    if not displacements:
        raise SolveError("No nodal displacement table was found in the CalculiX .dat file")

    max_node, max_vector = max(
        displacements.items(), key=lambda item: math.sqrt(sum(value * value for value in item[1]))
    )
    max_magnitude = math.sqrt(sum(value * value for value in max_vector))
    reaction_match = re.search(
        r"total force \(fx,fy,fz\) for set FIXED.*?\n\s*"
        r"([-+\d.Ee]+)\s+([-+\d.Ee]+)\s+([-+\d.Ee]+)",
        text,
        re.IGNORECASE,
    )
    if reaction_match is None:
        raise SolveError("No total fixed-support reaction was found in the CalculiX .dat file")
    reaction = tuple(float(value) for value in reaction_match.groups())
    return {
        "nonzero_displacement": max_magnitude > 0.0,
        "maximum_displacement_m": max_magnitude,
        "maximum_displacement_node": max_node,
        "maximum_displacement_vector_m": list(max_vector),
        "maximum_displacement_node_x_m": nodes[max_node][0],
        "direction_consistent_with_negative_z_load": max_vector[2] < 0.0,
        "maximum_at_free_end": math.isclose(nodes[max_node][0], 1.0, abs_tol=COORDINATE_TOLERANCE_M),
        "fixed_reaction_n": list(reaction),
        "reaction_balances_applied_z_load": math.isclose(
            reaction[2], RESULTANT_FORCE_N, rel_tol=1.0e-8, abs_tol=1.0e-5
        ),
    }


def main() -> int:
    output_dir = Path("artifacts/cantilever").resolve()
    mesh_path = output_dir / "cantilever.msh"
    if not mesh_path.is_file():
        print(f"error: mesh not found; run generate_cantilever_mesh.py first: {mesh_path}", file=sys.stderr)
        return 1
    ccx = shutil.which("ccx")
    if ccx is None:
        print("error: ccx was not found on PATH", file=sys.stderr)
        return 1

    job_name = "cantilever_static"
    deck_path = output_dir / f"{job_name}.inp"
    for path in output_dir.glob(f"{job_name}.*"):
        path.unlink()
    try:
        nodes, _ = read_msh(mesh_path)
        model, deck = prepare_model(mesh_path)
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
        frd_text = frd_path.read_text(encoding="utf-8", errors="replace")
        sanity["stress_output_present"] = "STRESS" in frd_text.upper()
        sanity["solver_completed"] = "JOB FINISHED" in result.stdout.upper()
        sanity["solver_warnings"] = [
            line.strip()
            for line in result.stdout.splitlines()
            if "warning" in line.lower()
        ]
        if not sanity["solver_completed"]:
            raise SolveError(
                f"CalculiX did not report JOB FINISHED (exit {result.returncode}): "
                f"{result.stdout.strip()} {result.stderr.strip()}"
            )
        if not all(
            sanity[key]
            for key in (
                "nonzero_displacement",
                "direction_consistent_with_negative_z_load",
                "maximum_at_free_end",
                "stress_output_present",
                "solver_completed",
                "reaction_balances_applied_z_load",
            )
        ):
            raise SolveError(f"One or more basic solve sanity checks failed: {sanity}")
    except (OSError, subprocess.TimeoutExpired, SolveError, ValueError) as error:
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
