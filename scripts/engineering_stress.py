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
    maximum_location_m: Vector3
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
        if not isinstance(self.maximum_location_m, Vector3):
            raise TypeError("regional stress maximum location must be a Vector3")
        if not self.region.contains(self.maximum_location_m):
            raise ValueError("regional stress maximum location must be inside its region")


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


@dataclass(frozen=True)
class SpatialStressBand:
    """One controlled physical band along global X, never a mesh selection."""

    band_id: str
    band_version: str
    name: str
    lower_x_m: float
    upper_x_m: float
    upper_bound_inclusive: bool
    geometry_context: str
    coordinate_semantics: str = field(
        default="global_x_coordinate_proxy_not_shortest_feature_distance", init=False
    )

    def __post_init__(self) -> None:
        band_id = _required_text(self.band_id, "band ID")
        if re.fullmatch(r"[a-z][a-z0-9_]*", band_id) is None:
            raise ValueError("band ID must use lower snake case")
        object.__setattr__(self, "band_id", band_id)
        object.__setattr__(
            self, "band_version", _required_text(self.band_version, "band version")
        )
        object.__setattr__(self, "name", _required_text(self.name, "band name"))
        if (
            not math.isfinite(self.lower_x_m)
            or not math.isfinite(self.upper_x_m)
            or self.lower_x_m >= self.upper_x_m
        ):
            raise ValueError("spatial stress band requires increasing finite X bounds")
        if not isinstance(self.upper_bound_inclusive, bool):
            raise TypeError("upper-bound inclusion must be boolean")
        object.__setattr__(
            self, "geometry_context", _required_text(self.geometry_context, "geometry context")
        )

    def contains(self, location_m: Vector3) -> bool:
        upper_match = (
            location_m.x <= self.upper_x_m
            if self.upper_bound_inclusive
            else location_m.x < self.upper_x_m
        )
        return self.lower_x_m <= location_m.x and upper_match


@dataclass(frozen=True)
class SpatialStressBandEvidence:
    evidence_version: str
    band: SpatialStressBand
    mesh_identity: str
    mesh: MeshSummary
    sample_count: int
    minimum_von_mises_pa: float
    arithmetic_mean_von_mises_pa: float
    maximum_von_mises_pa: float
    maximum_location_m: Vector3
    units: str = field(default="Pa", init=False)
    interpretation: str = field(default="diagnostic_evidence", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_version", _required_text(self.evidence_version, "evidence version")
        )
        if not isinstance(self.band, SpatialStressBand):
            raise TypeError("spatial stress evidence requires a band")
        object.__setattr__(
            self, "mesh_identity", _required_text(self.mesh_identity, "mesh identity")
        )
        if not isinstance(self.mesh, MeshSummary):
            raise TypeError("spatial stress evidence requires a MeshSummary")
        if not isinstance(self.sample_count, int) or self.sample_count <= 0:
            raise ValueError("spatial stress band sample count must be positive")
        values = (
            self.minimum_von_mises_pa,
            self.arithmetic_mean_von_mises_pa,
            self.maximum_von_mises_pa,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("spatial stress statistics must be finite and nonnegative")
        if not values[0] <= values[1] <= values[2]:
            raise ValueError("spatial stress statistics must be minimum/mean/maximum ordered")
        if not isinstance(self.maximum_location_m, Vector3):
            raise TypeError("spatial stress maximum location must be a Vector3")
        if not self.band.contains(self.maximum_location_m):
            raise ValueError("spatial stress maximum location must be inside its band")


@dataclass(frozen=True)
class SpatialStressBandStudy:
    study_version: str
    band: SpatialStressBand
    evaluations: tuple[SpatialStressBandEvidence, ...]
    adjacent_changes: tuple[RegionalStressRefinementChange, ...]
    scope: str = field(default="three_level_spatial_band_observation_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "study_version", _required_text(self.study_version, "study version")
        )
        evaluations = tuple(self.evaluations)
        changes = tuple(self.adjacent_changes)
        if len(evaluations) != 3 or len(changes) != 2:
            raise ValueError("spatial stress band study requires three levels and two changes")
        if any(item.band != self.band for item in evaluations):
            raise ValueError("spatial stress band study requires one physical band")
        sizes = tuple(item.mesh.characteristic_size_m for item in evaluations)
        if not sizes[0] > sizes[1] > sizes[2] > 0.0:
            raise ValueError("spatial stress band levels must be coarse to fine")
        if any(
            changes[index].reference_mesh_size_m != sizes[index]
            or changes[index].refined_mesh_size_m != sizes[index + 1]
            for index in range(2)
        ):
            raise ValueError("spatial stress changes must join adjacent mesh levels")
        object.__setattr__(self, "evaluations", evaluations)
        object.__setattr__(self, "adjacent_changes", changes)


@dataclass(frozen=True)
class FeatureRelationshipEvidence:
    feature_id: str
    relationship: str
    metric_name: str | None
    metric_value_m: float | None
    method: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_id", _required_text(self.feature_id, "feature ID"))
        object.__setattr__(
            self, "relationship", _required_text(self.relationship, "feature relationship")
        )
        if (self.metric_name is None) != (self.metric_value_m is None):
            raise ValueError("feature metric name and value must be both present or both absent")
        if self.metric_name is not None:
            object.__setattr__(
                self, "metric_name", _required_text(self.metric_name, "feature metric name")
            )
            if not math.isfinite(self.metric_value_m) or self.metric_value_m < 0.0:
                raise ValueError("feature metric must be finite and nonnegative")
        object.__setattr__(self, "method", _required_text(self.method, "feature method"))


@dataclass(frozen=True)
class LocatedStressFeatureEvidence:
    mesh_identity: str
    mesh_size_m: float
    maximum_von_mises_pa: float
    location_m: Vector3
    feature_relationships: tuple[FeatureRelationshipEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "mesh_identity", _required_text(self.mesh_identity, "mesh identity")
        )
        if not math.isfinite(self.mesh_size_m) or self.mesh_size_m <= 0.0:
            raise ValueError("located stress mesh size must be positive and finite")
        if not math.isfinite(self.maximum_von_mises_pa) or self.maximum_von_mises_pa < 0.0:
            raise ValueError("located stress maximum must be finite and nonnegative")
        if not isinstance(self.location_m, Vector3):
            raise TypeError("located stress evidence requires a Vector3")
        relationships = tuple(self.feature_relationships)
        if not relationships or any(
            not isinstance(item, FeatureRelationshipEvidence) for item in relationships
        ):
            raise ValueError("located stress evidence requires feature relationships")
        object.__setattr__(self, "feature_relationships", relationships)


@dataclass(frozen=True)
class StressSpatialDiagnosticPolicy:
    policy_name: str
    policy_version: str
    near_feature_distance_m: float
    localized_maximum_distance_m: float
    material_movement_distance_m: float
    stable_relative_mean_change: float
    sensitive_relative_maximum_change: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _required_text(self.policy_name, "policy name"))
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        values = (
            self.near_feature_distance_m,
            self.localized_maximum_distance_m,
            self.material_movement_distance_m,
            self.stable_relative_mean_change,
            self.sensitive_relative_maximum_change,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("stress spatial policy values must be finite and nonnegative")
        if self.localized_maximum_distance_m >= self.material_movement_distance_m:
            raise ValueError("localized distance must be below material-movement distance")


@dataclass(frozen=True)
class SpatialBandBehavior:
    band_id: str
    mean_behavior: str
    maximum_behavior: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "band_id", _required_text(self.band_id, "band ID"))
        if self.mean_behavior not in {"comparatively_stable", "mesh_sensitive", "indeterminate"}:
            raise ValueError("unsupported band mean behavior")
        if self.maximum_behavior not in {
            "comparatively_stable",
            "mesh_sensitive",
            "indeterminate",
        }:
            raise ValueError("unsupported band maximum behavior")


@dataclass(frozen=True)
class StressSpatialDiagnostic:
    diagnostic_version: str
    policy: StressSpatialDiagnosticPolicy
    regional_maxima: tuple[LocatedStressFeatureEvidence, ...]
    adjacent_maximum_location_distances_m: tuple[float, ...]
    regional_location_behavior: str
    band_behaviors: tuple[SpatialBandBehavior, ...]
    feature_relationship_interpretation: str = field(
        default="geometric_association_only_mechanism_indeterminate", init=False
    )
    scope: str = field(default="controlled_spatial_stress_diagnostic_only", init=False)
    interpretation: str = field(default="diagnostic_evidence", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic_version",
            _required_text(self.diagnostic_version, "diagnostic version"),
        )
        if not isinstance(self.policy, StressSpatialDiagnosticPolicy):
            raise TypeError("stress spatial diagnostic requires an explicit policy")
        maxima = tuple(self.regional_maxima)
        distances = tuple(self.adjacent_maximum_location_distances_m)
        behaviors = tuple(self.band_behaviors)
        if len(maxima) != 3 or len(distances) != 2:
            raise ValueError("stress spatial diagnostic requires three maxima and two distances")
        if not maxima[0].mesh_size_m > maxima[1].mesh_size_m > maxima[2].mesh_size_m:
            raise ValueError("regional maxima must be ordered coarse to fine")
        if any(not math.isfinite(value) or value < 0.0 for value in distances):
            raise ValueError("maximum-location distances must be finite and nonnegative")
        if self.regional_location_behavior not in {
            "spatially_localized_within_policy",
            "materially_moved_with_refinement",
            "indeterminate",
        }:
            raise ValueError("unsupported regional location behavior")
        if not behaviors or any(not isinstance(item, SpatialBandBehavior) for item in behaviors):
            raise ValueError("stress spatial diagnostic requires band behaviors")
        object.__setattr__(self, "regional_maxima", maxima)
        object.__setattr__(self, "adjacent_maximum_location_distances_m", distances)
        object.__setattr__(self, "band_behaviors", behaviors)


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
    samples = tuple(
        (von_mises_stress_pa(item.stress_pa), item)
        for item in numerical_result.integration_point_stresses
        if region.contains(item.location_m)
    )
    if not samples:
        raise RegionalStressEvidenceError(
            f"region {region.region_id!r} contains no located integration-point stresses"
        )
    values = tuple(value for value, _ in samples)
    maximum_value, maximum_sample = min(
        samples,
        key=lambda sample: (
            -sample[0],
            *sample[1].location_m.as_tuple(),
            sample[1].element_id,
            sample[1].integration_point,
        ),
    )
    return RegionalStressEvidence(
        "1",
        region,
        mesh_identity,
        mesh,
        len(values),
        min(values),
        sum(values) / len(values),
        maximum_value,
        maximum_sample.location_m,
    )


def validate_spatial_stress_bands(bands: Sequence[SpatialStressBand]) -> tuple[SpatialStressBand, ...]:
    ordered = tuple(bands)
    if not ordered:
        raise ValueError("at least one spatial stress band is required")
    if any(not isinstance(item, SpatialStressBand) for item in ordered):
        raise TypeError("spatial stress bands must be SpatialStressBand values")
    if len({item.band_id for item in ordered}) != len(ordered):
        raise ValueError("spatial stress band IDs must be unique")
    for previous, current in zip(ordered, ordered[1:]):
        if previous.upper_x_m > current.lower_x_m or (
            previous.upper_x_m == current.lower_x_m
            and previous.upper_bound_inclusive
        ):
            raise ValueError("spatial stress bands must be ordered and non-overlapping")
    return ordered


def evaluate_spatial_stress_bands(
    bands: Sequence[SpatialStressBand],
    numerical_result: NumericalResult,
    *,
    mesh_identity: str,
    mesh: MeshSummary,
) -> tuple[SpatialStressBandEvidence, ...]:
    """Summarize all raw stress samples in each controlled physical X band."""
    ordered = validate_spatial_stress_bands(bands)
    if any(item.location_m is None for item in numerical_result.integration_point_stresses):
        raise RegionalStressEvidenceError(
            "spatial stress bands require a location for every integration point"
        )
    results = []
    for band in ordered:
        samples = tuple(
            (von_mises_stress_pa(item.stress_pa), item)
            for item in numerical_result.integration_point_stresses
            if band.contains(item.location_m)
        )
        if not samples:
            raise RegionalStressEvidenceError(
                f"spatial stress band {band.band_id!r} contains no samples"
            )
        values = tuple(value for value, _ in samples)
        maximum_value, maximum_sample = min(
            samples,
            key=lambda sample: (
                -sample[0],
                *sample[1].location_m.as_tuple(),
                sample[1].element_id,
                sample[1].integration_point,
            ),
        )
        results.append(
            SpatialStressBandEvidence(
                "1",
                band,
                mesh_identity,
                mesh,
                len(values),
                min(values),
                sum(values) / len(values),
                maximum_value,
                maximum_sample.location_m,
            )
        )
    return tuple(results)


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


def build_spatial_stress_band_studies(
    mesh_levels: Sequence[Sequence[SpatialStressBandEvidence]],
) -> tuple[SpatialStressBandStudy, ...]:
    levels = tuple(tuple(level) for level in mesh_levels)
    if len(levels) != 3 or not levels[0]:
        raise ValueError("spatial stress study requires three nonempty mesh levels")
    bands = tuple(item.band for item in levels[0])
    if any(tuple(item.band for item in level) != bands for level in levels[1:]):
        raise ValueError("every mesh level must use identical ordered physical bands")
    studies = []
    for index, band in enumerate(bands):
        evaluations = tuple(level[index] for level in levels)
        changes = tuple(
            RegionalStressRefinementChange(
                reference.mesh.characteristic_size_m,
                refined.mesh.characteristic_size_m,
                refined.arithmetic_mean_von_mises_pa
                - reference.arithmetic_mean_von_mises_pa,
                _relative_change(
                    reference.arithmetic_mean_von_mises_pa,
                    refined.arithmetic_mean_von_mises_pa,
                ),
                refined.maximum_von_mises_pa - reference.maximum_von_mises_pa,
                _relative_change(
                    reference.maximum_von_mises_pa, refined.maximum_von_mises_pa
                ),
            )
            for reference, refined in zip(evaluations, evaluations[1:])
        )
        studies.append(SpatialStressBandStudy("1", band, evaluations, changes))
    return tuple(studies)


def build_stress_spatial_diagnostic(
    regional_maxima: Sequence[LocatedStressFeatureEvidence],
    band_studies: Sequence[SpatialStressBandStudy],
    policy: StressSpatialDiagnosticPolicy,
) -> StressSpatialDiagnostic:
    maxima = tuple(regional_maxima)
    studies = tuple(band_studies)
    if len(maxima) != 3:
        raise ValueError("stress spatial diagnostic requires three regional maxima")
    distances = tuple(
        math.dist(reference.location_m.as_tuple(), refined.location_m.as_tuple())
        for reference, refined in zip(maxima, maxima[1:])
    )
    if any(value >= policy.material_movement_distance_m for value in distances):
        location_behavior = "materially_moved_with_refinement"
    elif all(value <= policy.localized_maximum_distance_m for value in distances):
        location_behavior = "spatially_localized_within_policy"
    else:
        location_behavior = "indeterminate"
    band_behaviors = []
    for study in studies:
        mean_changes = tuple(item.relative_mean_change for item in study.adjacent_changes)
        maximum_changes = tuple(
            item.relative_maximum_change for item in study.adjacent_changes
        )
        mean_behavior = (
            "indeterminate"
            if any(value is None for value in mean_changes)
            else (
                "comparatively_stable"
                if all(value <= policy.stable_relative_mean_change for value in mean_changes)
                else "mesh_sensitive"
            )
        )
        maximum_behavior = (
            "indeterminate"
            if any(value is None for value in maximum_changes)
            else (
                "mesh_sensitive"
                if any(
                    value >= policy.sensitive_relative_maximum_change
                    for value in maximum_changes
                )
                else "comparatively_stable"
            )
        )
        band_behaviors.append(
            SpatialBandBehavior(study.band.band_id, mean_behavior, maximum_behavior)
        )
    return StressSpatialDiagnostic(
        "1",
        policy,
        maxima,
        distances,
        location_behavior,
        tuple(band_behaviors),
    )


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
        "maximum_location_m": list(evidence.maximum_location_m.as_tuple()),
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


def spatial_stress_band_to_dict(band: SpatialStressBand) -> dict:
    return {
        "band_id": band.band_id,
        "band_version": band.band_version,
        "name": band.name,
        "axis": "global_x",
        "lower_x_m": band.lower_x_m,
        "upper_x_m": band.upper_x_m,
        "lower_bound_inclusive": True,
        "upper_bound_inclusive": band.upper_bound_inclusive,
        "coordinate_semantics": band.coordinate_semantics,
        "geometry_context": band.geometry_context,
    }


def spatial_stress_band_evidence_to_dict(evidence: SpatialStressBandEvidence) -> dict:
    return {
        "evidence_version": evidence.evidence_version,
        "interpretation": evidence.interpretation,
        "band": spatial_stress_band_to_dict(evidence.band),
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
        "maximum_location_m": list(evidence.maximum_location_m.as_tuple()),
        "units": evidence.units,
    }


def spatial_stress_band_study_to_dict(study: SpatialStressBandStudy) -> dict:
    return {
        "study_version": study.study_version,
        "scope": study.scope,
        "band": spatial_stress_band_to_dict(study.band),
        "ordered_mesh_evaluations": [
            spatial_stress_band_evidence_to_dict(item) for item in study.evaluations
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


def stress_spatial_diagnostic_to_dict(diagnostic: StressSpatialDiagnostic) -> dict:
    def relationship_to_dict(item: FeatureRelationshipEvidence) -> dict:
        return {
            "feature_id": item.feature_id,
            "relationship": item.relationship,
            "metric_name": item.metric_name,
            "metric_value_m": item.metric_value_m,
            "method": item.method,
        }

    return {
        "diagnostic_version": diagnostic.diagnostic_version,
        "scope": diagnostic.scope,
        "interpretation": diagnostic.interpretation,
        "policy": {
            "name": diagnostic.policy.policy_name,
            "version": diagnostic.policy.policy_version,
            "near_feature_distance_m": diagnostic.policy.near_feature_distance_m,
            "localized_maximum_distance_m": (
                diagnostic.policy.localized_maximum_distance_m
            ),
            "material_movement_distance_m": (
                diagnostic.policy.material_movement_distance_m
            ),
            "stable_relative_mean_change": (
                diagnostic.policy.stable_relative_mean_change
            ),
            "sensitive_relative_maximum_change": (
                diagnostic.policy.sensitive_relative_maximum_change
            ),
        },
        "regional_maxima": [
            {
                "mesh_identity": item.mesh_identity,
                "mesh_size_m": item.mesh_size_m,
                "maximum_von_mises_pa": item.maximum_von_mises_pa,
                "location_m": list(item.location_m.as_tuple()),
                "feature_relationships": [
                    relationship_to_dict(relationship)
                    for relationship in item.feature_relationships
                ],
            }
            for item in diagnostic.regional_maxima
        ],
        "adjacent_maximum_location_distances_m": list(
            diagnostic.adjacent_maximum_location_distances_m
        ),
        "regional_location_behavior": diagnostic.regional_location_behavior,
        "band_behaviors": [
            {
                "band_id": item.band_id,
                "mean_behavior": item.mean_behavior,
                "maximum_behavior": item.maximum_behavior,
            }
            for item in diagnostic.band_behaviors
        ],
        "feature_relationship_interpretation": (
            diagnostic.feature_relationship_interpretation
        ),
        "semantics": (
            "controlled geometric association and observed mesh behavior only"
        ),
    }


def stress_spatial_diagnostic_to_json(diagnostic: StressSpatialDiagnostic) -> str:
    return json.dumps(
        stress_spatial_diagnostic_to_dict(diagnostic),
        sort_keys=True,
        separators=(",", ":"),
    )


def regional_stress_mesh_study_to_json(study: RegionalStressMeshStudy) -> str:
    return json.dumps(
        regional_stress_mesh_study_to_dict(study), sort_keys=True, separators=(",", ":")
    )
