"""Solver-neutral evidence for one controlled local feature-refinement study."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from analysis_results import GlobalRawMaximumVonMises
from engineering_stress import PhysicalCoordinateBoxRegion, RegionalStressEvidence
from numerical_results import Vector3


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


def _code(value: str, label: str) -> str:
    value = _text(value, label)
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise ValueError(f"{label} must use lower snake case")
    return value


@dataclass(frozen=True)
class LocalMeshRefinementDefinition:
    definition_version: str
    level_id: str
    feature_id: str
    feature_version: str
    sizing_region_minimum_m: Vector3
    sizing_region_maximum_m: Vector3
    far_field_size_m: float
    local_target_size_m: float
    transition_thickness_m: float
    gmsh_field_type: str = field(default="Box", init=False)
    selection_semantics: str = field(
        default="physical_global_coordinate_box_not_mesh_entities", init=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "definition_version", _text(self.definition_version, "definition version"))
        object.__setattr__(self, "level_id", _code(self.level_id, "level ID"))
        object.__setattr__(self, "feature_id", _code(self.feature_id, "feature ID"))
        object.__setattr__(self, "feature_version", _text(self.feature_version, "feature version"))
        if not isinstance(self.sizing_region_minimum_m, Vector3) or not isinstance(
            self.sizing_region_maximum_m, Vector3
        ):
            raise TypeError("local sizing bounds must be Vector3 values")
        if any(
            lower >= upper
            for lower, upper in zip(
                self.sizing_region_minimum_m.as_tuple(),
                self.sizing_region_maximum_m.as_tuple(),
            )
        ):
            raise ValueError("local sizing bounds must increase on every axis")
        for value, label in (
            (self.far_field_size_m, "far-field size"),
            (self.local_target_size_m, "local target size"),
            (self.transition_thickness_m, "transition thickness"),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{label} must be positive and finite")
        if self.local_target_size_m > self.far_field_size_m:
            raise ValueError("local target size cannot exceed the far-field size")


@dataclass(frozen=True)
class FeatureMaximumLocalizationPolicy:
    policy_name: str
    policy_version: str
    maximum_adjacent_movement_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _text(self.policy_name, "policy name"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy version"))
        if not math.isfinite(self.maximum_adjacent_movement_m) or self.maximum_adjacent_movement_m < 0.0:
            raise ValueError("maximum localization movement must be finite and nonnegative")


@dataclass(frozen=True)
class FeatureStressBehaviorPolicy:
    policy_name: str
    policy_version: str
    stable_relative_mean_change: float
    sensitive_relative_maximum_change: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _text(self.policy_name, "policy name"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy version"))
        for value in (
            self.stable_relative_mean_change,
            self.sensitive_relative_maximum_change,
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError("feature stress behavior tolerances must be finite and nonnegative")


@dataclass(frozen=True)
class FeatureStressRefinementLevel:
    refinement: LocalMeshRefinementDefinition
    evidence: RegionalStressEvidence
    global_raw_peak: GlobalRawMaximumVonMises

    def __post_init__(self) -> None:
        if not isinstance(self.refinement, LocalMeshRefinementDefinition):
            raise TypeError("feature level requires a local refinement definition")
        if not isinstance(self.evidence, RegionalStressEvidence):
            raise TypeError("feature level requires regional stress evidence")
        if not isinstance(self.global_raw_peak, GlobalRawMaximumVonMises):
            raise TypeError("feature level requires a global raw stress diagnostic")


@dataclass(frozen=True)
class FeatureStressRefinementChange:
    reference_level_id: str
    refined_level_id: str
    signed_mean_change_pa: float
    relative_mean_change: float | None
    signed_maximum_change_pa: float
    relative_maximum_change: float | None
    maximum_location_movement_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_level_id", _code(self.reference_level_id, "reference level ID"))
        object.__setattr__(self, "refined_level_id", _code(self.refined_level_id, "refined level ID"))
        for value in (
            self.signed_mean_change_pa,
            self.signed_maximum_change_pa,
            self.maximum_location_movement_m,
        ):
            if not math.isfinite(value):
                raise ValueError("feature refinement changes must be finite")
        if self.maximum_location_movement_m < 0.0:
            raise ValueError("maximum-location movement cannot be negative")
        for value in (self.relative_mean_change, self.relative_maximum_change):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError("relative feature refinement changes must be nonnegative")


@dataclass(frozen=True)
class FormalConvergenceEligibility:
    status: str
    reason_code: str
    explanation: str

    def __post_init__(self) -> None:
        if self.status != "ineligible":
            raise ValueError("V1 local-field studies are outside the formal uniform-grid contract")
        object.__setattr__(self, "reason_code", _code(self.reason_code, "eligibility reason code"))
        object.__setattr__(self, "explanation", _text(self.explanation, "eligibility explanation"))


@dataclass(frozen=True)
class FeatureStressRefinementStudy:
    study_id: str
    study_version: str
    feature_region: PhysicalCoordinateBoxRegion
    levels: tuple[FeatureStressRefinementLevel, ...]
    adjacent_changes: tuple[FeatureStressRefinementChange, ...]
    localization_policy: FeatureMaximumLocalizationPolicy
    localization_status: str
    behavior_policy: FeatureStressBehaviorPolicy
    mean_behavior: str
    maximum_behavior: str
    formal_convergence: FormalConvergenceEligibility
    scope: str = field(default="targeted_local_feature_refinement_evidence_only", init=False)
    interpretation: str = field(default="diagnostic_evidence", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _code(self.study_id, "study ID"))
        object.__setattr__(self, "study_version", _text(self.study_version, "study version"))
        levels = tuple(self.levels)
        changes = tuple(self.adjacent_changes)
        if len(levels) != 3 or len(changes) != 2:
            raise ValueError("V1 feature refinement study requires three levels and two changes")
        if any(item.evidence.region != self.feature_region for item in levels):
            raise ValueError("every level must evaluate the same physical feature region")
        far_sizes = {item.refinement.far_field_size_m for item in levels}
        if len(far_sizes) != 1:
            raise ValueError("far-field sizing must remain fixed")
        local_sizes = tuple(item.refinement.local_target_size_m for item in levels)
        if not local_sizes[0] > local_sizes[1] > local_sizes[2] > 0.0:
            raise ValueError("local target sizes must decrease coarse to fine")
        if not levels[0].evidence.sample_count < levels[1].evidence.sample_count < levels[2].evidence.sample_count:
            raise ValueError("feature sample count must increase under local refinement")
        if self.localization_status not in {"spatially_localized_within_policy", "not_localized_within_policy"}:
            raise ValueError("unsupported feature localization status")
        if self.mean_behavior not in {"comparatively_stable", "mesh_sensitive", "indeterminate"}:
            raise ValueError("unsupported feature mean behavior")
        if self.maximum_behavior not in {"comparatively_stable", "mesh_sensitive", "indeterminate"}:
            raise ValueError("unsupported feature maximum behavior")
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "adjacent_changes", changes)


@dataclass(frozen=True)
class CriticalStressAssessmentImpact:
    impact_version: str
    prior_status: str
    resulting_status: str
    resolved_blocker_codes: tuple[str, ...]
    still_present_blocker_codes: tuple[str, ...]
    replaced_blocker_codes: tuple[str, ...]
    additional_evidence_codes: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "impact_version", _text(self.impact_version, "impact version"))
        if self.prior_status != "not_established" or self.resulting_status != "not_established":
            raise ValueError("V1 impact does not promote critical-stress status")
        for values in (
            self.resolved_blocker_codes,
            self.still_present_blocker_codes,
            self.replaced_blocker_codes,
            self.additional_evidence_codes,
        ):
            if any(re.fullmatch(r"[a-z][a-z0-9_]*", value) is None for value in values):
                raise ValueError("impact codes must use lower snake case")
        object.__setattr__(self, "explanation", _text(self.explanation, "impact explanation"))


def _relative(reference: float, refined: float) -> float | None:
    return None if reference == 0.0 else abs(refined - reference) / abs(reference)


def build_feature_stress_refinement_study(
    study_id: str,
    study_version: str,
    levels: Sequence[FeatureStressRefinementLevel],
    localization_policy: FeatureMaximumLocalizationPolicy,
    behavior_policy: FeatureStressBehaviorPolicy,
) -> FeatureStressRefinementStudy:
    ordered = tuple(levels)
    if len(ordered) != 3:
        raise ValueError("feature refinement study requires exactly three levels")
    changes = tuple(
        FeatureStressRefinementChange(
            reference.refinement.level_id,
            refined.refinement.level_id,
            refined.evidence.arithmetic_mean_von_mises_pa
            - reference.evidence.arithmetic_mean_von_mises_pa,
            _relative(
                reference.evidence.arithmetic_mean_von_mises_pa,
                refined.evidence.arithmetic_mean_von_mises_pa,
            ),
            refined.evidence.maximum_von_mises_pa
            - reference.evidence.maximum_von_mises_pa,
            _relative(
                reference.evidence.maximum_von_mises_pa,
                refined.evidence.maximum_von_mises_pa,
            ),
            math.dist(
                reference.evidence.maximum_location_m.as_tuple(),
                refined.evidence.maximum_location_m.as_tuple(),
            ),
        )
        for reference, refined in zip(ordered, ordered[1:])
    )
    localized = all(
        item.maximum_location_movement_m
        <= localization_policy.maximum_adjacent_movement_m
        for item in changes
    )
    mean_changes = tuple(item.relative_mean_change for item in changes)
    maximum_changes = tuple(item.relative_maximum_change for item in changes)
    mean_behavior = (
        "indeterminate"
        if any(value is None for value in mean_changes)
        else (
            "comparatively_stable"
            if all(value <= behavior_policy.stable_relative_mean_change for value in mean_changes)
            else "mesh_sensitive"
        )
    )
    maximum_behavior = (
        "indeterminate"
        if any(value is None for value in maximum_changes)
        else (
            "mesh_sensitive"
            if any(
                value >= behavior_policy.sensitive_relative_maximum_change
                for value in maximum_changes
            )
            else "comparatively_stable"
        )
    )
    return FeatureStressRefinementStudy(
        study_id,
        study_version,
        ordered[0].evidence.region,
        ordered,
        changes,
        localization_policy,
        (
            "spatially_localized_within_policy"
            if localized
            else "not_localized_within_policy"
        ),
        behavior_policy,
        mean_behavior,
        maximum_behavior,
        FormalConvergenceEligibility(
            "ineligible",
            "existing_uniform_grid_contract_not_applicable_to_fixed_far_field_local_sizing",
            "The existing Richardson/GCI contract requires three decreasing global characteristic sizes; this study fixes the far field and changes a nonuniform local Box field target.",
        ),
    )


def assess_critical_stress_impact(
    study: FeatureStressRefinementStudy,
    prior_blocker_codes: Sequence[str],
) -> CriticalStressAssessmentImpact:
    codes = tuple(prior_blocker_codes)
    additional = ["root_feature_local_refinement_evidence"]
    if study.maximum_behavior == "mesh_sensitive":
        additional.append("root_feature_local_maximum_mesh_sensitive")
    elif study.maximum_behavior == "comparatively_stable":
        additional.append("root_feature_local_maximum_comparatively_stable")
    else:
        additional.append("root_feature_local_maximum_behavior_indeterminate")
    additional.append(
        "root_feature_maximum_spatially_localized"
        if study.localization_status == "spatially_localized_within_policy"
        else "root_feature_maximum_not_spatially_localized"
    )
    return CriticalStressAssessmentImpact(
        "1",
        "not_established",
        "not_established",
        (),
        codes,
        (),
        tuple(additional),
        (
            "The local study adds root-feature evidence but does not define a justified "
            "stress extraction, resolve the existing lower-upright evidence, or characterize "
            "the simplified mounting constraint."
        ),
    )


def local_refinement_definition_to_dict(item: LocalMeshRefinementDefinition) -> dict:
    return {
        "definition_version": item.definition_version,
        "level_id": item.level_id,
        "feature_identity": {
            "feature_id": item.feature_id,
            "feature_version": item.feature_version,
        },
        "selection_semantics": item.selection_semantics,
        "gmsh_field_type": item.gmsh_field_type,
        "sizing_region_bounds_m": {
            "minimum": list(item.sizing_region_minimum_m.as_tuple()),
            "maximum": list(item.sizing_region_maximum_m.as_tuple()),
        },
        "far_field_size_m": item.far_field_size_m,
        "local_target_size_m": item.local_target_size_m,
        "transition_thickness_m": item.transition_thickness_m,
    }


def feature_stress_refinement_study_to_dict(study: FeatureStressRefinementStudy) -> dict:
    def evidence(level: FeatureStressRefinementLevel) -> dict:
        item = level.evidence
        peak = level.global_raw_peak
        return {
            "refinement": local_refinement_definition_to_dict(level.refinement),
            "mesh_summary": {
                "node_count": item.mesh.node_count,
                "element_count": item.mesh.element_count,
                "element_type": item.mesh.element_type,
                "far_field_characteristic_size_m": item.mesh.characteristic_size_m,
            },
            "local_region_sample_count": item.sample_count,
            "regional_von_mises_pa": {
                "minimum": item.minimum_von_mises_pa,
                "arithmetic_mean": item.arithmetic_mean_von_mises_pa,
                "maximum": item.maximum_von_mises_pa,
            },
            "regional_maximum_location_m": list(item.maximum_location_m.as_tuple()),
            "global_raw_maximum_diagnostic": {
                "interpretation": "diagnostic_only",
                "von_mises_pa": peak.von_mises_pa,
                "location_m": None if peak.location_m is None else list(peak.location_m.as_tuple()),
            },
        }

    return {
        "study_id": study.study_id,
        "study_version": study.study_version,
        "scope": study.scope,
        "interpretation": study.interpretation,
        "feature_region": {
            "region_id": study.feature_region.region_id,
            "region_version": study.feature_region.region_version,
            "name": study.feature_region.name,
            "selection_method": study.feature_region.selection_method,
            "closed_bounds_m": {
                "minimum": list(study.feature_region.minimum_m.as_tuple()),
                "maximum": list(study.feature_region.maximum_m.as_tuple()),
            },
            "feature_context": study.feature_region.feature_context,
            "constraint_relationship": study.feature_region.constraint_relationship,
            "constraint_reference": study.feature_region.constraint_reference,
            "minimum_separation_from_constraint_m": (
                study.feature_region.minimum_separation_from_constraint_m
            ),
        },
        "ordered_levels": [evidence(item) for item in study.levels],
        "adjacent_changes": [
            {
                "reference_level_id": item.reference_level_id,
                "refined_level_id": item.refined_level_id,
                "signed_mean_change_pa": item.signed_mean_change_pa,
                "relative_mean_change": item.relative_mean_change,
                "signed_maximum_change_pa": item.signed_maximum_change_pa,
                "relative_maximum_change": item.relative_maximum_change,
                "maximum_location_movement_m": item.maximum_location_movement_m,
            }
            for item in study.adjacent_changes
        ],
        "maximum_localization": {
            "status": study.localization_status,
            "policy": {
                "name": study.localization_policy.policy_name,
                "version": study.localization_policy.policy_version,
                "maximum_adjacent_movement_m": study.localization_policy.maximum_adjacent_movement_m,
            },
            "semantics": "physical XYZ localization only; not stress convergence or physical correctness",
        },
        "observed_refinement_behavior": {
            "mean": study.mean_behavior,
            "maximum": study.maximum_behavior,
            "policy": {
                "name": study.behavior_policy.policy_name,
                "version": study.behavior_policy.policy_version,
                "stable_relative_mean_change": study.behavior_policy.stable_relative_mean_change,
                "sensitive_relative_maximum_change": study.behavior_policy.sensitive_relative_maximum_change,
            },
            "semantics": "descriptive controlled thresholds only; not convergence or acceptance",
        },
        "formal_convergence_eligibility": {
            "status": study.formal_convergence.status,
            "reason_code": study.formal_convergence.reason_code,
            "explanation": study.formal_convergence.explanation,
        },
    }


def critical_stress_impact_to_dict(impact: CriticalStressAssessmentImpact) -> dict:
    return {
        "impact_version": impact.impact_version,
        "prior_status": impact.prior_status,
        "resulting_status": impact.resulting_status,
        "resolved_blocker_codes": list(impact.resolved_blocker_codes),
        "still_present_blocker_codes": list(impact.still_present_blocker_codes),
        "replaced_blocker_codes": list(impact.replaced_blocker_codes),
        "additional_evidence_codes": list(impact.additional_evidence_codes),
        "explanation": impact.explanation,
        "established_stress_reference": None,
    }


def feature_stress_refinement_study_to_json(study: FeatureStressRefinementStudy) -> str:
    return json.dumps(
        feature_stress_refinement_study_to_dict(study),
        sort_keys=True,
        separators=(",", ":"),
    )
