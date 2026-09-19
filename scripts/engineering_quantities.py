"""Solver-neutral quantities, mesh-refinement evidence, and V1 error estimates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from analysis_results import AnalysisResult, GlobalRawMaximumVonMises, MeshSummary
from engineering_domain import (
    AnalysisDefinition,
    GeometryEntity,
    GeometrySelection,
    analysis_definition_to_dict,
)
from engineering_postprocessing import vector_magnitude
from numerical_results import NumericalResult, Vector3
from surface_load_mapping import triangle_area


class QuantityField(str, Enum):
    DISPLACEMENT = "displacement"


class QuantityComponent(str, Enum):
    UX = "UX"
    UY = "UY"
    UZ = "UZ"
    MAGNITUDE = "magnitude"


class QuantityAggregation(str, Enum):
    AVERAGE = "average"
    MAXIMUM = "maximum"


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


def _nonnegative_finite(value: float, label: str) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{label} must be finite and nonnegative")


@dataclass(frozen=True)
class QuantityOfInterest:
    """V1 regional-displacement intent tied to geometry, never mesh IDs."""

    quantity_id: str
    name: str
    target: GeometrySelection
    field: QuantityField
    component: QuantityComponent
    aggregation: QuantityAggregation
    units: str

    def __post_init__(self) -> None:
        quantity_id = _required_text(self.quantity_id, "quantity ID")
        if re.fullmatch(r"[a-z][a-z0-9_]*", quantity_id) is None:
            raise ValueError("quantity ID must use lower snake case")
        object.__setattr__(self, "quantity_id", quantity_id)
        object.__setattr__(self, "name", _required_text(self.name, "quantity name"))
        if not isinstance(self.target, GeometrySelection):
            raise TypeError("quantity target must be a GeometrySelection")
        if self.target.entity is not GeometryEntity.FACE:
            raise ValueError("regional displacement quantities currently require a face")
        if self.field is not QuantityField.DISPLACEMENT:
            raise ValueError("only displacement quantities are currently supported")
        if not isinstance(self.component, QuantityComponent):
            raise TypeError("quantity component must be a QuantityComponent")
        if not isinstance(self.aggregation, QuantityAggregation):
            raise TypeError("quantity aggregation must be a QuantityAggregation")
        if self.units != "m":
            raise ValueError("displacement quantity units must be metres ('m')")


@dataclass(frozen=True)
class QuantityEvaluation:
    """One evaluated quantity with resolved-region and mesh context."""

    quantity: QuantityOfInterest
    value: float
    units: str
    model_version_reference: str
    analysis_comparison_basis_sha256: str
    resolved_region_identity: str
    mesh_identity: str
    mesh: MeshSummary
    evaluation_method: str
    integrated_region_area_m2: float

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, QuantityOfInterest):
            raise TypeError("quantity evaluation requires a QuantityOfInterest")
        if not math.isfinite(self.value):
            raise ValueError("quantity evaluation value must be finite")
        if self.units != self.quantity.units:
            raise ValueError("quantity evaluation units must match the definition")
        for value, label in (
            (self.model_version_reference, "model version reference"),
            (self.resolved_region_identity, "resolved region identity"),
            (self.mesh_identity, "mesh identity"),
            (self.evaluation_method, "evaluation method"),
        ):
            _required_text(value, label)
        if re.fullmatch(r"[0-9a-f]{64}", self.analysis_comparison_basis_sha256) is None:
            raise ValueError("analysis comparison basis must be a SHA-256")
        if not isinstance(self.mesh, MeshSummary):
            raise TypeError("quantity evaluation mesh must be a MeshSummary")
        if not math.isfinite(self.integrated_region_area_m2) or self.integrated_region_area_m2 <= 0:
            raise ValueError("integrated quantity region area must be positive and finite")


@dataclass(frozen=True)
class MeshRefinementComparisonPolicy:
    """Explicit case-specific relative-change policy in the QoI's units."""

    policy_name: str
    policy_version: str
    relative_change_tolerance: float
    minimum_reference_magnitude: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _required_text(self.policy_name, "policy name"))
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        _nonnegative_finite(self.relative_change_tolerance, "relative-change tolerance")
        _nonnegative_finite(self.minimum_reference_magnitude, "minimum reference magnitude")


@dataclass(frozen=True)
class MeshRefinementComparison:
    """Two-mesh change evidence without a convergence claim."""

    reference: QuantityEvaluation
    refined: QuantityEvaluation
    absolute_change: float
    relative_change: float | None
    policy: MeshRefinementComparisonPolicy
    status: str
    scope: str = field(default="mesh_refinement_comparison_only", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reference, QuantityEvaluation) or not isinstance(
            self.refined, QuantityEvaluation
        ):
            raise TypeError("mesh comparison requires two quantity evaluations")
        _nonnegative_finite(self.absolute_change, "absolute quantity change")
        if self.relative_change is not None:
            _nonnegative_finite(self.relative_change, "relative quantity change")
        if not isinstance(self.policy, MeshRefinementComparisonPolicy):
            raise TypeError("mesh comparison must retain its explicit policy")
        if self.status not in {
            "within_tolerance",
            "outside_tolerance",
            "relative_change_not_applicable",
        }:
            raise ValueError("unsupported mesh-refinement comparison status")


@dataclass(frozen=True)
class RawStressRefinementDiagnostic:
    """Two raw stress peaks reported without an acceptance policy."""

    reference: GlobalRawMaximumVonMises
    refined: GlobalRawMaximumVonMises
    absolute_change_pa: float
    relative_change: float | None
    interpretation: str = field(default="diagnostic_only", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reference, GlobalRawMaximumVonMises) or not isinstance(
            self.refined, GlobalRawMaximumVonMises
        ):
            raise TypeError("raw stress diagnostic requires two raw stress peaks")
        _nonnegative_finite(self.absolute_change_pa, "absolute stress change")
        if self.relative_change is not None:
            _nonnegative_finite(self.relative_change, "relative stress change")


@dataclass(frozen=True)
class MeshConvergenceStudy:
    """Exactly three ordered mesh evaluations and their observed change trend."""

    study_id: str
    study_version: str
    quantity: QuantityOfInterest
    evaluations: tuple[QuantityEvaluation, ...]
    adjacent_comparisons: tuple[MeshRefinementComparison, ...]
    trend: str
    scope: str = field(default="three_level_mesh_trend_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _required_text(self.study_id, "study ID"))
        object.__setattr__(
            self, "study_version", _required_text(self.study_version, "study version")
        )
        evaluations = tuple(self.evaluations)
        comparisons = tuple(self.adjacent_comparisons)
        if len(evaluations) != 3:
            raise ValueError("V1 mesh convergence study requires exactly three levels")
        if len(comparisons) != 2:
            raise ValueError("three mesh levels require exactly two adjacent comparisons")
        if any(item.quantity != self.quantity for item in evaluations):
            raise ValueError("mesh convergence study requires the same quantity at every level")
        if any(
            comparisons[index].reference != evaluations[index]
            or comparisons[index].refined != evaluations[index + 1]
            for index in range(2)
        ):
            raise ValueError("mesh convergence comparisons must join adjacent ordered levels")
        if self.trend not in {"stabilizing", "not_stabilizing", "indeterminate"}:
            raise ValueError("unsupported three-level mesh trend")
        expected_trend = _three_level_trend(
            comparisons[0].absolute_change,
            comparisons[0].relative_change,
            comparisons[1].absolute_change,
            comparisons[1].relative_change,
        )
        if self.trend != expected_trend:
            raise ValueError("mesh trend must match its adjacent comparison evidence")
        object.__setattr__(self, "evaluations", evaluations)
        object.__setattr__(self, "adjacent_comparisons", comparisons)


@dataclass(frozen=True)
class RawStressLevelEvaluation:
    """One mesh's global raw integration-point peak and mesh context."""

    mesh_identity: str
    mesh: MeshSummary
    peak: GlobalRawMaximumVonMises

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "mesh_identity", _required_text(self.mesh_identity, "mesh identity")
        )
        if not isinstance(self.mesh, MeshSummary):
            raise TypeError("raw stress level mesh must be a MeshSummary")
        if not isinstance(self.peak, GlobalRawMaximumVonMises):
            raise TypeError("raw stress level peak must be GlobalRawMaximumVonMises")


@dataclass(frozen=True)
class RawStressMeshTrendDiagnostic:
    """Three raw stress peaks and adjacent changes without acceptance semantics."""

    study_version: str
    levels: tuple[RawStressLevelEvaluation, ...]
    adjacent_changes: tuple[RawStressRefinementDiagnostic, ...]
    observed_change_trend: str
    scope: str = field(default="three_level_raw_stress_trend_only", init=False)
    interpretation: str = field(default="diagnostic_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "study_version", _required_text(self.study_version, "study version")
        )
        levels = tuple(self.levels)
        changes = tuple(self.adjacent_changes)
        if len(levels) != 3 or len(changes) != 2:
            raise ValueError("raw stress trend requires three levels and two adjacent changes")
        if any(
            changes[index].reference != levels[index].peak
            or changes[index].refined != levels[index + 1].peak
            for index in range(2)
        ):
            raise ValueError("raw stress changes must join adjacent ordered levels")
        if self.observed_change_trend not in {
            "stabilizing",
            "not_stabilizing",
            "indeterminate",
        }:
            raise ValueError("unsupported raw stress numerical trend")
        expected_trend = _three_level_trend(
            changes[0].absolute_change_pa,
            changes[0].relative_change,
            changes[1].absolute_change_pa,
            changes[1].relative_change,
        )
        if self.observed_change_trend != expected_trend:
            raise ValueError("raw stress trend must match its adjacent change evidence")
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "adjacent_changes", changes)


@dataclass(frozen=True)
class DiscretizationErrorEstimatePolicy:
    """Explicit policy for the supported constant-ratio three-grid method."""

    policy_name: str
    policy_version: str
    safety_factor: float
    minimum_difference_magnitude: float
    minimum_relative_reference_magnitude: float
    refinement_ratio_relative_tolerance: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _required_text(self.policy_name, "policy name"))
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        if not math.isfinite(self.safety_factor) or self.safety_factor <= 0.0:
            raise ValueError("GCI safety factor must be positive and finite")
        _nonnegative_finite(
            self.minimum_difference_magnitude, "minimum successive-difference magnitude"
        )
        _nonnegative_finite(
            self.minimum_relative_reference_magnitude,
            "minimum relative-reference magnitude",
        )
        _nonnegative_finite(
            self.refinement_ratio_relative_tolerance,
            "refinement-ratio relative tolerance",
        )


@dataclass(frozen=True)
class DiscretizationEstimateEligibility:
    """Structured eligibility outcome; ineligible records carry no formal estimate."""

    status: str
    reason_code: str | None
    reason: str | None

    def __post_init__(self) -> None:
        if self.status not in {"eligible", "ineligible"}:
            raise ValueError("unsupported discretization-estimate eligibility status")
        if self.status == "eligible":
            if self.reason_code is not None or self.reason is not None:
                raise ValueError("eligible estimate must not carry an ineligibility reason")
        else:
            object.__setattr__(
                self, "reason_code", _required_text(self.reason_code, "ineligibility code")
            )
            object.__setattr__(
                self, "reason", _required_text(self.reason, "ineligibility reason")
            )


@dataclass(frozen=True)
class DiscretizationErrorEstimate:
    """Narrow three-grid Richardson/GCI evidence for one solver-neutral quantity."""

    estimate_version: str
    source_study_id: str
    quantity_id: str
    units: str
    policy: DiscretizationErrorEstimatePolicy
    eligibility: DiscretizationEstimateEligibility
    interpretation: str
    grid_sizes_m: tuple[float, float, float]
    refinement_ratios: tuple[float, float]
    fine_value: float
    observed_order: float | None
    richardson_extrapolated_value: float | None
    signed_fine_to_extrapolated_difference: float | None
    absolute_fine_to_extrapolated_difference: float | None
    relative_fine_to_extrapolated_difference: float | None
    coarse_medium_approximate_relative_error: float | None
    coarse_medium_gci: float | None
    approximate_relative_error: float | None
    fine_grid_gci: float | None
    scope: str = field(default="three_grid_discretization_estimate_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "estimate_version", _required_text(self.estimate_version, "estimate version")
        )
        object.__setattr__(
            self, "source_study_id", _required_text(self.source_study_id, "source study ID")
        )
        object.__setattr__(self, "quantity_id", _required_text(self.quantity_id, "quantity ID"))
        object.__setattr__(self, "units", _required_text(self.units, "estimate units"))
        if not isinstance(self.policy, DiscretizationErrorEstimatePolicy):
            raise TypeError("discretization estimate requires an explicit policy")
        if not isinstance(self.eligibility, DiscretizationEstimateEligibility):
            raise TypeError("discretization estimate requires eligibility evidence")
        if self.interpretation not in {"qoi_discretization_evidence", "diagnostic_only"}:
            raise ValueError("unsupported discretization evidence interpretation")
        sizes = tuple(self.grid_sizes_m)
        ratios = tuple(self.refinement_ratios)
        if len(sizes) != 3 or any(not math.isfinite(value) or value <= 0.0 for value in sizes):
            raise ValueError("discretization estimate requires three positive finite grid sizes")
        if len(ratios) != 2 or any(not math.isfinite(value) or value <= 1.0 for value in ratios):
            raise ValueError("discretization estimate requires two valid refinement ratios")
        if not math.isfinite(self.fine_value):
            raise ValueError("fine-grid value must be finite")
        formal_values = (
            self.observed_order,
            self.richardson_extrapolated_value,
            self.signed_fine_to_extrapolated_difference,
            self.absolute_fine_to_extrapolated_difference,
            self.coarse_medium_approximate_relative_error,
            self.coarse_medium_gci,
            self.approximate_relative_error,
            self.fine_grid_gci,
        )
        if self.eligibility.status == "ineligible" and any(
            value is not None for value in (*formal_values, self.relative_fine_to_extrapolated_difference)
        ):
            raise ValueError("ineligible study must not carry formal numerical estimates")
        if self.eligibility.status == "eligible":
            if any(value is None or not math.isfinite(value) for value in formal_values):
                raise ValueError("eligible study requires finite formal numerical estimates")
            if self.observed_order <= 0.0 or self.fine_grid_gci < 0.0:
                raise ValueError("eligible estimate requires positive order and nonnegative GCI")
            _nonnegative_finite(
                self.absolute_fine_to_extrapolated_difference,
                "absolute fine-to-extrapolated difference",
            )
            _nonnegative_finite(
                self.coarse_medium_approximate_relative_error,
                "coarse/medium approximate relative error",
            )
            _nonnegative_finite(self.coarse_medium_gci, "coarse/medium GCI")
            _nonnegative_finite(self.approximate_relative_error, "approximate relative error")
            if self.relative_fine_to_extrapolated_difference is not None:
                _nonnegative_finite(
                    self.relative_fine_to_extrapolated_difference,
                    "relative fine-to-extrapolated difference",
                )
        object.__setattr__(self, "grid_sizes_m", sizes)
        object.__setattr__(self, "refinement_ratios", ratios)


@dataclass(frozen=True)
class AsymptoticConsistencyPolicy:
    """Explicit policy for the V1 adjacent-GCI consistency relation."""

    policy_name: str
    policy_version: str
    formula_identity: str
    target_ratio: float
    allowable_absolute_deviation: float
    minimum_denominator: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _required_text(self.policy_name, "policy name"))
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        object.__setattr__(
            self, "formula_identity", _required_text(self.formula_identity, "formula identity")
        )
        if self.formula_identity != "gci_32_over_r_to_p_gci_21":
            raise ValueError("unsupported asymptotic-consistency formula identity")
        if not math.isfinite(self.target_ratio) or self.target_ratio <= 0.0:
            raise ValueError("asymptotic-consistency target ratio must be positive and finite")
        _nonnegative_finite(
            self.allowable_absolute_deviation,
            "asymptotic-consistency allowable absolute deviation",
        )
        _nonnegative_finite(
            self.minimum_denominator,
            "asymptotic-consistency minimum denominator",
        )


@dataclass(frozen=True)
class AsymptoticConsistencyEvidence:
    """Deterministic check of the adjacent-GCI asymptotic relation."""

    evidence_version: str
    source_study_id: str
    quantity_id: str
    interpretation: str
    policy: AsymptoticConsistencyPolicy
    status: str
    reason_code: str | None
    gci_32: float | None
    gci_21: float | None
    refinement_ratio: float | None
    observed_order: float | None
    consistency_ratio: float | None
    absolute_deviation: float | None
    scope: str = field(default="adjacent_gci_asymptotic_consistency_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_version", _required_text(self.evidence_version, "evidence version")
        )
        object.__setattr__(
            self, "source_study_id", _required_text(self.source_study_id, "source study ID")
        )
        object.__setattr__(self, "quantity_id", _required_text(self.quantity_id, "quantity ID"))
        if self.interpretation not in {"qoi_discretization_evidence", "diagnostic_only"}:
            raise ValueError("unsupported asymptotic-consistency interpretation")
        if not isinstance(self.policy, AsymptoticConsistencyPolicy):
            raise TypeError("asymptotic-consistency evidence requires an explicit policy")
        if self.status not in {"consistent", "outside_tolerance", "not_applicable"}:
            raise ValueError("unsupported asymptotic-consistency status")
        values = (
            self.gci_32,
            self.gci_21,
            self.refinement_ratio,
            self.observed_order,
            self.consistency_ratio,
            self.absolute_deviation,
        )
        if self.status == "not_applicable":
            object.__setattr__(
                self, "reason_code", _required_text(self.reason_code, "not-applicable reason")
            )
            if any(value is not None for value in values):
                raise ValueError("not-applicable consistency evidence must not carry GCI results")
        else:
            if self.reason_code is not None:
                raise ValueError("applicable consistency evidence must not carry a reason code")
            if any(value is None or not math.isfinite(value) for value in values):
                raise ValueError("applicable consistency evidence requires finite numerical values")
            if self.gci_32 < 0.0 or self.gci_21 < 0.0:
                raise ValueError("GCI values must be nonnegative")
            if self.refinement_ratio <= 1.0 or self.observed_order <= 0.0:
                raise ValueError("consistency evidence requires valid ratio and observed order")
            _nonnegative_finite(self.absolute_deviation, "consistency-ratio deviation")
            expected_deviation = abs(self.consistency_ratio - self.policy.target_ratio)
            if self.absolute_deviation != expected_deviation:
                raise ValueError("consistency-ratio deviation must match the policy target")
            expected_status = (
                "consistent"
                if expected_deviation <= self.policy.allowable_absolute_deviation
                else "outside_tolerance"
            )
            if self.status != expected_status:
                raise ValueError("consistency status must match its numerical evidence")


def quantity_of_interest_to_dict(quantity: QuantityOfInterest) -> dict:
    return {
        "quantity_id": quantity.quantity_id,
        "name": quantity.name,
        "target": {
            "region_name": quantity.target.region_name,
            "entity": quantity.target.entity.value,
        },
        "field": quantity.field.value,
        "component": quantity.component.value,
        "aggregation": quantity.aggregation.value,
        "units": quantity.units,
    }


def quantity_of_interest_to_json(quantity: QuantityOfInterest) -> str:
    return json.dumps(
        quantity_of_interest_to_dict(quantity),
        sort_keys=True,
        separators=(",", ":"),
    )


def _analysis_comparison_basis_sha256(definition: AnalysisDefinition) -> str:
    snapshot = analysis_definition_to_dict(definition)
    mesh = dict(snapshot["mesh_config"])
    mesh.pop("characteristic_size_m")
    snapshot["mesh_config"] = mesh
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _component(vector: Vector3, component: QuantityComponent) -> float:
    if component is QuantityComponent.UX:
        return vector.x
    if component is QuantityComponent.UY:
        return vector.y
    if component is QuantityComponent.UZ:
        return vector.z
    return vector_magnitude(vector)


def evaluate_regional_displacement(
    quantity: QuantityOfInterest,
    definition: AnalysisDefinition,
    analysis_result: AnalysisResult,
    numerical_result: NumericalResult,
    *,
    resolved_region_identity: str,
    mesh_identity: str,
    faces: Sequence[Mapping[str, object]],
    node_coordinates_m: Mapping[int, tuple[float, float, float]],
) -> QuantityEvaluation:
    """Evaluate a face displacement using transient resolved mesh entities."""
    if quantity.field is not QuantityField.DISPLACEMENT:
        raise ValueError("regional evaluator supports only displacement")
    if analysis_result.model_version != definition.model_version:
        raise ValueError("analysis result and definition model versions differ")
    if (
        analysis_result.mesh.element_type != definition.mesh.element_type.value
        or analysis_result.mesh.characteristic_size_m != definition.mesh.characteristic_size_m
    ):
        raise ValueError("analysis result and definition mesh configurations differ")
    displacements = {
        item.node_id: item.displacement_m for item in numerical_result.displacements
    }
    area_sum = 0.0
    weighted_sum = 0.0
    nodal_values: list[float] = []
    for face in faces:
        face_nodes = face.get("nodes")
        if not isinstance(face_nodes, (list, tuple)) or len(face_nodes) != 6:
            raise ValueError("regional C3D10 displacement requires six-node faces")
        node_ids = tuple(face_nodes)
        if any(not isinstance(node_id, int) for node_id in node_ids):
            raise TypeError("resolved face node IDs must be integers")
        if any(node_id not in node_coordinates_m or node_id not in displacements for node_id in node_ids):
            raise ValueError("resolved quantity region is missing coordinates or displacement data")
        area = triangle_area(*(node_coordinates_m[node_id] for node_id in node_ids[:3]))
        area_sum += area
        for node_id in node_ids[3:]:
            weighted_sum += (
                _component(displacements[node_id], quantity.component) * area / 3.0
            )
        nodal_values.extend(
            _component(displacements[node_id], quantity.component) for node_id in node_ids
        )
    if area_sum <= 0.0:
        raise ValueError("resolved quantity region must have positive area")
    if quantity.aggregation is QuantityAggregation.AVERAGE:
        value = weighted_sum / area_sum
        method = "three_point_c3d10_surface_quadrature"
    else:
        value = max(nodal_values)
        method = "resolved_surface_nodal_maximum"
    return QuantityEvaluation(
        quantity=quantity,
        value=value,
        units=quantity.units,
        model_version_reference=definition.model_version.value,
        analysis_comparison_basis_sha256=_analysis_comparison_basis_sha256(definition),
        resolved_region_identity=resolved_region_identity,
        mesh_identity=mesh_identity,
        mesh=analysis_result.mesh,
        evaluation_method=method,
        integrated_region_area_m2=area_sum,
    )


def quantity_evaluation_to_dict(evaluation: QuantityEvaluation) -> dict:
    return {
        "quantity": quantity_of_interest_to_dict(evaluation.quantity),
        "value": evaluation.value,
        "units": evaluation.units,
        "model_version_reference": evaluation.model_version_reference,
        "analysis_comparison_basis_sha256": evaluation.analysis_comparison_basis_sha256,
        "resolved_region_identity": evaluation.resolved_region_identity,
        "mesh_identity": evaluation.mesh_identity,
        "mesh_summary": {
            "node_count": evaluation.mesh.node_count,
            "element_count": evaluation.mesh.element_count,
            "element_type": evaluation.mesh.element_type,
            "characteristic_size_m": evaluation.mesh.characteristic_size_m,
        },
        "evaluation_method": evaluation.evaluation_method,
        "integrated_region_area_m2": evaluation.integrated_region_area_m2,
    }


def compare_mesh_refinement(
    reference: QuantityEvaluation,
    refined: QuantityEvaluation,
    policy: MeshRefinementComparisonPolicy,
) -> MeshRefinementComparison:
    """Compare compatible evaluations using a visible two-level policy."""
    if reference.quantity != refined.quantity:
        raise ValueError("mesh refinement comparison requires the same quantity")
    if reference.model_version_reference != refined.model_version_reference:
        raise ValueError("mesh refinement comparison requires the same model version")
    if reference.analysis_comparison_basis_sha256 != refined.analysis_comparison_basis_sha256:
        raise ValueError("analyses differ by more than characteristic mesh size")
    if reference.resolved_region_identity != refined.resolved_region_identity:
        raise ValueError("mesh refinement comparison requires the same resolved region identity")
    if reference.units != refined.units:
        raise ValueError("mesh refinement comparison requires matching units")
    if refined.mesh.characteristic_size_m >= reference.mesh.characteristic_size_m:
        raise ValueError("refined evaluation must use a smaller characteristic mesh size")
    absolute_change = abs(refined.value - reference.value)
    if abs(reference.value) <= policy.minimum_reference_magnitude:
        relative_change = None
        status = "relative_change_not_applicable"
    else:
        relative_change = absolute_change / abs(reference.value)
        status = (
            "within_tolerance"
            if relative_change <= policy.relative_change_tolerance
            else "outside_tolerance"
        )
    return MeshRefinementComparison(
        reference,
        refined,
        absolute_change,
        relative_change,
        policy,
        status,
    )


def mesh_refinement_comparison_to_dict(comparison: MeshRefinementComparison) -> dict:
    return {
        "scope": comparison.scope,
        "status": comparison.status,
        "policy": {
            "name": comparison.policy.policy_name,
            "version": comparison.policy.policy_version,
            "relative_change_tolerance": comparison.policy.relative_change_tolerance,
            "minimum_reference_magnitude": comparison.policy.minimum_reference_magnitude,
            "minimum_reference_units": comparison.reference.units,
        },
        "reference_evaluation": quantity_evaluation_to_dict(comparison.reference),
        "refined_evaluation": quantity_evaluation_to_dict(comparison.refined),
        "absolute_change": comparison.absolute_change,
        "relative_change": comparison.relative_change,
        "units": comparison.reference.units,
    }


def _three_level_trend(
    first_absolute: float,
    first_relative: float | None,
    second_absolute: float,
    second_relative: float | None,
) -> str:
    """Classify only whether both successive change measures decrease."""
    if first_relative is None or second_relative is None:
        return "indeterminate"
    if second_absolute < first_absolute and second_relative < first_relative:
        return "stabilizing"
    return "not_stabilizing"


def build_mesh_convergence_study(
    study_id: str,
    study_version: str,
    evaluations: Sequence[QuantityEvaluation],
    policy: MeshRefinementComparisonPolicy,
) -> MeshConvergenceStudy:
    """Build the narrow three-level trend from ordered coarse-to-fine values."""
    ordered = tuple(evaluations)
    if len(ordered) != 3:
        raise ValueError("V1 mesh convergence study requires exactly three levels")
    comparisons = (
        compare_mesh_refinement(ordered[0], ordered[1], policy),
        compare_mesh_refinement(ordered[1], ordered[2], policy),
    )
    trend = _three_level_trend(
        comparisons[0].absolute_change,
        comparisons[0].relative_change,
        comparisons[1].absolute_change,
        comparisons[1].relative_change,
    )
    return MeshConvergenceStudy(
        study_id,
        study_version,
        ordered[0].quantity,
        ordered,
        comparisons,
        trend,
    )


def mesh_convergence_study_to_dict(study: MeshConvergenceStudy) -> dict:
    """Serialize the deterministic three-level contract without stronger claims."""
    return {
        "study_id": study.study_id,
        "study_version": study.study_version,
        "scope": study.scope,
        "quantity": quantity_of_interest_to_dict(study.quantity),
        "ordered_mesh_evaluations": [
            quantity_evaluation_to_dict(item) for item in study.evaluations
        ],
        "adjacent_refinement_comparisons": [
            mesh_refinement_comparison_to_dict(item)
            for item in study.adjacent_comparisons
        ],
        "trend": study.trend,
        "trend_semantics": {
            "stabilizing": (
                "both absolute and relative changes strictly decrease from the first "
                "adjacent pair to the second"
            ),
            "not_stabilizing": (
                "both relative changes are meaningful and at least one successive "
                "change measure does not strictly decrease"
            ),
            "indeterminate": (
                "at least one relative change is unavailable under the explicit "
                "minimum-reference policy"
            ),
        },
    }


def _ineligible_discretization_estimate(
    *,
    source_study_id: str,
    quantity_id: str,
    units: str,
    policy: DiscretizationErrorEstimatePolicy,
    sizes: tuple[float, float, float],
    ratios: tuple[float, float],
    fine_value: float,
    reason_code: str,
    reason: str,
    interpretation: str,
) -> DiscretizationErrorEstimate:
    return DiscretizationErrorEstimate(
        "1",
        source_study_id,
        quantity_id,
        units,
        policy,
        DiscretizationEstimateEligibility("ineligible", reason_code, reason),
        interpretation,
        sizes,
        ratios,
        fine_value,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _estimate_three_grid_values(
    *,
    source_study_id: str,
    quantity_id: str,
    units: str,
    values: tuple[float, float, float],
    sizes: tuple[float, float, float],
    policy: DiscretizationErrorEstimatePolicy,
    interpretation: str,
) -> DiscretizationErrorEstimate:
    """Apply the V1 constant-ratio formula to grid-3/grid-2/grid-1 values.

    With coarse/medium/fine values phi3, phi2, phi1 and common ratio r,
    p = ln(abs((phi2-phi3)/(phi1-phi2))) / ln(r).
    """
    if not isinstance(policy, DiscretizationErrorEstimatePolicy):
        raise TypeError("formal estimate requires a DiscretizationErrorEstimatePolicy")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("three-grid quantity values must be finite")
    if any(not math.isfinite(value) or value <= 0.0 for value in sizes):
        raise ValueError("grid sizes must be positive and finite")
    h3, h2, h1 = sizes
    if not h3 > h2 > h1:
        raise ValueError("grid sizes must be ordered coarse, medium, fine")
    r32, r21 = h3 / h2, h2 / h1
    ratios = (r32, r21)
    if not math.isclose(
        r32,
        r21,
        rel_tol=policy.refinement_ratio_relative_tolerance,
        abs_tol=0.0,
    ):
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=values[2],
            reason_code="nonuniform_refinement_ratio_unsupported",
            reason="V1 supports only a constant characteristic-size refinement ratio",
            interpretation=interpretation,
        )
    refinement_ratio = (r32 + r21) / 2.0
    ratios = (refinement_ratio, refinement_ratio)
    phi3, phi2, phi1 = values
    delta32 = phi2 - phi3
    delta21 = phi1 - phi2
    if (
        abs(delta32) <= policy.minimum_difference_magnitude
        or abs(delta21) <= policy.minimum_difference_magnitude
    ):
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=phi1,
            reason_code="successive_difference_below_policy_minimum",
            reason="at least one successive difference is too small for a meaningful V1 estimate",
            interpretation=interpretation,
        )
    if delta32 * delta21 <= 0.0:
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=phi1,
            reason_code="nonmonotonic_sequence",
            reason="successive solution differences do not have a common sign",
            interpretation=interpretation,
        )
    observed_order = math.log(abs(delta32 / delta21)) / math.log(refinement_ratio)
    if not math.isfinite(observed_order) or observed_order <= 0.0:
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=phi1,
            reason_code="observed_order_not_positive",
            reason="successive changes do not decrease, so the V1 observed order is not positive",
            interpretation=interpretation,
        )
    denominator = refinement_ratio**observed_order - 1.0
    if not math.isfinite(denominator) or denominator <= 0.0:
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=phi1,
            reason_code="invalid_extrapolation_denominator",
            reason="the V1 Richardson denominator is not positive and finite",
            interpretation=interpretation,
        )
    extrapolated = phi1 + delta21 / denominator
    signed_difference = extrapolated - phi1
    absolute_difference = abs(signed_difference)
    relative_difference = (
        None
        if abs(extrapolated) <= policy.minimum_relative_reference_magnitude
        else absolute_difference / abs(extrapolated)
    )
    if abs(phi1) <= policy.minimum_relative_reference_magnitude:
        return _ineligible_discretization_estimate(
            source_study_id=source_study_id,
            quantity_id=quantity_id,
            units=units,
            policy=policy,
            sizes=sizes,
            ratios=ratios,
            fine_value=phi1,
            reason_code="fine_value_below_relative_reference_minimum",
            reason="fine-grid magnitude is too small for the relative-error and GCI definitions",
            interpretation=interpretation,
        )
    coarse_medium_approximate_relative_error = abs(delta32 / phi2)
    coarse_medium_gci = (
        policy.safety_factor * coarse_medium_approximate_relative_error / denominator
    )
    approximate_relative_error = abs(delta21 / phi1)
    fine_grid_gci = policy.safety_factor * approximate_relative_error / denominator
    return DiscretizationErrorEstimate(
        "1",
        source_study_id,
        quantity_id,
        units,
        policy,
        DiscretizationEstimateEligibility("eligible", None, None),
        interpretation,
        sizes,
        ratios,
        phi1,
        observed_order,
        extrapolated,
        signed_difference,
        absolute_difference,
        relative_difference,
        coarse_medium_approximate_relative_error,
        coarse_medium_gci,
        approximate_relative_error,
        fine_grid_gci,
    )


def estimate_mesh_discretization_error(
    study: MeshConvergenceStudy,
    policy: DiscretizationErrorEstimatePolicy,
) -> DiscretizationErrorEstimate:
    """Estimate V1 discretization error from a validated three-level QoI study."""
    if not isinstance(study, MeshConvergenceStudy):
        raise TypeError("formal estimate requires a MeshConvergenceStudy")
    return _estimate_three_grid_values(
        source_study_id=study.study_id,
        quantity_id=study.quantity.quantity_id,
        units=study.quantity.units,
        values=tuple(item.value for item in study.evaluations),
        sizes=tuple(item.mesh.characteristic_size_m for item in study.evaluations),
        policy=policy,
        interpretation="qoi_discretization_evidence",
    )


def assess_raw_stress_discretization_eligibility(
    study: RawStressMeshTrendDiagnostic,
    policy: DiscretizationErrorEstimatePolicy,
) -> DiscretizationErrorEstimate:
    """Apply the same eligibility gate to diagnostic raw peak stress evidence."""
    if not isinstance(study, RawStressMeshTrendDiagnostic):
        raise TypeError("raw stress eligibility requires its three-level diagnostic")
    return _estimate_three_grid_values(
        source_study_id="controlled_bracket_global_raw_von_mises_three_level",
        quantity_id="global_raw_integration_point_von_mises",
        units="Pa",
        values=tuple(level.peak.von_mises_pa for level in study.levels),
        sizes=tuple(level.mesh.characteristic_size_m for level in study.levels),
        policy=policy,
        interpretation="diagnostic_only",
    )


def discretization_error_estimate_to_dict(
    estimate: DiscretizationErrorEstimate,
) -> dict:
    """Canonically shaped serialization; fractions are never implicit percentages."""
    return {
        "estimate_version": estimate.estimate_version,
        "scope": estimate.scope,
        "source_study_id": estimate.source_study_id,
        "quantity_id": estimate.quantity_id,
        "units": estimate.units,
        "interpretation": estimate.interpretation,
        "eligibility": {
            "status": estimate.eligibility.status,
            "reason_code": estimate.eligibility.reason_code,
            "reason": estimate.eligibility.reason,
        },
        "policy": {
            "name": estimate.policy.policy_name,
            "version": estimate.policy.policy_version,
            "method": "constant_refinement_ratio_three_grid_richardson_gci",
            "safety_factor": estimate.policy.safety_factor,
            "minimum_difference_magnitude": estimate.policy.minimum_difference_magnitude,
            "minimum_difference_units": estimate.units,
            "minimum_relative_reference_magnitude": (
                estimate.policy.minimum_relative_reference_magnitude
            ),
            "minimum_relative_reference_units": estimate.units,
            "refinement_ratio_relative_tolerance": (
                estimate.policy.refinement_ratio_relative_tolerance
            ),
        },
        "ordering": "grid_3_coarse_grid_2_medium_grid_1_fine",
        "grid_sizes_m": {
            "grid_3_coarse": estimate.grid_sizes_m[0],
            "grid_2_medium": estimate.grid_sizes_m[1],
            "grid_1_fine": estimate.grid_sizes_m[2],
        },
        "refinement_ratios": {
            "r32": estimate.refinement_ratios[0],
            "r21": estimate.refinement_ratios[1],
        },
        "formula": {
            "observed_order": "ln(abs((phi2-phi3)/(phi1-phi2)))/ln(r)",
            "richardson": "phi1+(phi1-phi2)/(r^p-1)",
            "fine_grid_gci": "safety_factor*abs((phi1-phi2)/phi1)/(r^p-1)",
        },
        "fine_value": estimate.fine_value,
        "observed_order": estimate.observed_order,
        "richardson_extrapolated_value": estimate.richardson_extrapolated_value,
        "signed_fine_to_extrapolated_difference": (
            estimate.signed_fine_to_extrapolated_difference
        ),
        "absolute_fine_to_extrapolated_difference": (
            estimate.absolute_fine_to_extrapolated_difference
        ),
        "relative_fine_to_extrapolated_difference": (
            estimate.relative_fine_to_extrapolated_difference
        ),
        "coarse_medium_approximate_relative_error": (
            estimate.coarse_medium_approximate_relative_error
        ),
        "coarse_medium_gci": estimate.coarse_medium_gci,
        "approximate_relative_error": estimate.approximate_relative_error,
        "fine_grid_gci": estimate.fine_grid_gci,
        "gci_pair": {
            "gci_32": estimate.coarse_medium_gci,
            "gci_21": estimate.fine_grid_gci,
        },
        "relative_values_semantics": "dimensionless_fraction",
        "asymptotic_range_assessment": "not_established_v1",
        "gci_semantics": "estimated_qoi_discretization_uncertainty_under_method_assumptions",
    }


def build_asymptotic_consistency_evidence(
    estimate: DiscretizationErrorEstimate,
    policy: AsymptoticConsistencyPolicy,
) -> AsymptoticConsistencyEvidence:
    """Check GCI_32 / (r**p * GCI_21) against an explicit target."""
    if not isinstance(estimate, DiscretizationErrorEstimate):
        raise TypeError("consistency check requires a DiscretizationErrorEstimate")
    if not isinstance(policy, AsymptoticConsistencyPolicy):
        raise TypeError("consistency check requires an AsymptoticConsistencyPolicy")

    def not_applicable(reason_code: str) -> AsymptoticConsistencyEvidence:
        return AsymptoticConsistencyEvidence(
            "1",
            estimate.source_study_id,
            estimate.quantity_id,
            estimate.interpretation,
            policy,
            "not_applicable",
            reason_code,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    if estimate.eligibility.status != "eligible":
        return not_applicable("formal_discretization_estimate_ineligible")
    required = (
        estimate.coarse_medium_gci,
        estimate.fine_grid_gci,
        estimate.observed_order,
        *estimate.refinement_ratios,
    )
    if any(value is None or not math.isfinite(value) for value in required):
        return not_applicable("required_gci_evidence_unavailable")
    r32, r21 = estimate.refinement_ratios
    if r32 <= 1.0 or r21 <= 1.0 or not math.isclose(
        r32,
        r21,
        rel_tol=estimate.policy.refinement_ratio_relative_tolerance,
        abs_tol=0.0,
    ):
        return not_applicable("invalid_refinement_ratio")
    refinement_ratio = (r32 + r21) / 2.0
    try:
        denominator = (
            refinement_ratio**estimate.observed_order * estimate.fine_grid_gci
        )
    except OverflowError:
        return not_applicable("invalid_consistency_denominator")
    if not math.isfinite(denominator) or denominator <= policy.minimum_denominator:
        return not_applicable("invalid_consistency_denominator")
    consistency_ratio = estimate.coarse_medium_gci / denominator
    if not math.isfinite(consistency_ratio):
        return not_applicable("invalid_consistency_ratio")
    absolute_deviation = abs(consistency_ratio - policy.target_ratio)
    status = (
        "consistent"
        if absolute_deviation <= policy.allowable_absolute_deviation
        else "outside_tolerance"
    )
    return AsymptoticConsistencyEvidence(
        "1",
        estimate.source_study_id,
        estimate.quantity_id,
        estimate.interpretation,
        policy,
        status,
        None,
        estimate.coarse_medium_gci,
        estimate.fine_grid_gci,
        refinement_ratio,
        estimate.observed_order,
        consistency_ratio,
        absolute_deviation,
    )


def asymptotic_consistency_evidence_to_dict(
    evidence: AsymptoticConsistencyEvidence,
) -> dict:
    """Serialize adjacent-GCI consistency with explicit dimensionless semantics."""
    return {
        "evidence_version": evidence.evidence_version,
        "scope": evidence.scope,
        "source_study_id": evidence.source_study_id,
        "quantity_id": evidence.quantity_id,
        "interpretation": evidence.interpretation,
        "status": evidence.status,
        "reason_code": evidence.reason_code,
        "policy": {
            "name": evidence.policy.policy_name,
            "version": evidence.policy.policy_version,
            "formula_identity": evidence.policy.formula_identity,
            "target_ratio": evidence.policy.target_ratio,
            "allowable_absolute_deviation": evidence.policy.allowable_absolute_deviation,
            "minimum_denominator": evidence.policy.minimum_denominator,
            "units": "dimensionless_fraction",
        },
        "gci_32": evidence.gci_32,
        "gci_21": evidence.gci_21,
        "refinement_ratio": evidence.refinement_ratio,
        "observed_order": evidence.observed_order,
        "asymptotic_consistency_ratio": evidence.consistency_ratio,
        "absolute_deviation_from_target": evidence.absolute_deviation,
        "formula": "GCI_32/(r^p*GCI_21)",
        "status_semantics": {
            "consistent": (
                "the adjacent GCI pair satisfies only the configured numerical relation"
            ),
            "outside_tolerance": (
                "the adjacent GCI relation exceeds the configured absolute deviation"
            ),
            "not_applicable": "the required eligible finite GCI evidence is unavailable",
        },
    }


def compare_raw_stress_diagnostic(
    reference_result: AnalysisResult,
    refined_result: AnalysisResult,
) -> RawStressRefinementDiagnostic:
    reference = reference_result.stress.global_raw_max_von_mises
    refined = refined_result.stress.global_raw_max_von_mises
    absolute = abs(refined.von_mises_pa - reference.von_mises_pa)
    relative = None if reference.von_mises_pa == 0.0 else absolute / reference.von_mises_pa
    return RawStressRefinementDiagnostic(reference, refined, absolute, relative)


def raw_stress_diagnostic_to_dict(diagnostic: RawStressRefinementDiagnostic) -> dict:
    def peak(value: GlobalRawMaximumVonMises) -> dict:
        return {
            "von_mises_pa": value.von_mises_pa,
            "element_id": value.element_id,
            "integration_point": value.integration_point,
            "position_m": None if value.location_m is None else list(value.location_m.as_tuple()),
        }

    return {
        "interpretation": diagnostic.interpretation,
        "representation": "global_raw_integration_point_von_mises",
        "reference": peak(diagnostic.reference),
        "refined": peak(diagnostic.refined),
        "absolute_change_pa": diagnostic.absolute_change_pa,
        "relative_change": diagnostic.relative_change,
    }


def build_raw_stress_mesh_trend(
    results: Sequence[AnalysisResult],
    quantity_evaluations: Sequence[QuantityEvaluation],
    *,
    study_version: str = "1",
) -> RawStressMeshTrendDiagnostic:
    """Collect raw global peaks across the same three ordered mesh executions."""
    result_levels = tuple(results)
    evaluations = tuple(quantity_evaluations)
    if len(result_levels) != 3 or len(evaluations) != 3:
        raise ValueError("raw stress trend requires exactly three result/evaluation levels")
    model_versions = {item.model_version for item in result_levels}
    if len(model_versions) != 1:
        raise ValueError("raw stress trend requires one model version")
    if len({item.quantity for item in evaluations}) != 1:
        raise ValueError("raw stress trend requires one companion quantity")
    if len({item.analysis_comparison_basis_sha256 for item in evaluations}) != 1:
        raise ValueError("raw stress levels differ by more than characteristic mesh size")
    if any(
        result_levels[index].mesh != evaluations[index].mesh for index in range(3)
    ):
        raise ValueError("raw stress and quantity levels must use the same meshes")
    levels = tuple(
        RawStressLevelEvaluation(
            evaluations[index].mesh_identity,
            result_levels[index].mesh,
            result_levels[index].stress.global_raw_max_von_mises,
        )
        for index in range(3)
    )
    changes = (
        compare_raw_stress_diagnostic(result_levels[0], result_levels[1]),
        compare_raw_stress_diagnostic(result_levels[1], result_levels[2]),
    )
    trend = _three_level_trend(
        changes[0].absolute_change_pa,
        changes[0].relative_change,
        changes[1].absolute_change_pa,
        changes[1].relative_change,
    )
    return RawStressMeshTrendDiagnostic(study_version, levels, changes, trend)


def raw_stress_mesh_trend_to_dict(study: RawStressMeshTrendDiagnostic) -> dict:
    """Serialize raw stress changes with diagnostic-only interpretation."""
    return {
        "study_version": study.study_version,
        "scope": study.scope,
        "interpretation": study.interpretation,
        "ordered_mesh_levels": [
            {
                "mesh_identity": level.mesh_identity,
                "mesh_summary": {
                    "node_count": level.mesh.node_count,
                    "element_count": level.mesh.element_count,
                    "element_type": level.mesh.element_type,
                    "characteristic_size_m": level.mesh.characteristic_size_m,
                },
                "global_raw_maximum_von_mises": {
                    "von_mises_pa": level.peak.von_mises_pa,
                    "element_id": level.peak.element_id,
                    "integration_point": level.peak.integration_point,
                    "position_m": (
                        None
                        if level.peak.location_m is None
                        else list(level.peak.location_m.as_tuple())
                    ),
                },
            }
            for level in study.levels
        ],
        "adjacent_changes": [
            raw_stress_diagnostic_to_dict(item) for item in study.adjacent_changes
        ],
        "observed_change_trend": study.observed_change_trend,
        "trend_semantics": (
            "observed adjacent absolute and relative change magnitudes only; "
            "no acceptance or stronger inference"
        ),
    }
