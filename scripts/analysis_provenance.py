"""Immutable execution provenance for completed Engineering Core analyses."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from engineering_domain import (
    AnalysisDefinition,
    ModelVersionReference,
    analysis_definition_to_dict,
)


ENGINEERING_EVIDENCE_POSTPROCESSOR_ID = "engineering-core-analysis-result"
ENGINEERING_EVIDENCE_POSTPROCESSOR_VERSION = "1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ProvenanceError(RuntimeError):
    """Raised when trustworthy execution provenance cannot be constructed."""


class ArtifactKind(str, Enum):
    CAD_STEP = "cad_step"
    MESH = "mesh"
    SOLVER_INPUT = "solver_input"
    SOLVER_DAT = "solver_dat"
    SOLVER_FRD = "solver_frd"


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonempty")
    return value.strip()


def _sha256(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name).lower()
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be 64 hexadecimal characters")
    return normalized


@dataclass(frozen=True)
class ArtifactProvenance:
    """Content identity for one current analysis artifact.

    ``local_path`` is optional execution/debug metadata. It is deliberately
    excluded from dataclass equality and hashing because a filesystem location
    is not durable artifact identity.
    """

    kind: ArtifactKind
    sha256: str
    byte_size: int | None = None
    local_path: str | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ArtifactKind):
            raise TypeError("artifact kind must be an ArtifactKind")
        object.__setattr__(self, "sha256", _sha256(self.sha256, "artifact SHA-256"))
        if self.byte_size is not None and (
            not isinstance(self.byte_size, int) or self.byte_size < 0
        ):
            raise ValueError("artifact byte size must be a nonnegative integer")
        if self.local_path is not None:
            object.__setattr__(
                self, "local_path", _required_text(str(self.local_path), "artifact local path")
            )


@dataclass(frozen=True)
class ToolProvenance:
    identifier: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _required_text(self.identifier, "tool identifier"))
        object.__setattr__(self, "version", _required_text(self.version, "tool version"))


@dataclass(frozen=True)
class PostprocessorProvenance:
    identifier: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "identifier", _required_text(self.identifier, "postprocessor identifier")
        )
        object.__setattr__(self, "version", _required_text(self.version, "postprocessor version"))


@dataclass(frozen=True)
class AnalysisProvenance:
    """Traceability from immutable engineering intent to exact execution artifacts."""

    model_version: ModelVersionReference
    analysis_definition_sha256: str
    cad_step: ArtifactProvenance
    mesh: ArtifactProvenance
    solver_input: ArtifactProvenance
    solver_dat: ArtifactProvenance | None
    solver_frd: ArtifactProvenance | None
    gmsh: ToolProvenance
    calculix: ToolProvenance
    postprocessor: PostprocessorProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.model_version, ModelVersionReference):
            raise TypeError("provenance model version must be a ModelVersionReference")
        object.__setattr__(
            self,
            "analysis_definition_sha256",
            _sha256(self.analysis_definition_sha256, "analysis-definition SHA-256"),
        )
        expected_kinds = (
            ("cad_step", self.cad_step, ArtifactKind.CAD_STEP),
            ("mesh", self.mesh, ArtifactKind.MESH),
            ("solver_input", self.solver_input, ArtifactKind.SOLVER_INPUT),
            ("solver_dat", self.solver_dat, ArtifactKind.SOLVER_DAT),
            ("solver_frd", self.solver_frd, ArtifactKind.SOLVER_FRD),
        )
        for field_name, artifact, expected_kind in expected_kinds:
            if artifact is None and field_name in {"solver_dat", "solver_frd"}:
                continue
            if not isinstance(artifact, ArtifactProvenance):
                raise TypeError(f"{field_name} must be ArtifactProvenance")
            if artifact.kind is not expected_kind:
                raise ValueError(f"{field_name} must have artifact kind {expected_kind.value}")
        if not isinstance(self.gmsh, ToolProvenance):
            raise TypeError("Gmsh provenance must be ToolProvenance")
        if not isinstance(self.calculix, ToolProvenance):
            raise TypeError("CalculiX provenance must be ToolProvenance")
        if not isinstance(self.postprocessor, PostprocessorProvenance):
            raise TypeError("postprocessor provenance must be PostprocessorProvenance")


def canonical_analysis_definition_json(definition: AnalysisDefinition) -> str:
    """Serialize engineering intent as stable canonical UTF-8 JSON text."""
    if not isinstance(definition, AnalysisDefinition):
        raise TypeError("definition must be an AnalysisDefinition")
    return json.dumps(
        analysis_definition_to_dict(definition),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def analysis_definition_fingerprint(definition: AnalysisDefinition) -> str:
    """Return deterministic SHA-256 identity for the complete engineering definition."""
    canonical = canonical_analysis_definition_json(definition).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file by streaming bytes, failing explicitly when it cannot be read."""
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("SHA-256 chunk size must be a positive integer")
    source = Path(path)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
    except OSError as error:
        raise ProvenanceError(f"Cannot read provenance artifact {source}: {error}") from error
    return digest.hexdigest()


def artifact_provenance_from_file(kind: ArtifactKind, path: Path) -> ArtifactProvenance:
    """Capture exact content identity and optional local execution location."""
    source = Path(path)
    digest = sha256_file(source)
    try:
        byte_size = source.stat().st_size
    except OSError as error:
        raise ProvenanceError(f"Cannot inspect provenance artifact {source}: {error}") from error
    return ArtifactProvenance(kind, digest, byte_size, str(source))


def build_analysis_provenance(
    definition: AnalysisDefinition,
    *,
    cad_step_path: Path,
    mesh_path: Path,
    solver_input_path: Path,
    solver_dat_path: Path | None,
    solver_frd_path: Path | None,
    gmsh_version: str,
    calculix_version: str,
) -> AnalysisProvenance:
    """Capture the current proven execution boundary without altering the definition."""
    if gmsh_version != definition.mesh.mesher_version:
        raise ProvenanceError("executed Gmsh version differs from AnalysisDefinition")
    if calculix_version != definition.solver.solver_version:
        raise ProvenanceError("executed CalculiX version differs from AnalysisDefinition")
    return AnalysisProvenance(
        model_version=definition.model_version,
        analysis_definition_sha256=analysis_definition_fingerprint(definition),
        cad_step=artifact_provenance_from_file(ArtifactKind.CAD_STEP, cad_step_path),
        mesh=artifact_provenance_from_file(ArtifactKind.MESH, mesh_path),
        solver_input=artifact_provenance_from_file(ArtifactKind.SOLVER_INPUT, solver_input_path),
        solver_dat=(
            None
            if solver_dat_path is None
            else artifact_provenance_from_file(ArtifactKind.SOLVER_DAT, solver_dat_path)
        ),
        solver_frd=(
            None
            if solver_frd_path is None
            else artifact_provenance_from_file(ArtifactKind.SOLVER_FRD, solver_frd_path)
        ),
        gmsh=ToolProvenance("Gmsh", gmsh_version),
        calculix=ToolProvenance("CalculiX", calculix_version),
        postprocessor=PostprocessorProvenance(
            ENGINEERING_EVIDENCE_POSTPROCESSOR_ID,
            ENGINEERING_EVIDENCE_POSTPROCESSOR_VERSION,
        ),
    )


def _artifact_to_dict(
    artifact: ArtifactProvenance | None, *, include_local_paths: bool
) -> dict | None:
    if artifact is None:
        return None
    record = {
        "kind": artifact.kind.value,
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
    }
    if include_local_paths:
        record["local_path"] = artifact.local_path
    return record


def analysis_provenance_to_dict(
    provenance: AnalysisProvenance, *, include_local_paths: bool = True
) -> dict:
    """Return stable JSON-compatible provenance; paths can be excluded from identity views."""
    if not isinstance(provenance, AnalysisProvenance):
        raise TypeError("provenance must be AnalysisProvenance")
    return {
        "model_version_reference": provenance.model_version.value,
        "analysis_definition_sha256": provenance.analysis_definition_sha256,
        "artifacts": {
            "cad_step": _artifact_to_dict(
                provenance.cad_step, include_local_paths=include_local_paths
            ),
            "mesh": _artifact_to_dict(provenance.mesh, include_local_paths=include_local_paths),
            "solver_input": _artifact_to_dict(
                provenance.solver_input, include_local_paths=include_local_paths
            ),
            "solver_dat": _artifact_to_dict(
                provenance.solver_dat, include_local_paths=include_local_paths
            ),
            "solver_frd": _artifact_to_dict(
                provenance.solver_frd, include_local_paths=include_local_paths
            ),
        },
        "tools": {
            "gmsh": {
                "identifier": provenance.gmsh.identifier,
                "version": provenance.gmsh.version,
            },
            "calculix": {
                "identifier": provenance.calculix.identifier,
                "version": provenance.calculix.version,
            },
        },
        "postprocessor": {
            "identifier": provenance.postprocessor.identifier,
            "version": provenance.postprocessor.version,
        },
    }
