"""Parse current CalculiX DAT output into solver-neutral numerical results."""

from __future__ import annotations

import re
from pathlib import Path

from numerical_results import (
    IntegrationPointStress,
    NodalDisplacement,
    NodalReaction,
    NumericalResult,
    StressTensor,
    Vector3,
)


class CalculixResultParseError(RuntimeError):
    """Raised when required CalculiX output is absent, malformed, or ambiguous."""


NUMBER = r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?"
NODAL_ROW = re.compile(rf"^\s*(\d+)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$")
STRESS_ROW = re.compile(
    rf"^\s*(\d+)\s+(\d+)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+"
    rf"({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$"
)
VECTOR_ROW = re.compile(rf"^\s*({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$")


def _parse_nodal_table(
    text: str,
    header: re.Pattern[str],
    quantity: str,
) -> list[tuple[int, Vector3]]:
    records = []
    reading = False
    found_header = False
    for line in text.splitlines():
        if header.search(line):
            reading = True
            found_header = True
            continue
        if not reading:
            continue
        match = NODAL_ROW.match(line)
        if match:
            records.append(
                (int(match.group(1)), Vector3(*(float(value) for value in match.groups()[1:])))
            )
            continue
        if not line.strip():
            continue
        if re.match(r"^\s*\d", line):
            raise CalculixResultParseError(f"Malformed CalculiX {quantity} record: {line.strip()}")
        if records:
            break
    if not found_header:
        raise CalculixResultParseError(f"CalculiX {quantity} table header is missing")
    if not records:
        raise CalculixResultParseError(f"CalculiX {quantity} table contains no complete records")
    return records


def parse_displacements_dat(text: str) -> tuple[NodalDisplacement, ...]:
    rows = _parse_nodal_table(
        text,
        re.compile(r"displacements \(vx,vy,vz\)", re.IGNORECASE),
        "displacement",
    )
    return tuple(NodalDisplacement(node, vector) for node, vector in rows)


def parse_reactions_dat(text: str, set_name: str = "FIXED") -> tuple[NodalReaction, ...]:
    rows = _parse_nodal_table(
        text,
        re.compile(
            rf"forces \(fx,fy,fz\) for set {re.escape(set_name)}\b",
            re.IGNORECASE,
        ),
        "reaction",
    )
    return tuple(NodalReaction(node, vector) for node, vector in rows)


def parse_reaction_resultant_dat(text: str, set_name: str = "FIXED") -> Vector3:
    lines = text.splitlines()
    header = re.compile(
        rf"total force \(fx,fy,fz\) for set {re.escape(set_name)}\b",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        if not header.search(line):
            continue
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                continue
            match = VECTOR_ROW.match(candidate)
            if match:
                return Vector3(*(float(value) for value in match.groups()))
            raise CalculixResultParseError(
                f"Malformed CalculiX total reaction record: {candidate.strip()}"
            )
    raise CalculixResultParseError("CalculiX total reaction table is missing")


def parse_integration_point_stresses_dat(text: str) -> tuple[IntegrationPointStress, ...]:
    header = re.compile(
        r"stresses \(elem, integ\.pnt\.,sxx,syy,szz,sxy,sxz,syz\)",
        re.IGNORECASE,
    )
    records = []
    reading = False
    found_header = False
    for line in text.splitlines():
        if header.search(line):
            reading = True
            found_header = True
            continue
        if not reading:
            continue
        match = STRESS_ROW.match(line)
        if match:
            values = tuple(float(value) for value in match.groups()[2:])
            records.append(
                IntegrationPointStress(
                    element_id=int(match.group(1)),
                    integration_point=int(match.group(2)),
                    stress_pa=StressTensor(*values),
                )
            )
            continue
        if not line.strip():
            continue
        if re.match(r"^\s*\d", line):
            raise CalculixResultParseError(f"Malformed CalculiX stress record: {line.strip()}")
        if records:
            break
    if not found_header:
        raise CalculixResultParseError("CalculiX integration-point stress table header is missing")
    if not records:
        raise CalculixResultParseError(
            "CalculiX integration-point stress table contains no complete records"
        )
    return tuple(records)


def parse_calculix_dat_text(text: str, reaction_set_name: str = "FIXED") -> NumericalResult:
    """Parse the authoritative axial DAT tables from already-decoded text."""
    return NumericalResult(
        displacements=parse_displacements_dat(text),
        reactions=parse_reactions_dat(text, reaction_set_name),
        integration_point_stresses=parse_integration_point_stresses_dat(text),
        reaction_resultant_n=parse_reaction_resultant_dat(text, reaction_set_name),
    )


def parse_calculix_dat(path: Path, reaction_set_name: str = "FIXED") -> NumericalResult:
    """Parse the authoritative DAT tables used by current linear-static benchmarks."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_calculix_dat_text(text, reaction_set_name)
