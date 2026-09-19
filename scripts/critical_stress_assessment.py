"""Deterministic boundary between stress evidence and a future design stress."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from engineering_quantities import RawStressMeshTrendDiagnostic
from engineering_stress import (
    RegionalStressMeshStudy,
    SpatialStressBandStudy,
    StressSpatialDiagnostic,
)


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


def _snake_code(value: str, label: str) -> str:
    value = _required_text(value, label)
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise ValueError(f"{label} must use lower snake case")
    return value


@dataclass(frozen=True)
class CriticalStressAssessmentPolicy:
    """Controlled-case criteria; deliberately not a universal FEA rule."""

    policy_name: str
    policy_version: str
    regional_evidence_region_id: str
    regional_evidence_region_version: str
    regional_containing_band_id: str
    required_maximum_mesh_behavior: str
    required_maximum_location_behavior: str
    required_local_peak_treatment: str
    global_raw_peak_role: str = field(default="diagnostic_only", init=False)
    regional_maximum_rule: str = field(
        default="requires_stable_local_maximum_and_location_evidence", init=False
    )
    regional_aggregate_rule: str = field(
        default="requires_explicit_justification_that_mean_does_not_suppress_a_meaningful_local_peak",
        init=False,
    )
    physical_region_rule: str = field(
        default="requires_versioned_physical_region_identity", init=False
    )
    constraint_relationship_rule: str = field(
        default="proximity_or_separation_alone_is_insufficient", init=False
    )
    model_assumption_rule: str = field(
        default="all_relevant_limitations_must_be_adequately_characterized", init=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _required_text(self.policy_name, "policy name"))
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        object.__setattr__(
            self,
            "regional_evidence_region_id",
            _snake_code(self.regional_evidence_region_id, "regional evidence region ID"),
        )
        object.__setattr__(
            self,
            "regional_evidence_region_version",
            _required_text(self.regional_evidence_region_version, "region version"),
        )
        object.__setattr__(
            self,
            "regional_containing_band_id",
            _snake_code(self.regional_containing_band_id, "regional containing band ID"),
        )
        if self.required_maximum_mesh_behavior != "comparatively_stable":
            raise ValueError("V1 requires a comparatively stable local maximum")
        if self.required_maximum_location_behavior != "spatially_localized_within_policy":
            raise ValueError("V1 requires a spatially localized maximum")
        if self.required_local_peak_treatment != "explicitly_retained_and_justified":
            raise ValueError("V1 requires explicit local-peak treatment")


@dataclass(frozen=True)
class ModelAssumptionLimitation:
    code: str
    description: str
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _snake_code(self.code, "assumption code"))
        object.__setattr__(
            self, "description", _required_text(self.description, "assumption description")
        )
        if self.status not in {"unresolved", "adequately_characterized"}:
            raise ValueError("unsupported model-assumption status")


@dataclass(frozen=True)
class StressExtractionCandidate:
    """Explicit proposed extraction; it is not established merely by existing."""

    source_kind: str
    region_id: str
    region_version: str
    extraction_method_id: str
    value_pa: float
    mesh_behavior: str
    location_behavior: str
    local_peak_treatment: str

    def __post_init__(self) -> None:
        if self.source_kind not in {
            "global_raw_peak",
            "regional_maximum",
            "regional_aggregate",
        }:
            raise ValueError("unsupported stress extraction source")
        object.__setattr__(self, "region_id", _snake_code(self.region_id, "candidate region ID"))
        object.__setattr__(
            self, "region_version", _required_text(self.region_version, "candidate region version")
        )
        object.__setattr__(
            self,
            "extraction_method_id",
            _snake_code(self.extraction_method_id, "extraction method ID"),
        )
        if not math.isfinite(self.value_pa) or self.value_pa < 0.0:
            raise ValueError("candidate stress must be finite and nonnegative")
        object.__setattr__(
            self, "mesh_behavior", _required_text(self.mesh_behavior, "mesh behavior")
        )
        object.__setattr__(
            self, "location_behavior", _required_text(self.location_behavior, "location behavior")
        )
        object.__setattr__(
            self,
            "local_peak_treatment",
            _required_text(self.local_peak_treatment, "local peak treatment"),
        )


@dataclass(frozen=True)
class CriticalStressBlockingReason:
    code: str
    evidence_category: str
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _snake_code(self.code, "blocking reason code"))
        object.__setattr__(
            self, "evidence_category", _snake_code(self.evidence_category, "evidence category")
        )
        object.__setattr__(self, "explanation", _required_text(self.explanation, "explanation"))


@dataclass(frozen=True)
class CandidateInvestigationRegion:
    region_id: str
    region_version: str
    selection_basis: str
    interpretation: str = field(default="investigation_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "region_id", _snake_code(self.region_id, "candidate region ID"))
        object.__setattr__(
            self, "region_version", _required_text(self.region_version, "candidate region version")
        )
        object.__setattr__(
            self, "selection_basis", _snake_code(self.selection_basis, "selection basis")
        )


@dataclass(frozen=True)
class NextStressEvidenceRequirement:
    code: str
    evidence_category: str
    requirement: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _snake_code(self.code, "requirement code"))
        object.__setattr__(
            self, "evidence_category", _snake_code(self.evidence_category, "evidence category")
        )
        object.__setattr__(self, "requirement", _required_text(self.requirement, "requirement"))


@dataclass(frozen=True)
class EstablishedStressReference:
    region_id: str
    region_version: str
    extraction_method_id: str
    value_pa: float
    source_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "region_id", _snake_code(self.region_id, "stress region ID"))
        object.__setattr__(
            self, "region_version", _required_text(self.region_version, "stress region version")
        )
        object.__setattr__(
            self,
            "extraction_method_id",
            _snake_code(self.extraction_method_id, "extraction method ID"),
        )
        if not math.isfinite(self.value_pa) or self.value_pa < 0.0:
            raise ValueError("established stress must be finite and nonnegative")
        if self.source_kind not in {"regional_maximum", "regional_aggregate"}:
            raise ValueError("a global raw peak cannot be an established stress reference")


@dataclass(frozen=True)
class CriticalStressAssessment:
    assessment_version: str
    policy: CriticalStressAssessmentPolicy
    status: str
    established_stress_reference: EstablishedStressReference | None
    blocking_reasons: tuple[CriticalStressBlockingReason, ...]
    candidate_regions: tuple[CandidateInvestigationRegion, ...]
    next_evidence_requirements: tuple[NextStressEvidenceRequirement, ...]
    model_assumptions: tuple[ModelAssumptionLimitation, ...]
    scope: str = field(default="controlled_case_design_stress_establishment_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "assessment_version", _required_text(self.assessment_version, "assessment version")
        )
        if not isinstance(self.policy, CriticalStressAssessmentPolicy):
            raise TypeError("critical stress assessment requires an explicit policy")
        if self.status not in {"established", "not_established"}:
            raise ValueError("unsupported critical stress assessment status")
        blockers = tuple(self.blocking_reasons)
        regions = tuple(self.candidate_regions)
        requirements = tuple(self.next_evidence_requirements)
        assumptions = tuple(self.model_assumptions)
        if self.status == "established":
            if not isinstance(self.established_stress_reference, EstablishedStressReference):
                raise ValueError("established status requires a stress reference")
            if blockers or requirements:
                raise ValueError("established status cannot retain blockers or next requirements")
        else:
            if self.established_stress_reference is not None:
                raise ValueError("not-established status cannot emit a stress reference")
            if not blockers or not requirements:
                raise ValueError("not-established status requires blockers and next evidence")
        object.__setattr__(self, "blocking_reasons", blockers)
        object.__setattr__(self, "candidate_regions", regions)
        object.__setattr__(self, "next_evidence_requirements", requirements)
        object.__setattr__(self, "model_assumptions", assumptions)


def _reason(code: str, category: str, explanation: str) -> CriticalStressBlockingReason:
    return CriticalStressBlockingReason(code, category, explanation)


def _requirement(code: str, category: str, text: str) -> NextStressEvidenceRequirement:
    return NextStressEvidenceRequirement(code, category, text)


def build_critical_stress_assessment(
    raw_stress: RawStressMeshTrendDiagnostic,
    regional_stress: RegionalStressMeshStudy,
    spatial_bands: Sequence[SpatialStressBandStudy],
    spatial_diagnostic: StressSpatialDiagnostic,
    policy: CriticalStressAssessmentPolicy,
    model_assumptions: Sequence[ModelAssumptionLimitation],
    *,
    candidate: StressExtractionCandidate | None = None,
    assessment_version: str = "critical-stress-assessment/1",
) -> CriticalStressAssessment:
    """Apply explicit criteria to existing evidence; never recompute solver output."""
    if not isinstance(raw_stress, RawStressMeshTrendDiagnostic):
        raise TypeError("raw stress input must be RawStressMeshTrendDiagnostic")
    if not isinstance(regional_stress, RegionalStressMeshStudy):
        raise TypeError("regional stress input must be RegionalStressMeshStudy")
    if not isinstance(spatial_diagnostic, StressSpatialDiagnostic):
        raise TypeError("spatial diagnostic input must be StressSpatialDiagnostic")
    if not isinstance(policy, CriticalStressAssessmentPolicy):
        raise TypeError("critical stress policy must be CriticalStressAssessmentPolicy")
    bands = tuple(spatial_bands)
    assumptions = tuple(model_assumptions)
    if any(not isinstance(item, SpatialStressBandStudy) for item in bands):
        raise TypeError("spatial band evidence must contain SpatialStressBandStudy values")
    if any(not isinstance(item, ModelAssumptionLimitation) for item in assumptions):
        raise TypeError("model assumptions must contain ModelAssumptionLimitation values")

    blockers: list[CriticalStressBlockingReason] = []
    region = regional_stress.region
    physical_region_matches = (
        region.region_id == policy.regional_evidence_region_id
        and region.region_version == policy.regional_evidence_region_version
    )
    band_behavior = next(
        (
            item
            for item in spatial_diagnostic.band_behaviors
            if item.band_id == policy.regional_containing_band_id
        ),
        None,
    )

    candidate_regions = tuple(
        CandidateInvestigationRegion(
            study.band.band_id,
            study.band.band_version,
            "mesh_sensitive_local_maximum",
        )
        for study in bands
        if next(
            (
                behavior.maximum_behavior
                for behavior in spatial_diagnostic.band_behaviors
                if behavior.band_id == study.band.band_id
            ),
            "indeterminate",
        )
        == "mesh_sensitive"
    )

    if not physical_region_matches:
        blockers.append(
            _reason(
                "physical_region_identity_mismatch",
                "physical_region_identity",
                "The regional evidence does not match the versioned physical region required by the policy.",
            )
        )
    if candidate is None:
        blockers.extend(
            (
                _reason(
                    "global_raw_peak_is_diagnostic_only",
                    "global_raw_peak",
                    "The global raw peak is retained as diagnostic evidence and is not an extraction method.",
                ),
                _reason(
                    "feature_specific_extraction_not_defined",
                    "stress_extraction_method",
                    "No justified feature-specific stress extraction method has been defined.",
                ),
            )
        )
        if raw_stress.observed_change_trend != "stabilizing":
            blockers.append(
                _reason(
                    "global_raw_peak_mesh_sensitive",
                    "global_raw_peak",
                    "The global raw peak does not show a stabilizing observed-change trend and remains diagnostic only.",
                )
            )
    else:
        if candidate.source_kind == "global_raw_peak":
            blockers.append(
                _reason(
                    "global_raw_peak_cannot_establish_design_stress",
                    "global_raw_peak",
                    "Policy prohibits automatic promotion of the global raw peak.",
                )
            )
        if (
            candidate.region_id != policy.regional_evidence_region_id
            or candidate.region_version != policy.regional_evidence_region_version
        ):
            blockers.append(
                _reason(
                    "candidate_region_identity_mismatch",
                    "physical_region_identity",
                    "The proposed extraction does not target the policy's versioned physical region.",
                )
            )
        if candidate.mesh_behavior != policy.required_maximum_mesh_behavior:
            blockers.append(
                _reason(
                    "candidate_mesh_behavior_insufficient",
                    "mesh_refinement_behavior",
                    "The proposed extraction lacks the required refinement stability.",
                )
            )
        if candidate.location_behavior != policy.required_maximum_location_behavior:
            blockers.append(
                _reason(
                    "candidate_location_behavior_insufficient",
                    "spatial_behavior",
                    "The proposed extraction lacks the required spatial localization evidence.",
                )
            )
        if candidate.local_peak_treatment != policy.required_local_peak_treatment:
            blockers.append(
                _reason(
                    "local_peak_treatment_not_justified",
                    "regional_aggregate",
                    "The extraction does not show that averaging preserves or explicitly resolves a meaningful local peak.",
                )
            )
        expected_recorded_value = (
            regional_stress.evaluations[-1].maximum_von_mises_pa
            if candidate.source_kind == "regional_maximum"
            else (
                regional_stress.evaluations[-1].arithmetic_mean_von_mises_pa
                if candidate.source_kind == "regional_aggregate"
                else None
            )
        )
        if expected_recorded_value is not None and candidate.value_pa != expected_recorded_value:
            blockers.append(
                _reason(
                    "candidate_value_not_traceable_to_regional_evidence",
                    "stress_extraction_method",
                    "The proposed value does not equal the recorded fine-mesh statistic for its declared source kind.",
                )
            )

    if band_behavior is None or band_behavior.maximum_behavior != policy.required_maximum_mesh_behavior:
        blockers.append(
            _reason(
                "regional_local_maximum_mesh_sensitive",
                "regional_maximum",
                "The containing physical band's local maximum is not comparatively stable over the tested meshes.",
            )
        )
    if spatial_diagnostic.regional_location_behavior != policy.required_maximum_location_behavior:
        blockers.append(
            _reason(
                "regional_maximum_location_not_stable",
                "spatial_behavior",
                "The regional maximum location is not spatially localized under the configured diagnostic policy.",
            )
        )
    if any(item.status != "adequately_characterized" for item in assumptions):
        blockers.append(
            _reason(
                "model_assumption_limitations_unresolved",
                "model_assumptions",
                "At least one relevant modeling limitation is not adequately characterized for design-stress selection.",
            )
        )

    # Preserve stable order while removing codes that can arise from overlapping criteria.
    blockers = list({item.code: item for item in blockers}.values())
    requirements: list[NextStressEvidenceRequirement] = []
    blocker_codes = {item.code for item in blockers}
    if blocker_codes & {
        "regional_local_maximum_mesh_sensitive",
        "candidate_mesh_behavior_insufficient",
    }:
        requirements.extend(
            (
                _requirement(
                    "feature_specific_local_refinement",
                    "mesh_refinement_behavior",
                    "Produce feature-specific local refinement evidence in each candidate investigation region.",
                ),
                _requirement(
                    "feature_specific_stress_convergence_evidence",
                    "mesh_refinement_behavior",
                    "Demonstrate the configured local stress extraction's behavior over an appropriate refinement sequence.",
                ),
            )
        )
    if blocker_codes & {
        "feature_specific_extraction_not_defined",
        "global_raw_peak_cannot_establish_design_stress",
        "local_peak_treatment_not_justified",
        "candidate_value_not_traceable_to_regional_evidence",
    }:
        requirements.append(
            _requirement(
                "explicit_stress_extraction_methodology",
                "stress_extraction_method",
                "Define and justify a feature-specific extraction method, including treatment of meaningful local peaks.",
            )
        )
    if blocker_codes & {
        "regional_maximum_location_not_stable",
        "candidate_location_behavior_insufficient",
    }:
        requirements.append(
            _requirement(
                "localized_peak_tracking_evidence",
                "spatial_behavior",
                "Establish that the selected local stress feature remains spatially identified through refinement.",
            )
        )
    if "model_assumption_limitations_unresolved" in blocker_codes:
        requirements.append(
            _requirement(
                "constraint_sensitive_stress_characterization",
                "model_assumptions",
                "Characterize the influence of the simplified mounting constraint on the proposed stress extraction.",
            )
        )
    if blocker_codes & {"physical_region_identity_mismatch", "candidate_region_identity_mismatch"}:
        requirements.append(
            _requirement(
                "versioned_physical_region_evidence",
                "physical_region_identity",
                "Evaluate the extraction on the policy's versioned physical region.",
            )
        )

    if blockers:
        return CriticalStressAssessment(
            assessment_version,
            policy,
            "not_established",
            None,
            tuple(blockers),
            candidate_regions,
            tuple(requirements),
            assumptions,
        )
    assert candidate is not None
    return CriticalStressAssessment(
        assessment_version,
        policy,
        "established",
        EstablishedStressReference(
            candidate.region_id,
            candidate.region_version,
            candidate.extraction_method_id,
            candidate.value_pa,
            candidate.source_kind,
        ),
        (),
        candidate_regions,
        (),
        assumptions,
    )


def critical_stress_assessment_to_dict(assessment: CriticalStressAssessment) -> dict:
    reference = assessment.established_stress_reference
    return {
        "assessment_version": assessment.assessment_version,
        "scope": assessment.scope,
        "status": assessment.status,
        "unit_system": "SI",
        "policy": {
            "name": assessment.policy.policy_name,
            "version": assessment.policy.policy_version,
            "criteria": {
                "global_raw_peak": assessment.policy.global_raw_peak_role,
                "regional_maximum": assessment.policy.regional_maximum_rule,
                "regional_aggregate": assessment.policy.regional_aggregate_rule,
                "mesh_refinement_behavior": assessment.policy.required_maximum_mesh_behavior,
                "maximum_location_behavior": assessment.policy.required_maximum_location_behavior,
                "physical_region_identity": assessment.policy.physical_region_rule,
                "constraint_relationship": assessment.policy.constraint_relationship_rule,
                "model_assumptions": assessment.policy.model_assumption_rule,
                "local_peak_treatment": assessment.policy.required_local_peak_treatment,
            },
            "regional_evidence_identity": {
                "region_id": assessment.policy.regional_evidence_region_id,
                "region_version": assessment.policy.regional_evidence_region_version,
                "containing_band_id": assessment.policy.regional_containing_band_id,
            },
        },
        "established_stress_reference": (
            None
            if reference is None
            else {
                "region_id": reference.region_id,
                "region_version": reference.region_version,
                "extraction_method_id": reference.extraction_method_id,
                "value_pa": reference.value_pa,
                "source_kind": reference.source_kind,
            }
        ),
        "blocking_reasons": [
            {
                "code": item.code,
                "evidence_category": item.evidence_category,
                "explanation": item.explanation,
            }
            for item in assessment.blocking_reasons
        ],
        "candidate_investigation_regions": [
            {
                "region_id": item.region_id,
                "region_version": item.region_version,
                "selection_basis": item.selection_basis,
                "interpretation": item.interpretation,
            }
            for item in assessment.candidate_regions
        ],
        "model_assumption_limitations": [
            {
                "code": item.code,
                "description": item.description,
                "status": item.status,
            }
            for item in assessment.model_assumptions
        ],
        "next_evidence_requirements": [
            {
                "code": item.code,
                "evidence_category": item.evidence_category,
                "requirement": item.requirement,
            }
            for item in assessment.next_evidence_requirements
        ],
        "semantics": "stress evidence is not a design stress unless status is established",
    }


def critical_stress_assessment_to_json(assessment: CriticalStressAssessment) -> str:
    return json.dumps(
        critical_stress_assessment_to_dict(assessment),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
