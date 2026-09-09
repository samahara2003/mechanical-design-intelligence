"""Generate and mesh the deterministic cantilever STEP benchmark with Gmsh CLI."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


LENGTH_M = 1.0
WIDTH_M = 0.05
HEIGHT_M = 0.05
DEFAULT_MESH_SIZE_M = 0.0125
BOUNDING_BOX_TOLERANCE_M = 1.0e-6
METRES_TO_STEP_MILLIMETRES = 1000.0

ELEMENT_TYPE_NAMES = {
    1: "2-node line",
    2: "3-node triangle",
    4: "4-node tetrahedron",
    9: "6-node second-order triangle",
    11: "10-node second-order tetrahedron",
    15: "1-node point",
}


class SpikeError(RuntimeError):
    """Raised when an engineering-spike prerequisite or invariant fails."""


def gmsh_path(path: Path) -> str:
    """Return an absolute path formatted safely for a Gmsh string literal."""
    return path.resolve().as_posix().replace('"', '\\"')


def run_gmsh(gmsh: str, geometry_script: Path) -> str:
    result = subprocess.run(
        [gmsh, str(geometry_script), "-0", "-nopopup"],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        raise SpikeError(
            f"Gmsh failed for {geometry_script} with exit code "
            f"{result.returncode}:\n{output.strip()}"
        )
    return output


def write_fixture_script(path: Path, step_path: Path) -> None:
    length_mm = LENGTH_M * METRES_TO_STEP_MILLIMETRES
    width_mm = WIDTH_M * METRES_TO_STEP_MILLIMETRES
    height_mm = HEIGHT_M * METRES_TO_STEP_MILLIMETRES
    path.write_text(
        f'''// Deterministic cantilever fixture. STEP export uses millimetres. Generated; do not edit.
SetFactory("OpenCASCADE");
Box(1) = {{0, 0, 0, {length_mm}, {width_mm}, {height_mm}}};
Save "{gmsh_path(step_path)}";
''',
        encoding="utf-8",
    )


def write_mesh_script(
    path: Path, step_path: Path, msh_path: Path, inp_path: Path, mesh_size_m: float
) -> None:
    path.write_text(
        f'''// Import the STEP boundary and create semantic FEA groups. Generated; do not edit.
SetFactory("OpenCASCADE");
Geometry.OCCTargetUnit = "M";
Merge "{gmsh_path(step_path)}";

eps = {BOUNDING_BOX_TOLERANCE_M};
volumes[] = Volume {{:}};
surfaces[] = Surface {{:}};
fixed[] = Surface In BoundingBox {{-eps, -eps, -eps, eps, {WIDTH_M} + eps, {HEIGHT_M} + eps}};
load[] = Surface In BoundingBox {{{LENGTH_M} - eps, -eps, -eps, {LENGTH_M} + eps, {WIDTH_M} + eps, {HEIGHT_M} + eps}};

If (#volumes[] != 1)
  Error("Expected exactly one imported solid volume");
EndIf
If (#fixed[] != 1)
  Error("Expected exactly one fixed-end surface at x=0");
EndIf
If (#load[] != 1)
  Error("Expected exactly one load-end surface at x=1 m");
EndIf

Physical Volume("beam", 1) = {{volumes[]}};
Physical Surface("fixed", 2) = {{fixed[]}};
Physical Surface("load", 3) = {{load[]}};

Mesh.MeshSizeMin = {mesh_size_m};
Mesh.MeshSizeMax = {mesh_size_m};
Mesh.Algorithm3D = 1;
Mesh.ElementOrder = 2;
Mesh.MshFileVersion = 2.2;
Mesh.Binary = 0;
Mesh 3;

Printf("MDI_GEOMETRY volumes=%g surfaces=%g fixed=%g load=%g", #volumes[], #surfaces[], #fixed[], #load[]);
Save "{gmsh_path(msh_path)}";
Save "{gmsh_path(inp_path)}";
''',
        encoding="utf-8",
    )


def parse_geometry_counts(output: str) -> dict[str, int]:
    match = re.search(
        r"MDI_GEOMETRY volumes=(\d+) surfaces=(\d+) fixed=(\d+) load=(\d+)",
        output,
    )
    if match is None:
        raise SpikeError("Gmsh output did not contain the expected geometry validation summary")
    return dict(zip(("volumes", "surfaces", "fixed", "load"), map(int, match.groups())))


def parse_msh(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()

    def section_count(section: str) -> int:
        try:
            return int(lines[lines.index(section) + 1])
        except (ValueError, IndexError) as error:
            raise SpikeError(f"Invalid or missing {section} section in {path}") from error

    node_count = section_count("$Nodes")
    element_count = section_count("$Elements")
    element_start = lines.index("$Elements") + 2
    element_lines = lines[element_start : element_start + element_count]
    type_counts: dict[int, int] = {}
    for line in element_lines:
        fields = line.split()
        if len(fields) < 3:
            raise SpikeError(f"Malformed element record in {path}: {line}")
        element_type = int(fields[1])
        type_counts[element_type] = type_counts.get(element_type, 0) + 1

    physical_names: dict[str, dict[str, int]] = {}
    if "$PhysicalNames" in lines:
        start = lines.index("$PhysicalNames") + 2
        count = int(lines[start - 1])
        for line in lines[start : start + count]:
            dimension, tag, quoted_name = line.split(maxsplit=2)
            physical_names[quoted_name.strip('"')] = {
                "dimension": int(dimension),
                "tag": int(tag),
            }

    expected_groups = {
        "beam": {"dimension": 3, "tag": 1},
        "fixed": {"dimension": 2, "tag": 2},
        "load": {"dimension": 2, "tag": 3},
    }
    if physical_names != expected_groups:
        raise SpikeError(
            f"Unexpected physical groups in {path}: {physical_names}; expected {expected_groups}"
        )

    tetrahedron_count = type_counts.get(11, 0)
    if tetrahedron_count == 0:
        raise SpikeError(f"No 10-node second-order tetrahedral elements were found in {path}")

    return {
        "node_count": node_count,
        "element_count": element_count,
        "volume_element_count": tetrahedron_count,
        "volume_element_type": "C3D10",
        "element_types": [
            {
                "gmsh_type": element_type,
                "name": ELEMENT_TYPE_NAMES.get(element_type, "unknown"),
                "count": count,
            }
            for element_type, count in sorted(type_counts.items())
        ],
        "physical_groups": physical_names,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/cantilever"),
        help="artifact directory (default: artifacts/cantilever)",
    )
    parser.add_argument(
        "--mesh-size",
        type=float,
        default=DEFAULT_MESH_SIZE_M,
        help=f"uniform provisional mesh size in metres (default: {DEFAULT_MESH_SIZE_M})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 < args.mesh_size <= min(WIDTH_M, HEIGHT_M):
        print(
            "error: --mesh-size must be greater than zero and no larger than the "
            "smallest beam cross-section dimension",
            file=sys.stderr,
        )
        return 2

    gmsh = shutil.which("gmsh")
    if gmsh is None:
        print("error: gmsh was not found on PATH", file=sys.stderr)
        return 1

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_script = output_dir / "generate_fixture.geo"
    mesh_script = output_dir / "mesh_imported_step.geo"
    step_path = output_dir / "cantilever.step"
    msh_path = output_dir / "cantilever.msh"
    inp_path = output_dir / "cantilever_mesh.inp"
    for generated_output in (step_path, msh_path, inp_path):
        generated_output.unlink(missing_ok=True)

    try:
        write_fixture_script(fixture_script, step_path)
        run_gmsh(gmsh, fixture_script)
        if not step_path.is_file() or step_path.stat().st_size == 0:
            raise SpikeError(f"STEP fixture was not created: {step_path}")

        write_mesh_script(mesh_script, step_path, msh_path, inp_path, args.mesh_size)
        mesh_output = run_gmsh(gmsh, mesh_script)
        geometry = parse_geometry_counts(mesh_output)
        mesh = parse_msh(msh_path)
        if not inp_path.is_file() or inp_path.stat().st_size == 0:
            raise SpikeError(f"CalculiX-compatible mesh was not created: {inp_path}")
    except (OSError, subprocess.TimeoutExpired, SpikeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = {
        "status": "ok",
        "units": "SI (metres)",
        "gmsh_executable": gmsh,
        "geometry": geometry,
        "mesh_size_m": args.mesh_size,
        "mesh": mesh,
        "artifacts": {
            "step": str(step_path),
            "msh": str(msh_path),
            "calculix_inp": str(inp_path),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
