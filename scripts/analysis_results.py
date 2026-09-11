"""Immutable, solver-neutral engineering summaries for completed analyses."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Mapping

from engineering_domain import AnalysisDefinition, ModelVersionReference
from engineering_postprocessing import vector_magnitude, von_mises_stress_pa
from numerical_results import (
    IntegrationPointStress,
    NodalDisplacement,
    NumericalResult,
    StressTensor,
    Vector3,
)


class AnalysisResultBuildError(RuntimeError):
    """Raised when required objective evidence cannot be summarized."""


def _positive_integer(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonempty")
    return value.strip()


@dataclass(frozen=True)
class EvidenceWarning:
    """Objective warning with a stable machine-readable code."""

    code: str
    message: str

    def __post_init__(self) -> None:
        code = _required_text(self.code, "warning code")
        if re.fullmatch(r"[a-z][a-z0-9_]*", code) is None:
            raise ValueError("warning code must use lower snake case")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", _required_text(self.message, "warning message"))


@dataclass(frozen=True)
class ResolvedAnalysisContext:
    """Small solver-neutral execution facts needed to build result evidence."""

    node_count: int
    element_count: int
    integrated_applied_resultant_n: Vector3
    warnings: tuple[EvidenceWarning, ...] = ()

    def __post_init__(self) -> None:
        _positive_integer(self.node_count, "node count")
        _positive_integer(self.element_count, "element count")
        if not isinstance(self.integrated_applied_resultant_n, Vector3):
            raise TypeError("integrated applied resultant must be a Vector3")
        warnings = tuple(self.warnings)
        if any(not isinstance(item, EvidenceWarning) for item in warnings):
            raise TypeError("resolved warnings must be EvidenceWarning values")
        object.__setattr__(self, "warnings", warnings)


@dataclass(frozen=True)
class MeshSummary:
    """Compact resolved mesh evidence; the complete mesh remains an artifact."""

    node_count: int
    element_count: int
    element_type: str
    characteristic_size_m: float

    def __post_init__(self) -> None:
        _positive_integer(self.node_count, "node count")
        _positive_integer(self.element_count, "element count")
        object.__setattr__(
            self, "element_type", _required_text(self.element_type, "element type")
        )
        if not math.isfinite(self.characteristic_size_m) or self.characteristic_size_m <= 0.0:
            raise ValueError("characteristic mesh size must be positive and finite")


@dataclass(frozen=True)
class DisplacementSummary:
    """Global displacement diagnostic, not a universal engineering QoI."""

    global_maximum_magnitude_m: float
    node_id: int
    vector_m: Vector3
    location_m: Vector3 | None = None

    def __post_init__(self) -> None:
        _positive_integer(self.node_id, "maximum-displacement node ID")
        if not math.isfinite(self.global_maximum_magnitude_m) or self.global_maximum_magnitude_m < 0:
            raise ValueError("maximum displacement magnitude must be finite and nonnegative")
        if not isinstance(self.vector_m, Vector3):
            raise TypeError("maximum displacement vector must be a Vector3")
        if self.location_m is not None and not isinstance(self.location_m, Vector3):
            raise TypeError("maximum displacement location must be a Vector3")
        if not math.isclose(
            self.global_maximum_magnitude_m,
            vector_magnitude(self.vector_m),
            rel_tol=1.0e-15,
            abs_tol=0.0,
        ):
            raise ValueError("maximum displacement magnitude must match its vector")


@dataclass(frozen=True)
class EquilibriumEvidence:
    """Force-resultant balance without an embedded acceptance tolerance."""

    applied_resultant_n: Vector3
    reaction_resultant_n: Vector3
    imbalance_n: Vector3
    residual_magnitude_n: float
    relative_imbalance: float | None

    def __post_init__(self) -> None:
        for value in (self.applied_resultant_n, self.reaction_resultant_n, self.imbalance_n):
            if not isinstance(value, Vector3):
                raise TypeError("equilibrium resultants and imbalance must be Vector3 values")
        if not math.isfinite(self.residual_magnitude_n) or self.residual_magnitude_n < 0.0:
            raise ValueError("equilibrium residual magnitude must be finite and nonnegative")
        if self.relative_imbalance is not None and (
            not math.isfinite(self.relative_imbalance) or self.relative_imbalance < 0.0
        ):
            raise ValueError("relative imbalance must be finite and nonnegative when defined")
        expected_imbalance = Vector3(
            self.applied_resultant_n.x + self.reaction_resultant_n.x,
            self.applied_resultant_n.y + self.reaction_resultant_n.y,
            self.applied_resultant_n.z + self.reaction_resultant_n.z,
        )
        if self.imbalance_n != expected_imbalance:
            raise ValueError("equilibrium imbalance must equal applied plus reaction")
        expected_residual = vector_magnitude(self.imbalance_n)
        if not math.isclose(
            self.residual_magnitude_n, expected_residual, rel_tol=1.0e-15, abs_tol=0.0
        ):
            raise ValueError("equilibrium residual magnitude must match its imbalance vector")
        denominator = max(
            vector_magnitude(self.applied_resultant_n),
            vector_magnitude(self.reaction_resultant_n),
        )
        expected_relative = None if denominator == 0.0 else expected_residual / denominator
        if self.relative_imbalance != expected_relative:
            raise ValueError("relative imbalance must use the larger resultant magnitude")


@dataclass(frozen=True)
class GlobalRawMaximumVonMises:
    """Traceable raw integration-point peak; not a relevant-design-stress policy."""

    von_mises_pa: float
    element_id: int
    integration_point: int
    stress_pa: StressTensor
    location_m: Vector3 | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.von_mises_pa) or self.von_mises_pa < 0.0:
            raise ValueError("von Mises stress must be finite and nonnegative")
        _positive_integer(self.element_id, "peak-stress element ID")
        _positive_integer(self.integration_point, "peak-stress integration-point identity")
        if not isinstance(self.stress_pa, StressTensor):
            raise TypeError("peak stress tensor must be a StressTensor")
        if self.location_m is not None and not isinstance(self.location_m, Vector3):
            raise TypeError("peak stress location must be a Vector3")
        if not math.isclose(
            self.von_mises_pa,
            von_mises_stress_pa(self.stress_pa),
            rel_tol=1.0e-15,
            abs_tol=0.0,
        ):
            raise ValueError("peak von Mises value must match its stress tensor")


@dataclass(frozen=True)
class StressSummary:
    """Objective global statistics for unaveraged integration-point stresses."""

    global_raw_max_von_mises: GlobalRawMaximumVonMises
    representation: str = field(default="raw_integration_point_cauchy_stress", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.global_raw_max_von_mises, GlobalRawMaximumVonMises):
            raise TypeError("stress summary peak must be GlobalRawMaximumVonMises")


@dataclass(frozen=True)
class AnalysisResult:
    """Compact completed engineering evidence, distinct from raw numerical fields."""

    model_version: ModelVersionReference
    mesh: MeshSummary
    displacement: DisplacementSummary
    equilibrium: EquilibriumEvidence
    stress: StressSummary
    warnings: tuple[EvidenceWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.model_version, ModelVersionReference):
            raise TypeError("analysis result model version must be a ModelVersionReference")
        if not isinstance(self.mesh, MeshSummary):
            raise TypeError("analysis result mesh must be a MeshSummary")
        if not isinstance(self.displacement, DisplacementSummary):
            raise TypeError("analysis result displacement must be a DisplacementSummary")
        if not isinstance(self.equilibrium, EquilibriumEvidence):
            raise TypeError("analysis result equilibrium must be EquilibriumEvidence")
        if not isinstance(self.stress, StressSummary):
            raise TypeError("analysis result stress must be a StressSummary")
        warnings = tuple(self.warnings)
        if any(not isinstance(item, EvidenceWarning) for item in warnings):
            raise TypeError("analysis result warnings must be EvidenceWarning values")
        object.__setattr__(self, "warnings", warnings)


def summarize_displacements(
    displacements: tuple[NodalDisplacement, ...],
    node_locations_m: Mapping[int, Vector3] | None = None,
) -> DisplacementSummary:
    """Select the exact largest magnitude, breaking ties by lowest node ID."""
    if not displacements:
        raise AnalysisResultBuildError("maximum displacement requires displacement data")
    selected = min(
        displacements,
        key=lambda item: (-vector_magnitude(item.displacement_m), item.node_id),
    )
    location = None if node_locations_m is None else node_locations_m.get(selected.node_id)
    return DisplacementSummary(
        global_maximum_magnitude_m=vector_magnitude(selected.displacement_m),
        node_id=selected.node_id,
        vector_m=selected.displacement_m,
        location_m=location,
    )


def build_equilibrium_evidence(
    applied_resultant_n: Vector3,
    reaction_resultant_n: Vector3,
) -> EquilibriumEvidence:
    """Add applied and reaction vectors without sign changes or hidden tolerances."""
    imbalance = Vector3(
        applied_resultant_n.x + reaction_resultant_n.x,
        applied_resultant_n.y + reaction_resultant_n.y,
        applied_resultant_n.z + reaction_resultant_n.z,
    )
    residual = vector_magnitude(imbalance)
    denominator = max(vector_magnitude(applied_resultant_n), vector_magnitude(reaction_resultant_n))
    relative = None if denominator == 0.0 else residual / denominator
    return EquilibriumEvidence(
        applied_resultant_n=applied_resultant_n,
        reaction_resultant_n=reaction_resultant_n,
        imbalance_n=imbalance,
        residual_magnitude_n=residual,
        relative_imbalance=relative,
    )


def summarize_stresses(
    stresses: tuple[IntegrationPointStress, ...],
    integration_point_locations_m: Mapping[tuple[int, int], Vector3] | None = None,
) -> StressSummary:
    """Select the exact raw-IP von Mises peak with deterministic identity ties."""
    if not stresses:
        raise AnalysisResultBuildError("global raw von Mises requires integration-point stress data")
    selected = min(
        stresses,
        key=lambda item: (
            -von_mises_stress_pa(item.stress_pa),
            item.element_id,
            item.integration_point,
        ),
    )
    location = selected.location_m
    if location is None and integration_point_locations_m is not None:
        location = integration_point_locations_m.get(
            (selected.element_id, selected.integration_point)
        )
    peak = GlobalRawMaximumVonMises(
        von_mises_pa=von_mises_stress_pa(selected.stress_pa),
        element_id=selected.element_id,
        integration_point=selected.integration_point,
        stress_pa=selected.stress_pa,
        location_m=location,
    )
    return StressSummary(global_raw_max_von_mises=peak)


def build_analysis_result(
    definition: AnalysisDefinition,
    numerical_result: NumericalResult,
    context: ResolvedAnalysisContext,
    *,
    node_locations_m: Mapping[int, Vector3] | None = None,
    integration_point_locations_m: Mapping[tuple[int, int], Vector3] | None = None,
) -> AnalysisResult:
    """Build compact objective evidence without solver syntax or benchmark references."""
    if numerical_result.reaction_resultant_n is None:
        raise AnalysisResultBuildError("equilibrium evidence requires a reaction resultant")
    return AnalysisResult(
        model_version=definition.model_version,
        mesh=MeshSummary(
            node_count=context.node_count,
            element_count=context.element_count,
            element_type=definition.mesh.element_type.value,
            characteristic_size_m=definition.mesh.characteristic_size_m,
        ),
        displacement=summarize_displacements(
            numerical_result.displacements, node_locations_m
        ),
        equilibrium=build_equilibrium_evidence(
            context.integrated_applied_resultant_n,
            numerical_result.reaction_resultant_n,
        ),
        stress=summarize_stresses(
            numerical_result.integration_point_stresses,
            integration_point_locations_m,
        ),
        warnings=context.warnings,
    )


def _vector_to_list(vector: Vector3 | None) -> list[float] | None:
    return None if vector is None else list(vector.as_tuple())


def analysis_result_to_dict(result: AnalysisResult) -> dict:
    """Return a stable JSON-compatible engineering-evidence representation."""
    peak = result.stress.global_raw_max_von_mises
    return {
        "unit_system": "SI",
        "model_version_reference": result.model_version.value,
        "mesh_summary": {
            "node_count": result.mesh.node_count,
            "element_count": result.mesh.element_count,
            "element_type": result.mesh.element_type,
            "characteristic_size_m": result.mesh.characteristic_size_m,
        },
        "displacement_summary": {
            "global_maximum_magnitude_m": result.displacement.global_maximum_magnitude_m,
            "node_id": result.displacement.node_id,
            "vector_m": _vector_to_list(result.displacement.vector_m),
            "location_m": _vector_to_list(result.displacement.location_m),
        },
        "equilibrium_evidence": {
            "applied_resultant_n": _vector_to_list(result.equilibrium.applied_resultant_n),
            "reaction_resultant_n": _vector_to_list(result.equilibrium.reaction_resultant_n),
            "imbalance_n": _vector_to_list(result.equilibrium.imbalance_n),
            "residual_magnitude_n": result.equilibrium.residual_magnitude_n,
            "relative_imbalance": result.equilibrium.relative_imbalance,
        },
        "stress_summary": {
            "representation": result.stress.representation,
            "global_raw_max_von_mises": {
                "von_mises_pa": peak.von_mises_pa,
                "element_id": peak.element_id,
                "integration_point": peak.integration_point,
                "location_m": _vector_to_list(peak.location_m),
                "stress_tensor_pa": {
                    "sigma_xx": peak.stress_pa.sigma_xx_pa,
                    "sigma_yy": peak.stress_pa.sigma_yy_pa,
                    "sigma_zz": peak.stress_pa.sigma_zz_pa,
                    "sigma_xy": peak.stress_pa.sigma_xy_pa,
                    "sigma_xz": peak.stress_pa.sigma_xz_pa,
                    "sigma_yz": peak.stress_pa.sigma_yz_pa,
                },
            },
        },
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in result.warnings
        ],
    }
