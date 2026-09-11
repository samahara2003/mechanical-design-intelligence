"""Build and run the C3D10 square-bar torsion CalculiX model."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cantilever_verification import VerificationError, quadratic_triangle_weights, read_displacements
from run_axial_bar_solve import verify_export_connectivity
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
RESULTANT_TORQUE_N_M = 100.0
LENGTH_M = 1.0
SIDE_M = 0.05
FACE_CENTROID_M = (LENGTH_M, SIDE_M / 2.0, SIDE_M / 2.0)
COORDINATE_TOLERANCE_M = 1.0e-8

# Exact for polynomial degree <= 3 on a triangle. Weights are normalized to sum to one.
TRIANGLE_CUBIC_RULE = (
    ((1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0), -27.0 / 48.0),
    ((0.6, 0.2, 0.2), 25.0 / 48.0),
    ((0.2, 0.6, 0.2), 25.0 / 48.0),
    ((0.2, 0.2, 0.6), 25.0 / 48.0),
)


def force_and_moment_resultants(
    nodal_forces: dict[int, tuple[float, float, float]],
    nodes: dict[int, tuple[float, float, float]],
    reference_m: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    force = [0.0, 0.0, 0.0]
    moment = [0.0, 0.0, 0.0]
    for node, vector in nodal_forces.items():
        for axis in range(3):
            force[axis] += vector[axis]
        rx, ry, rz = (nodes[node][axis] - reference_m[axis] for axis in range(3))
        fx, fy, fz = vector
        moment[0] += ry * fz - rz * fy
        moment[1] += rz * fx - rx * fz
        moment[2] += rx * fy - ry * fx
    return tuple(force), tuple(moment)


def consistent_torque_face_loads(
    faces: list[dict],
    nodes: dict[int, tuple[float, float, float]],
    target_torque_n_m: float,
    center_m: tuple[float, float, float] = FACE_CENTROID_M,
) -> tuple[dict[int, tuple[float, float, float]], dict]:
    """Integrate t=(0,-k(z-zc),k(y-yc)) with quadratic face functions."""
    if target_torque_n_m == 0.0:
        raise ValueError("Target torque must be nonzero")
    unit_loads: dict[int, list[float]] = {}
    area_total = 0.0
    for face in faces:
        if len(face["nodes"]) != 6:
            raise SolveError("Torque-load face must use six-node quadratic triangles")
        corner_coordinates = [nodes[node] for node in face["nodes"][:3]]
        area = triangle_area(*corner_coordinates)
        area_total += area
        for barycentric, quadrature_weight in TRIANGLE_CUBIC_RULE:
            y = sum(barycentric[index] * corner_coordinates[index][1] for index in range(3))
            z = sum(barycentric[index] * corner_coordinates[index][2] for index in range(3))
            traction = (0.0, -(z - center_m[2]), y - center_m[1])
            shape_weights = quadratic_triangle_weights(barycentric)
            for index, node in enumerate(face["nodes"]):
                vector = unit_loads.setdefault(node, [0.0, 0.0, 0.0])
                for axis in range(3):
                    vector[axis] += (
                        area * quadrature_weight * shape_weights[index] * traction[axis]
                    )
    unit = {node: tuple(vector) for node, vector in unit_loads.items()}
    unit_force, unit_moment = force_and_moment_resultants(unit, nodes, center_m)
    if unit_moment[0] == 0.0:
        raise SolveError("Integrated unit traction produces zero X torque")
    traction_scale_pa_per_m = target_torque_n_m / unit_moment[0]
    loads = {
        node: tuple(traction_scale_pa_per_m * value for value in vector)
        for node, vector in unit.items()
    }
    force, moment = force_and_moment_resultants(loads, nodes, center_m)
    return loads, {
        "face_area_m2": area_total,
        "traction_scale_pa_per_m": traction_scale_pa_per_m,
        "unit_scale_resultant_n": list(unit_force),
        "unit_scale_moment_n_m": list(unit_moment),
        "integrated_resultant_n": list(force),
        "integrated_moment_about_face_centroid_n_m": list(moment),
        "quadrature": "four-point degree-three exact triangle rule",
        "traction_field": "t=(0, -k*(z-0.025), +k*(y-0.025)) Pa",
    }


def read_fixed_reactions(path: Path) -> tuple[dict[int, tuple[float, float, float]], tuple[float, ...]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "forces (fx,fy,fz) for set FIXED" in line
        ),
        None,
    )
    if start is None:
        raise SolveError("No fixed-support reaction table found in DAT")
    reactions = {}
    total = None
    number = r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?"
    row_pattern = re.compile(rf"^\s*(\d+)\s+({number})\s+({number})\s+({number})\s*$")
    total_pattern = re.compile(rf"^\s*({number})\s+({number})\s+({number})\s*$")
    for line in lines[start + 1 :]:
        if "total force" in line.lower():
            continue
        row = row_pattern.match(line)
        if row:
            reactions[int(row.group(1))] = tuple(float(row.group(i)) for i in range(2, 5))
            continue
        if reactions:
            match = total_pattern.match(line)
            if match:
                total = tuple(float(match.group(i)) for i in range(1, 4))
                break
            if line.strip() and not line.lstrip().lower().startswith("node"):
                break
    if not reactions:
        raise SolveError("Fixed-support DAT table contains no nodal reactions")
    if total is None:
        total = tuple(sum(value[axis] for value in reactions.values()) for axis in range(3))
    return reactions, total


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
        raise SolveError("Torsion-bar volume, fixed, and torque-load groups must be nonempty")
    verify_export_connectivity(mesh_path.with_name("torsion_bar_mesh.inp"), volumes)
    fixed_nodes = sorted({node for face in fixed_faces for node in face["nodes"]})
    load_nodes = sorted({node for face in load_faces for node in face["nodes"]})
    if any(abs(nodes[node][0]) > COORDINATE_TOLERANCE_M for node in fixed_nodes):
        raise SolveError("Fixed node set contains a node away from x=0")
    if any(abs(nodes[node][0] - LENGTH_M) > COORDINATE_TOLERANCE_M for node in load_nodes):
        raise SolveError("Torque-load node set contains a node away from x=1")
    mapped_surface = map_surface_faces(load_faces, volumes, "C3D10")
    nodal_loads, load_evidence = consistent_torque_face_loads(
        load_faces, nodes, RESULTANT_TORQUE_N_M
    )
    force = load_evidence["integrated_resultant_n"]
    moment = load_evidence["integrated_moment_about_face_centroid_n_m"]
    if not math.isclose(load_evidence["face_area_m2"], SIDE_M**2, rel_tol=1e-10, abs_tol=1e-12):
        raise SolveError("Integrated torque-load face area is incorrect")
    if max(abs(value) for value in force) > 1e-8:
        raise SolveError(f"Torque load has nonzero resultant force: {force}")
    expected_moment = (RESULTANT_TORQUE_N_M, 0.0, 0.0)
    if not all(math.isclose(moment[i], expected_moment[i], rel_tol=1e-10, abs_tol=1e-8) for i in range(3)):
        raise SolveError(f"Integrated moment is {moment}, expected {expected_moment}")

    lines = [
        "*HEADING",
        "Mechanical Design Intelligence - square bar torsion C3D10 verification",
        "*NODE, NSET=ALLNODES",
    ]
    lines.extend(f"{node}, {x:.16g}, {y:.16g}, {z:.16g}" for node, (x, y, z) in sorted(nodes.items()))
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=TORSION_BAR")
    lines.extend(f"{element['id']}, " + ", ".join(map(str, element["nodes"])) for element in volumes)
    lines.append("*NSET, NSET=FIXED")
    lines.extend(wrapped_ids(fixed_nodes))
    lines.append("*NSET, NSET=TORQUE_LOAD_NODES")
    lines.extend(wrapped_ids(load_nodes))
    face_sets: dict[str, list[int]] = {}
    for element_id, face_label in mapped_surface:
        face_sets.setdefault(face_label, []).append(element_id)
    for face_label, element_ids in sorted(face_sets.items()):
        lines.append(f"*ELSET, ELSET=TORQUE_LOAD_{face_label}")
        lines.extend(wrapped_ids(sorted(element_ids)))
    lines.append("*SURFACE, NAME=TORQUE_LOAD_FACE, TYPE=ELEMENT")
    lines.extend(f"TORQUE_LOAD_{label}, {label}" for label in sorted(face_sets))
    lines.extend(
        [
            "*MATERIAL, NAME=STEEL",
            "*ELASTIC",
            f"{YOUNGS_MODULUS_PA:.16g}, {POISSONS_RATIO}",
            "*SOLID SECTION, ELSET=TORSION_BAR, MATERIAL=STEEL",
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
            "*EL PRINT, ELSET=TORSION_BAR",
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
        **load_evidence,
        "calculix_load_representation": "consistent C3D10-face nodal forces from linearly varying global Y/Z traction",
        "connectivity_verified_against_gmsh_export": True,
    }
    return model, "\n".join(lines)


def inspect_dat(path: Path, nodes: dict[int, tuple[float, float, float]]) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    displacements = read_displacements(path)
    maximum_node, maximum_vector = max(
        displacements.items(), key=lambda item: math.sqrt(sum(value**2 for value in item[1]))
    )
    reactions, reaction_force = read_fixed_reactions(path)
    _, reaction_moment = force_and_moment_resultants(reactions, nodes, FACE_CENTROID_M)
    balances = (
        max(abs(value) for value in reaction_force) <= 1e-5
        and math.isclose(reaction_moment[0], -RESULTANT_TORQUE_N_M, rel_tol=1e-7, abs_tol=1e-5)
        # Individual DAT reaction rows are printed to limited precision; their
        # reconstructed transverse moments retain that output-rounding error.
        and abs(reaction_moment[1]) <= 1e-4
        and abs(reaction_moment[2]) <= 1e-4
    )
    return {
        "maximum_displacement_node": maximum_node,
        "maximum_displacement_vector_m": list(maximum_vector),
        "maximum_displacement_m": math.sqrt(sum(value**2 for value in maximum_vector)),
        "maximum_displacement_node_x_m": nodes[maximum_node][0],
        "maximum_at_loaded_end": math.isclose(nodes[maximum_node][0], LENGTH_M, abs_tol=COORDINATE_TOLERANCE_M),
        "fixed_reaction_n": list(reaction_force),
        "fixed_reaction_moment_about_loaded_face_centroid_n_m": list(reaction_moment),
        "reaction_balances_applied_torque": balances,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/torsion_bar"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    mesh_path = output_dir / "torsion_bar.msh"
    if not mesh_path.is_file():
        print(f"error: mesh not found; run generate_torsion_bar_mesh.py first: {mesh_path}", file=sys.stderr)
        return 1
    ccx = shutil.which("ccx")
    if ccx is None:
        print("error: ccx was not found on PATH", file=sys.stderr)
        return 1
    job_name = "torsion_bar_static"
    for path in output_dir.glob(f"{job_name}.*"):
        path.unlink()
    try:
        nodes, _ = read_msh(mesh_path)
        model, deck = prepare_model(mesh_path)
        deck_path = output_dir / f"{job_name}.inp"
        deck_path.write_text(deck, encoding="ascii")
        result = subprocess.run(
            [ccx, "-i", job_name], cwd=output_dir, capture_output=True, check=False, text=True, timeout=120
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
        sanity["integration_point_stress_output_present"] = "stresses (elem, integ.pnt." in dat_path.read_text(encoding="utf-8", errors="replace")
        sanity["solver_completed"] = "JOB FINISHED" in result.stdout.upper()
        sanity["solver_warnings"] = [line.strip() for line in result.stdout.splitlines() if "warning" in line.lower()]
        if not all(
            sanity[key]
            for key in (
                "maximum_at_loaded_end",
                "reaction_balances_applied_torque",
                "integration_point_stress_output_present",
                "solver_completed",
            )
        ):
            raise SolveError(f"Torsion-bar solve sanity failed: {sanity}")
    except (OSError, StopIteration, subprocess.TimeoutExpired, SolveError, ValueError, VerificationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = {
        "status": "ok",
        "ccx_executable": ccx,
        "ccx_exit_code": result.returncode,
        "model": model,
        "sanity": sanity,
        "artifacts": {
            "input": str(deck_path), "dat": str(dat_path), "frd": str(frd_path),
            "sta": str(sta_path), "stdout": str(stdout_path), "stderr": str(stderr_path),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
