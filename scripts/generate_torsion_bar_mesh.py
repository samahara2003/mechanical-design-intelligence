"""Generate the square-bar torsion STEP fixture and C3D10 mesh."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from generate_cantilever_mesh import (
    BOUNDING_BOX_TOLERANCE_M,
    ELEMENT_TYPES,
    METRES_TO_STEP_MILLIMETRES,
    SpikeError,
    gmsh_path,
    parse_geometry_counts,
    parse_msh,
    run_gmsh,
)


LENGTH_M = 1.0
SIDE_M = 0.05
MESH_SIZE_M = 0.0125
PHYSICAL_GROUPS = {
    "torsion_bar": {"dimension": 3, "tag": 1},
    "fixed": {"dimension": 2, "tag": 2},
    "torque_load": {"dimension": 2, "tag": 3},
}


def write_fixture_script(path: Path, step_path: Path) -> None:
    scale = METRES_TO_STEP_MILLIMETRES
    path.write_text(
        f'''// Deterministic square torsion-bar fixture. STEP export uses millimetres. Generated; do not edit.
SetFactory("OpenCASCADE");
Box(1) = {{0, 0, 0, {LENGTH_M * scale}, {SIDE_M * scale}, {SIDE_M * scale}}};
Save "{gmsh_path(step_path)}";
''',
        encoding="utf-8",
    )


def write_mesh_script(path: Path, step_path: Path, msh_path: Path, inp_path: Path) -> None:
    path.write_text(
        f'''// Import torsion-bar STEP and create benchmark-specific FEA groups. Generated; do not edit.
SetFactory("OpenCASCADE");
Geometry.OCCTargetUnit = "M";
Merge "{gmsh_path(step_path)}";

eps = {BOUNDING_BOX_TOLERANCE_M};
volumes[] = Volume {{:}};
surfaces[] = Surface {{:}};
fixed[] = Surface In BoundingBox {{-eps, -eps, -eps, eps, {SIDE_M} + eps, {SIDE_M} + eps}};
torqueLoad[] = Surface In BoundingBox {{{LENGTH_M} - eps, -eps, -eps, {LENGTH_M} + eps, {SIDE_M} + eps, {SIDE_M} + eps}};

If (#volumes[] != 1)
  Error("Expected exactly one imported torsion-bar solid");
EndIf
If (#fixed[] != 1)
  Error("Expected exactly one fixed face at x=0");
EndIf
If (#torqueLoad[] != 1)
  Error("Expected exactly one torque-load face at x=1 m");
EndIf

Physical Volume("torsion_bar", 1) = {{volumes[]}};
Physical Surface("fixed", 2) = {{fixed[]}};
Physical Surface("torque_load", 3) = {{torqueLoad[]}};

Mesh.MeshSizeMin = {MESH_SIZE_M};
Mesh.MeshSizeMax = {MESH_SIZE_M};
Mesh.Algorithm3D = 1;
Mesh.ElementOrder = {ELEMENT_TYPES['C3D10']['order']};
Mesh.MshFileVersion = 2.2;
Mesh.Binary = 0;
Mesh 3;

Printf("MDI_GEOMETRY volumes=%g surfaces=%g fixed=%g load=%g", #volumes[], #surfaces[], #fixed[], #torqueLoad[]);
Save "{gmsh_path(msh_path)}";
Save "{gmsh_path(inp_path)}";
''',
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/torsion_bar"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gmsh = shutil.which("gmsh")
    if gmsh is None:
        print("error: gmsh was not found on PATH", file=sys.stderr)
        return 1
    version_result = subprocess.run(
        [gmsh, "--version"], capture_output=True, check=False, text=True, timeout=10
    )
    if version_result.returncode != 0 or not version_result.stdout.strip():
        print("error: unable to identify the Gmsh version", file=sys.stderr)
        return 1

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_script = output_dir / "generate_torsion_bar_fixture.geo"
    mesh_script = output_dir / "mesh_torsion_bar_step.geo"
    step_path = output_dir / "torsion_bar.step"
    msh_path = output_dir / "torsion_bar.msh"
    inp_path = output_dir / "torsion_bar_mesh.inp"
    summary_path = output_dir / "torsion_bar_mesh_summary.json"
    for generated in (step_path, msh_path, inp_path, summary_path):
        generated.unlink(missing_ok=True)
    try:
        write_fixture_script(fixture_script, step_path)
        run_gmsh(gmsh, fixture_script)
        if not step_path.is_file() or step_path.stat().st_size == 0:
            raise SpikeError(f"STEP fixture was not created: {step_path}")
        write_mesh_script(mesh_script, step_path, msh_path, inp_path)
        geometry = parse_geometry_counts(run_gmsh(gmsh, mesh_script))
        mesh = parse_msh(msh_path, "C3D10", PHYSICAL_GROUPS)
        if not inp_path.is_file() or inp_path.stat().st_size == 0:
            raise SpikeError(f"CalculiX-compatible mesh was not created: {inp_path}")
    except (OSError, subprocess.TimeoutExpired, SpikeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = {
        "status": "ok",
        "benchmark_id": "square-bar-torsion-c3d10-v1",
        "units": "SI (metres)",
        "gmsh_executable": gmsh,
        "gmsh_version": version_result.stdout.strip().splitlines()[0],
        "geometry": geometry,
        "mesh_size_m": MESH_SIZE_M,
        "mesh": mesh,
        "artifacts": {
            "step": str(step_path),
            "step_sha256": hashlib.sha256(step_path.read_bytes()).hexdigest(),
            "msh": str(msh_path),
            "msh_sha256": hashlib.sha256(msh_path.read_bytes()).hexdigest(),
            "gmsh_calculix_inp": str(inp_path),
            "gmsh_calculix_inp_sha256": hashlib.sha256(inp_path.read_bytes()).hexdigest(),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary["artifacts"]["summary"] = str(summary_path)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
