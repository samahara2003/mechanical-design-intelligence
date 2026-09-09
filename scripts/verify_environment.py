"""Verify command-line tools required by the Engineering Spike."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys


def run_version(
    label: str,
    command: str,
    arguments: list[str],
    version_pattern: str | None = None,
) -> bool:
    executable = shutil.which(command)
    if executable is None:
        print(f"{label}: NOT FOUND on PATH")
        return False

    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"{label}: ERROR ({error})")
        print(f"  path: {executable}")
        return False

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    version_identified = version_pattern is None or re.search(
        version_pattern, output, re.IGNORECASE
    ) is not None
    ok = version_identified and (result.returncode == 0 or version_pattern is not None)
    if ok:
        status = "OK"
    elif not version_identified:
        status = "ERROR (version not identified)"
    else:
        status = f"ERROR (exit {result.returncode})"
    print(f"{label}: {status}")
    print(f"  path: {executable}")
    if output:
        print(f"  version output: {output.splitlines()[0]}")
    return ok


def main() -> int:
    print(f"python: OK")
    print(f"  path: {sys.executable}")
    print(f"  version: {sys.version.split()[0]}")

    pip_ok = run_version("pip", sys.executable, ["-m", "pip", "--version"])
    gmsh_ok = run_version("gmsh", "gmsh", ["--version"])
    ccx_ok = run_version(
        "ccx", "ccx", ["-v"], r"\bThis is Version\s+\d+(?:\.\d+)+\b"
    )

    return 0 if pip_ok and gmsh_ok and ccx_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
