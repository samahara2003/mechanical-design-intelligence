"""Solver-neutral deterministic regional stress evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from analysis_results import MeshSummary
from engineering_postprocessing import von_mises_stress_pa
from numerical_results import NumericalResult, Vector3


class RegionalStressEvidenceError(RuntimeError):
    """Raised when a declared region cannot produce stress evidence."""


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


@dataclass(frozen=True)
class PhysicalCoordinateBoxRegion:
    """Engineering region defined by a closed global-coordinate box, never mesh IDs."""

    region_id: str
    region_version: str
    name: str
    minimum_m: Vector3
    maximum_m: Vector3
    feature_context: str
    constraint_relationship: str
    constraint_reference: str
    minimum_separation_from_constraint_m: float
    selection_method: str = field(
        default="raw_integration_point_location_inside_closed_global_coordinate_box",
        init=False,
    )

    def __post_init__(self) -> None:
        region_id = _required_text(self.region_id, "region ID")
        if re.fullmatch(r"[a-z][a-z0-9_]*", region_id) is None:
            raise ValueError("region ID must use lower snake case")
        object.__setattr__(self, "region_id", region_id)
        object.__setattr__(
            self, "region_version", _required_text(self.region_version, "region version")
        )
        object.__setattr__(self, "name", _required_text(self.name, "region name"))
        if not isinstance(self.minimum_m, Vector3) or not isinstance(self.maximum_m, Vector3):
            raise TypeError("region bounds must be Vector3 values")
        if any(
            lower >= upper
            for lower, upper in zip(self.minimum_m.as_tuple(), self.maximum_m.as_tuple())
        ):
            raise ValueError("every region maximum must exceed its minimum")
        object.__setattr__(
            self, "feature_context", _required_text(self.feature_context, "feature context")
        )
        if self.constraint_relationship not in {
            "on_simplified_constraint",
            "adjacent_to_simplified_constraint",
            "separated_from_immediate_constrained_surface",
        }:
            raise ValueError("unsupported constraint relationship")
        object.__setattr__(
            self,
            "constraint_reference",
            _required_text(self.constraint_reference, "constraint reference"),
        )
        if (
            not math.isfinite(self.minimum_separation_from_constraint_m)
            or self.minimum_separation_from_constraint_m < 0.0
        ):
            raise ValueError("constraint separation must be finite and nonnegative")

    def contains(self, location_m: Vector3) -> bool:
        if not isinstance(location_m, Vector3):
            raise TypeError("region selection requires a Vector3 location")
        return all(
            lower <= value <= upper
            for lower, value, upper in zip(
                self.minimum_m.as_tuple(),
                location_m.as_tuple(),
                self.maximum_m.as_tuple(),
            )
        )


@dataclass(frozen=True)
class RegionalStressEvidence:
    """Descriptive raw-IP von Mises evidence for one physical region and mesh."""

    evidence_version: str
    region: PhysicalCoordinateBoxRegion
    mesh_identity: str
    mesh: MeshSummary
    sample_count: int
    minimum_von_mises_pa: float
    arithmetic_mean_von_mises_pa: float
    maximum_von_mises_pa: float
    units: str = field(default="Pa", init=False)
    stress_representation: str = field(
        default="raw_integration_point_cauchy_stress_derived_von_mises", init=False
    )
    interpretation: str = field(default="diagnostic_evidence", init=False)
    scope: str = field(default="regional_stress_evidence_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_version", _required_text(self.evidence_version, "evidence version")
        )
        if not isinstance(self.region, PhysicalCoordinateBoxRegion):
            raise TypeError("regional stress evidence requires a physical region")
        object.__setattr__(
            self, "mesh_identity", _required_text(self.mesh_identity, "mesh identity")
        )
        if not isinstance(self.mesh, MeshSummary):
            raise TypeError("regional stress evidence requires a MeshSummary")
        if not isinstance(self.sample_count, int) or self.sample_count <= 0:
            raise ValueError("regional stress sample count must be positive")
        values = (
            self.minimum_von_mises_pa,
            self.arithmetic_mean_von_mises_pa,
            self.maximum_von_mises_pa,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("regional von Mises statistics must be finite and nonnegative")
        if not values[0] <= values[1] <= values[2]:
            raise ValueError("regional stress statistics must be ordered minimum/mean/maximum")


@dataclass(frozen=True)
class RegionalStressRefinementChange:
    """Observed adjacent regional-stress change without an acceptance threshold."""

    reference_mesh_size_m: float
    refined_mesh_size_m: float
    signed_mean_change_pa: float
    relative_mean_change: float | None
    signed_maximum_change_pa: float
    relative_maximum_change: float | None

    def __post_init__(self) -> None:
        values = (
            self.reference_mesh_size_m,
            self.refined_mesh_size_m,
            self.signed_mean_change_pa,
            self.signed_maximum_change_pa,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("regional stress refinement values must be finite")
        if not self.reference_mesh_size_m > self.refined_mesh_size_m > 0.0:
            raise ValueError("regional stress refinement meshes must be coarse to fine")
        for value in (self.relative_mean_change, self.relative_maximum_change):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError("relative regional stress changes must be nonnegative")


@dataclass(frozen=True)
class RegionalStressMeshStudy:
    """Exactly three regional summaries and their adjacent observed changes."""

    study_version: str
    region: PhysicalCoordinateBoxRegion
    evaluations: tuple[RegionalStressEvidence, ...]
    adjacent_changes: tuple[RegionalStressRefinementChange, ...]
    scope: str = field(default="three_level_regional_stress_observation_only", init=False)
    interpretation: str = field(default="diagnostic_evidence", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "study_version", _required_text(self.study_version, "study version")
        )
        evaluations = tuple(self.evaluations)
        changes = tuple(self.adjacent_changes)
        if len(evaluations) != 3 or len(changes) != 2:
            raise ValueError("regional stress mesh study requires three levels and two changes")
        if any(item.region != self.region for item in evaluations):
            raise ValueError("regional stress mesh study requires one physical region")
        sizes = tuple(item.mesh.characteristic_size_m for item in evaluations)
        if not sizes[0] > sizes[1] > sizes[2] > 0.0:
            raise ValueError("regional stress levels must be ordered coarse to fine")
        if any(
            changes[index].reference_mesh_size_m != sizes[index]
            or changes[index].refined_mesh_size_m != sizes[index + 1]
            for index in range(2)
        ):
            raise ValueError("regional stress changes must join adjacent mesh levels")
        for index, change in enumerate(changes):
            reference = evaluations[index]
            refined = evaluations[index + 1]
            expected_mean_change = (
                refined.arithmetic_mean_von_mises_pa
                - reference.arithmetic_mean_von_mises_pa
            )
            expected_maximum_change = (
                refined.maximum_von_mises_pa - reference.maximum_von_mises_pa
            )
            expected_relative_mean = (
                None
                if reference.arithmetic_mean_von_mises_pa == 0.0
                else abs(expected_mean_change) / reference.arithmetic_mean_von_mises_pa
            )
            expected_relative_maximum = (
                None
                if reference.maximum_von_mises_pa == 0.0
                else abs(expected_maximum_change) / reference.maximum_von_mises_pa
            )
            if (
                change.signed_mean_change_pa != expected_mean_change
                or change.relative_mean_change != expected_relative_mean
                or change.signed_maximum_change_pa != expected_maximum_change
                or change.relative_maximum_change != expected_relative_maximum
            ):
                raise ValueError("regional stress change must match its adjacent evaluations")
        object.__setattr__(self, "evaluations", evaluations)
        object.__setattr__(self, "adjacent_changes", changes)


def evaluate_regional_stress(
    region: PhysicalCoordinateBoxRegion,
    numerical_result: NumericalResult,
    *,
    mesh_identity: str,
    mesh: MeshSummary,
) -> RegionalStressEvidence:
    """Select every located raw stress point in the region and summarize von Mises."""
    if not isinstance(region, PhysicalCoordinateBoxRegion):
        raise TypeError("regional stress evaluation requires a physical region")
    if not isinstance(numerical_result, NumericalResult):
        raise TypeError("regional stress evaluation requires a NumericalResult")
    if any(item.location_m is None for item in numerical_result.integration_point_stresses):
        raise RegionalStressEvidenceError(
            "regional stress evaluation requires a location for every integration point"
        )
    values = tuple(
        von_mises_stress_pa(item.stress_pa)
        for item in numerical_result.integration_point_stresses
        if region.contains(item.location_m)
    )
    if not values:
        raise RegionalStressEvidenceError(
            f"region {region.region_id!r} contains no located integration-point stresses"
        )
    return RegionalStressEvidence(
        "1",
        region,
        mesh_identity,
        mesh,
        len(values),
        min(values),
        sum(values) / len(values),
        max(values),
    )


def _relative_change(reference: float, refined: float) -> float | None:
    return None if reference == 0.0 else abs(refined - reference) / abs(reference)


def build_regional_stress_mesh_study(
    evaluations: Sequence[RegionalStressEvidence],
) -> RegionalStressMeshStudy:
    ordered = tuple(evaluations)
    if len(ordered) != 3:
        raise ValueError("regional stress mesh study requires exactly three levels")
    region = ordered[0].region
    changes = tuple(
        RegionalStressRefinementChange(
            reference.mesh.characteristic_size_m,
            refined.mesh.characteristic_size_m,
            refined.arithmetic_mean_von_mises_pa - reference.arithmetic_mean_von_mises_pa,
            _relative_change(
                reference.arithmetic_mean_von_mises_pa,
                refined.arithmetic_mean_von_mises_pa,
            ),
            refined.maximum_von_mises_pa - reference.maximum_von_mises_pa,
            _relative_change(reference.maximum_von_mises_pa, refined.maximum_von_mises_pa),
        )
        for reference, refined in zip(ordered, ordered[1:])
    )
    return RegionalStressMeshStudy("1", region, ordered, changes)


def physical_coordinate_box_region_to_dict(region: PhysicalCoordinateBoxRegion) -> dict:
    return {
        "region_id": region.region_id,
        "region_version": region.region_version,
        "name": region.name,
        "selection_method": region.selection_method,
        "coordinate_system": "global_cartesian",
        "closed_bounds_m": {
            "minimum": list(region.minimum_m.as_tuple()),
            "maximum": list(region.maximum_m.as_tuple()),
        },
        "feature_context": region.feature_context,
        "constraint_context": {
            "relationship": region.constraint_relationship,
            "reference": region.constraint_reference,
            "minimum_separation_m": region.minimum_separation_from_constraint_m,
        },
    }


def regional_stress_evidence_to_dict(evidence: RegionalStressEvidence) -> dict:
    return {
        "evidence_version": evidence.evidence_version,
        "scope": evidence.scope,
        "interpretation": evidence.interpretation,
        "region": physical_coordinate_box_region_to_dict(evidence.region),
        "stress_representation": evidence.stress_representation,
        "mesh_identity": evidence.mesh_identity,
        "mesh_summary": {
            "node_count": evidence.mesh.node_count,
            "element_count": evidence.mesh.element_count,
            "element_type": evidence.mesh.element_type,
            "characteristic_size_m": evidence.mesh.characteristic_size_m,
        },
        "sample_count": evidence.sample_count,
        "von_mises_pa": {
            "minimum": evidence.minimum_von_mises_pa,
            "arithmetic_mean": evidence.arithmetic_mean_von_mises_pa,
            "maximum": evidence.maximum_von_mises_pa,
        },
        "units": evidence.units,
    }


def regional_stress_mesh_study_to_dict(study: RegionalStressMeshStudy) -> dict:
    return {
        "study_version": study.study_version,
        "scope": study.scope,
        "interpretation": study.interpretation,
        "region": physical_coordinate_box_region_to_dict(study.region),
        "ordered_mesh_evaluations": [
            regional_stress_evidence_to_dict(item) for item in study.evaluations
        ],
        "adjacent_observed_changes": [
            {
                "reference_mesh_size_m": item.reference_mesh_size_m,
                "refined_mesh_size_m": item.refined_mesh_size_m,
                "signed_mean_change_pa": item.signed_mean_change_pa,
                "relative_mean_change": item.relative_mean_change,
                "signed_maximum_change_pa": item.signed_maximum_change_pa,
                "relative_maximum_change": item.relative_maximum_change,
            }
            for item in study.adjacent_changes
        ],
        "change_semantics": "observed adjacent changes only; no acceptance threshold",
    }


def regional_stress_mesh_study_to_json(study: RegionalStressMeshStudy) -> str:
    return json.dumps(
        regional_stress_mesh_study_to_dict(study), sort_keys=True, separators=(",", ":")
    )
