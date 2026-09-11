"""Create the deterministic bracket STEP and re-import it for C3D10 meshing."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from bracket_definition import (
    BASELINE_MESH_SIZE_M,
    BRACKET_LOAD_FACE,
    BRACKET_MOUNTING_FACES,
    BRACKET_VOLUME,
)
from generate_cantilever_mesh import (
    BOUNDING_BOX_TOLERANCE_M,
    ELEMENT_TYPES,
    METRES_TO_STEP_MILLIMETRES,
    SpikeError,
    gmsh_path,
    parse_msh,
    run_gmsh,
)
from run_cantilever_solve import read_msh


BASE_LENGTH_M = 0.140
BASE_WIDTH_M = 0.100
BASE_THICKNESS_M = 0.012
UPRIGHT_X_MIN_M = 0.128
UPRIGHT_HEIGHT_M = 0.120
ROOT_FILLET_RADIUS_M = 0.015
MOUNTING_HOLE_RADIUS_M = 0.006
MOUNTING_HOLE_CENTRES_M = ((0.035, 0.025), (0.035, 0.075))
LOAD_PAD_X_MAX_M = 0.155
LOAD_PAD_Y_MIN_M = 0.030
LOAD_PAD_Y_MAX_M = 0.070
LOAD_PAD_Z_MIN_M = 0.070
LOAD_PAD_Z_MAX_M = 0.110
LOAD_FACE_AREA_M2 = (LOAD_PAD_Y_MAX_M - LOAD_PAD_Y_MIN_M) * (
    LOAD_PAD_Z_MAX_M - LOAD_PAD_Z_MIN_M
)
PHYSICAL_GROUPS = {
    BRACKET_VOLUME.region_name: {"dimension": 3, "tag": 1},
    BRACKET_MOUNTING_FACES.region_name: {"dimension": 2, "tag": 2},
    BRACKET_LOAD_FACE.region_name: {"dimension": 2, "tag": 3},
}


def write_fixture_script(path: Path, step_path: Path) -> None:
    s = METRES_TO_STEP_MILLIMETRES
    path.write_text(
        f'''// Deterministic single-solid mounting bracket. STEP dimensions are millimetres.
SetFactory("OpenCASCADE");
Box(1) = {{0, 0, 0, {BASE_LENGTH_M*s}, {BASE_WIDTH_M*s}, {BASE_THICKNESS_M*s}}};
Box(2) = {{{UPRIGHT_X_MIN_M*s}, 0, {BASE_THICKNESS_M*s}, {(BASE_LENGTH_M-UPRIGHT_X_MIN_M)*s}, {BASE_WIDTH_M*s}, {(UPRIGHT_HEIGHT_M-BASE_THICKNESS_M)*s}}};
Cylinder(3) = {{{UPRIGHT_X_MIN_M*s}, 0, {BASE_THICKNESS_M*s}, 0, {BASE_WIDTH_M*s}, 0, {ROOT_FILLET_RADIUS_M*s}}};
Box(4) = {{{(UPRIGHT_X_MIN_M-ROOT_FILLET_RADIUS_M)*s}, 0, {BASE_THICKNESS_M*s}, {ROOT_FILLET_RADIUS_M*s}, {BASE_WIDTH_M*s}, {ROOT_FILLET_RADIUS_M*s}}};
rootFillet() = BooleanIntersection{{ Volume{{3}}; Delete; }}{{ Volume{{4}}; Delete; }};
Box(5) = {{{BASE_LENGTH_M*s}, {LOAD_PAD_Y_MIN_M*s}, {LOAD_PAD_Z_MIN_M*s}, {(LOAD_PAD_X_MAX_M-BASE_LENGTH_M)*s}, {(LOAD_PAD_Y_MAX_M-LOAD_PAD_Y_MIN_M)*s}, {(LOAD_PAD_Z_MAX_M-LOAD_PAD_Z_MIN_M)*s}}};
body() = BooleanUnion{{ Volume{{1}}; Delete; }}{{ Volume{{2, rootFillet(), 5}}; Delete; }};
Cylinder(6) = {{{MOUNTING_HOLE_CENTRES_M[0][0]*s}, {MOUNTING_HOLE_CENTRES_M[0][1]*s}, {-0.001*s}, 0, 0, {(BASE_THICKNESS_M+0.002)*s}, {MOUNTING_HOLE_RADIUS_M*s}}};
Cylinder(7) = {{{MOUNTING_HOLE_CENTRES_M[1][0]*s}, {MOUNTING_HOLE_CENTRES_M[1][1]*s}, {-0.001*s}, 0, 0, {(BASE_THICKNESS_M+0.002)*s}, {MOUNTING_HOLE_RADIUS_M*s}}};
bracket() = BooleanDifference{{ Volume{{body()}}; Delete; }}{{ Volume{{6, 7}}; Delete; }};
If (#bracket() != 1)
  Error("Expected one bracket solid after booleans");
EndIf
Save "{gmsh_path(step_path)}";
''',
        encoding="utf-8",
    )


def write_mesh_script(
    path: Path, step_path: Path, msh_path: Path, inp_path: Path, mesh_size_m: float
) -> None:
    holes = []
    for index, (x, y) in enumerate(MOUNTING_HOLE_CENTRES_M):
        holes.append(
            f'hole{index}[] = Surface In BoundingBox {{{x-MOUNTING_HOLE_RADIUS_M}-eps, '
            f'{y-MOUNTING_HOLE_RADIUS_M}-eps, -eps, {x+MOUNTING_HOLE_RADIUS_M}+eps, '
            f'{y+MOUNTING_HOLE_RADIUS_M}+eps, {BASE_THICKNESS_M}+eps}};'
        )
    path.write_text(
        f'''// Re-import STEP; resolve semantic regions geometrically; create C3D10 mesh.
SetFactory("OpenCASCADE");
Geometry.OCCTargetUnit = "M";
Merge "{gmsh_path(step_path)}";
eps = {BOUNDING_BOX_TOLERANCE_M};
volumes[] = Volume {{:}};
surfaces[] = Surface {{:}};
{chr(10).join(holes)}
loadPad[] = Surface In BoundingBox {{{LOAD_PAD_X_MAX_M}-eps, {LOAD_PAD_Y_MIN_M}-eps, {LOAD_PAD_Z_MIN_M}-eps, {LOAD_PAD_X_MAX_M}+eps, {LOAD_PAD_Y_MAX_M}+eps, {LOAD_PAD_Z_MAX_M}+eps}};
If (#volumes[] != 1)
  Error("Expected exactly one imported bracket solid");
EndIf
If (#hole0[] != 1 || #hole1[] != 1)
  Error("Expected one cylindrical surface for each mounting hole");
EndIf
If (#loadPad[] != 1)
  Error("Expected exactly one load-pad end face");
EndIf
Physical Volume("{BRACKET_VOLUME.region_name}", 1) = {{volumes[]}};
Physical Surface("{BRACKET_MOUNTING_FACES.region_name}", 2) = {{hole0[], hole1[]}};
Physical Surface("{BRACKET_LOAD_FACE.region_name}", 3) = {{loadPad[]}};
Mesh.MeshSizeMin = {mesh_size_m};
Mesh.MeshSizeMax = {mesh_size_m};
Mesh.Algorithm3D = 1;
Mesh.ElementOrder = {ELEMENT_TYPES['C3D10']['order']};
Mesh.MshFileVersion = 2.2;
Mesh.Binary = 0;
Mesh 3;
Printf("MDI_BRACKET_GEOMETRY volumes=%g surfaces=%g fixed=%g load=%g", #volumes[], #surfaces[], #hole0[] + #hole1[], #loadPad[]);
Save "{gmsh_path(msh_path)}";
Save "{gmsh_path(inp_path)}";
''',
        encoding="utf-8",
    )


def tetra_quality(corners: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    edges = []
    for i, j in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
        edges.append(math.dist(corners[i], corners[j]))
    a, b, c, d = corners
    ab = tuple(b[k] - a[k] for k in range(3))
    ac = tuple(c[k] - a[k] for k in range(3))
    ad = tuple(d[k] - a[k] for k in range(3))
    cross = (ac[1]*ad[2]-ac[2]*ad[1], ac[2]*ad[0]-ac[0]*ad[2], ac[0]*ad[1]-ac[1]*ad[0])
    volume = abs(sum(ab[k] * cross[k] for k in range(3))) / 6.0
    mean_ratio = min(
        1.0, 12.0 * (3.0 * volume) ** (2.0 / 3.0) / sum(edge**2 for edge in edges)
    )
    return volume, max(edges) / min(edges), mean_ratio


def mesh_quality(path: Path) -> dict:
    nodes, elements = read_msh(path)
    values = [
        tetra_quality([nodes[node] for node in element["nodes"][:4]])
        for element in elements
        if element["type"] == 11 and element["physical_tag"] == 1
    ]
    if not values or any(item[0] <= 0.0 for item in values):
        raise SpikeError("Bracket mesh contains no valid positive-volume C3D10 tetrahedra")
    volumes, edge_ratios, mean_ratios = zip(*values)
    return {
        "corner_tetrahedron_volume_m3": {"minimum": min(volumes), "maximum": max(volumes)},
        "edge_length_ratio": {"maximum": max(edge_ratios), "mean": sum(edge_ratios)/len(edge_ratios)},
        "mean_ratio_quality_0_to_1": {"minimum": min(mean_ratios), "mean": sum(mean_ratios)/len(mean_ratios), "maximum": max(mean_ratios)},
        "method": "corner-node tetrahedra; mean-ratio quality is 1 for an equilateral tetrahedron",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--step-path", type=Path, required=True)
    parser.add_argument("--mesh-size", type=float, default=BASELINE_MESH_SIZE_M)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.mesh_size <= BASE_THICKNESS_M:
        print("error: mesh size must be positive and no larger than plate thickness", file=sys.stderr)
        return 2
    gmsh = shutil.which("gmsh")
    if gmsh is None:
        print("error: gmsh was not found on PATH", file=sys.stderr)
        return 1
    version = subprocess.run([gmsh, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    step_path = args.step_path.resolve()
    step_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_script = step_path.parent / "generate_bracket_fixture.geo"
    mesh_script = output_dir / "mesh_bracket_step.geo"
    msh_path = output_dir / "bracket.msh"
    inp_path = output_dir / "bracket_mesh.inp"
    try:
        if not step_path.is_file():
            write_fixture_script(fixture_script, step_path)
            run_gmsh(gmsh, fixture_script)
        write_mesh_script(mesh_script, step_path, msh_path, inp_path, args.mesh_size)
        gmsh_output = run_gmsh(gmsh, mesh_script)
        match = re.search(r"MDI_BRACKET_GEOMETRY volumes=(\d+) surfaces=(\d+) fixed=(\d+) load=(\d+)", gmsh_output)
        if match is None:
            raise SpikeError("Gmsh bracket geometry summary is missing")
        mesh = parse_msh(msh_path, "C3D10", PHYSICAL_GROUPS)
        quality = mesh_quality(msh_path)
    except (OSError, subprocess.TimeoutExpired, SpikeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    warnings = [line.strip() for line in gmsh_output.splitlines() if "warning" in line.lower()]
    summary = {
        "status": "ok", "units": "SI", "gmsh_version": version,
        "geometry": dict(zip(("volumes", "surfaces", "fixed", "load"), map(int, match.groups()))),
        "mesh_size_m": args.mesh_size, "mesh": mesh, "quality": quality,
        "gmsh_warnings": warnings,
        "artifacts": {
            "step": str(step_path), "step_sha256": hashlib.sha256(step_path.read_bytes()).hexdigest(),
            "msh": str(msh_path), "msh_sha256": hashlib.sha256(msh_path.read_bytes()).hexdigest(),
            "gmsh_calculix_inp": str(inp_path),
        },
    }
    (output_dir / "bracket_mesh_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
