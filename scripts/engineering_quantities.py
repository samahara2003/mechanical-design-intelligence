"""Solver-neutral quantities and two-level mesh-refinement evidence."""

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
