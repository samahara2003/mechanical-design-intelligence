"""Immutable, solver-neutral interpretation of deterministic analysis evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from analysis_results import AnalysisResult
from engineering_postprocessing import vector_magnitude
from numerical_results import StressTensor, Vector3


ASSESSMENT_VERSION = "engineering-assessment/1"


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


@dataclass(frozen=True)
class AssessmentStatement:
    """Stable structured assumption or warning for downstream consumers."""

    code: str
    message: str

    def __post_init__(self) -> None:
        code = _required_text(self.code, "assessment statement code")
        if re.fullmatch(r"[a-z][a-z0-9_]*", code) is None:
            raise ValueError("assessment statement code must use lower snake case")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", _required_text(self.message, "assessment statement"))


@dataclass(frozen=True)
class EquilibriumAssessmentPolicy:
    """Versioned numerical force-balance policy in SI units.

    The allowable residual is the larger of the absolute tolerance and the
    relative tolerance times the larger applied/reaction resultant magnitude.
    This avoids dividing by zero or an unstable near-zero denominator.
    """

    policy_name: str
    policy_version: str
    relative_residual_tolerance: float
    absolute_residual_tolerance_n: float
    minimum_relative_reference_force_n: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "policy_name", _required_text(self.policy_name, "policy name")
        )
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy version")
        )
        if (
            not math.isfinite(self.relative_residual_tolerance)
            or self.relative_residual_tolerance < 0.0
        ):
            raise ValueError("relative equilibrium tolerance must be finite and nonnegative")
        if (
            not math.isfinite(self.absolute_residual_tolerance_n)
            or self.absolute_residual_tolerance_n < 0.0
        ):
            raise ValueError("absolute equilibrium tolerance must be finite and nonnegative")
        if (
            not math.isfinite(self.minimum_relative_reference_force_n)
            or self.minimum_relative_reference_force_n < 0.0
        ):
            raise ValueError("minimum relative reference force must be finite and nonnegative")


@dataclass(frozen=True)
class EngineeringAssessmentContext:
    """Explicit engineering context not inferred from generic result fields."""

    stress_location_context: str
    assumptions: tuple[AssessmentStatement, ...] = ()
    warnings: tuple[AssessmentStatement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stress_location_context",
            _required_text(self.stress_location_context, "stress location context"),
        )
        assumptions = tuple(self.assumptions)
        warnings = tuple(self.warnings)
        if any(not isinstance(item, AssessmentStatement) for item in assumptions + warnings):
            raise TypeError("assessment assumptions and warnings must be AssessmentStatement values")
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "warnings", warnings)


@dataclass(frozen=True)
class EquilibriumAssessment:
    """Numerical force-balance interpretation, never structural acceptance."""

    applied_force_n: Vector3
    reaction_force_n: Vector3
    residual_force_n: Vector3
    residual_magnitude_n: float
    relative_residual: float | None
    reference_force_magnitude_n: float
    allowable_residual_magnitude_n: float
    governing_tolerance: str
    status: str
    policy: EquilibriumAssessmentPolicy
    scope: str = field(default="numerical_consistency_only", init=False)

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, Vector3)
            for value in (self.applied_force_n, self.reaction_force_n, self.residual_force_n)
        ):
            raise TypeError("equilibrium forces must be Vector3 values")
        for value, label in (
            (self.residual_magnitude_n, "residual magnitude"),
            (self.reference_force_magnitude_n, "reference force magnitude"),
            (self.allowable_residual_magnitude_n, "allowable residual magnitude"),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{label} must be finite and nonnegative")
        if self.relative_residual is not None and (
            not math.isfinite(self.relative_residual) or self.relative_residual < 0.0
        ):
            raise ValueError("relative residual must be finite and nonnegative when defined")
        if self.governing_tolerance not in {"absolute", "relative"}:
            raise ValueError("governing tolerance must be absolute or relative")
        if self.status not in {"within_tolerance", "outside_tolerance"}:
            raise ValueError("equilibrium status must describe the configured tolerance result")
        if not isinstance(self.policy, EquilibriumAssessmentPolicy):
            raise TypeError("equilibrium assessment must retain its explicit policy")


@dataclass(frozen=True)
class DisplacementEvidence:
    """Preserved global maximum-displacement evidence from AnalysisResult."""

    magnitude_m: float
    vector_m: Vector3
    node_id: int
    position_m: Vector3 | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.magnitude_m) or self.magnitude_m < 0.0:
            raise ValueError("displacement magnitude must be finite and nonnegative")
        if not isinstance(self.vector_m, Vector3):
            raise TypeError("displacement vector must be a Vector3")
        if not isinstance(self.node_id, int) or self.node_id <= 0:
            raise ValueError("displacement node ID must be positive")
        if self.position_m is not None and not isinstance(self.position_m, Vector3):
            raise TypeError("displacement position must be a Vector3 when available")


@dataclass(frozen=True)
class StressEvidence:
    """Global raw stress peak retained as a traceable diagnostic only."""

    representation: str
    von_mises_pa: float
    element_id: int
    integration_point: int
    stress_pa: StressTensor
    position_m: Vector3 | None
    location_context: str
    interpretation: str = field(default="diagnostic_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "representation",
            _required_text(self.representation, "stress representation"),
        )
        object.__setattr__(
            self,
            "location_context",
            _required_text(self.location_context, "stress location context"),
        )
        if not math.isfinite(self.von_mises_pa) or self.von_mises_pa < 0.0:
            raise ValueError("von Mises stress must be finite and nonnegative")
        if not isinstance(self.element_id, int) or self.element_id <= 0:
            raise ValueError("stress element ID must be positive")
        if not isinstance(self.integration_point, int) or self.integration_point <= 0:
            raise ValueError("stress integration-point identity must be positive")
        if not isinstance(self.stress_pa, StressTensor):
            raise TypeError("stress evidence tensor must be a StressTensor")
        if self.position_m is not None and not isinstance(self.position_m, Vector3):
            raise TypeError("stress position must be a Vector3 when available")


@dataclass(frozen=True)
class EngineeringAssessment:
    """Deterministic interpretation above one immutable AnalysisResult."""

    assessment_version: str
    equilibrium: EquilibriumAssessment
    displacement: DisplacementEvidence
    stress: StressEvidence
    assumptions: tuple[AssessmentStatement, ...]
    warnings: tuple[AssessmentStatement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "assessment_version",
            _required_text(self.assessment_version, "assessment version"),
        )
        if not isinstance(self.equilibrium, EquilibriumAssessment):
            raise TypeError("assessment equilibrium must be EquilibriumAssessment")
        if not isinstance(self.displacement, DisplacementEvidence):
            raise TypeError("assessment displacement must be DisplacementEvidence")
        if not isinstance(self.stress, StressEvidence):
            raise TypeError("assessment stress must be StressEvidence")
        assumptions = tuple(self.assumptions)
        warnings = tuple(self.warnings)
        if any(not isinstance(item, AssessmentStatement) for item in assumptions + warnings):
            raise TypeError("assessment assumptions and warnings must be AssessmentStatement values")
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "warnings", warnings)


def _build_equilibrium(
    result: AnalysisResult,
    policy: EquilibriumAssessmentPolicy,
) -> EquilibriumAssessment:
    evidence = result.equilibrium
    reference = max(
        vector_magnitude(evidence.applied_resultant_n),
        vector_magnitude(evidence.reaction_resultant_n),
    )
    relative_allowance = policy.relative_residual_tolerance * reference
    absolute_allowance = policy.absolute_residual_tolerance_n
    allowable = max(absolute_allowance, relative_allowance)
    if absolute_allowance >= relative_allowance:
        governing = "absolute"
    else:
        governing = "relative"
    relative_residual = (
        None
        if reference <= policy.minimum_relative_reference_force_n
        else evidence.residual_magnitude_n / reference
    )
    return EquilibriumAssessment(
        applied_force_n=evidence.applied_resultant_n,
        reaction_force_n=evidence.reaction_resultant_n,
        residual_force_n=evidence.imbalance_n,
        residual_magnitude_n=evidence.residual_magnitude_n,
        relative_residual=relative_residual,
        reference_force_magnitude_n=reference,
        allowable_residual_magnitude_n=allowable,
        governing_tolerance=governing,
        status=(
            "within_tolerance"
            if evidence.residual_magnitude_n <= allowable
            else "outside_tolerance"
        ),
        policy=policy,
    )


def build_engineering_assessment(
    result: AnalysisResult,
    policy: EquilibriumAssessmentPolicy,
    context: EngineeringAssessmentContext,
    *,
    assessment_version: str = ASSESSMENT_VERSION,
) -> EngineeringAssessment:
    """Interpret objective result evidence using only explicit policy/context."""
    if not isinstance(result, AnalysisResult):
        raise TypeError("assessment source must be an AnalysisResult")
    if not isinstance(policy, EquilibriumAssessmentPolicy):
        raise TypeError("assessment policy must be EquilibriumAssessmentPolicy")
    if not isinstance(context, EngineeringAssessmentContext):
        raise TypeError("assessment context must be EngineeringAssessmentContext")
    peak = result.stress.global_raw_max_von_mises
    result_warnings = tuple(
        AssessmentStatement(item.code, item.message) for item in result.warnings
    )
    return EngineeringAssessment(
        assessment_version=assessment_version,
        equilibrium=_build_equilibrium(result, policy),
        displacement=DisplacementEvidence(
            magnitude_m=result.displacement.global_maximum_magnitude_m,
            vector_m=result.displacement.vector_m,
            node_id=result.displacement.node_id,
            position_m=result.displacement.location_m,
        ),
        stress=StressEvidence(
            representation=result.stress.representation,
            von_mises_pa=peak.von_mises_pa,
            element_id=peak.element_id,
            integration_point=peak.integration_point,
            stress_pa=peak.stress_pa,
            position_m=peak.location_m,
            location_context=context.stress_location_context,
        ),
        assumptions=context.assumptions,
        warnings=result_warnings + context.warnings,
    )


def _vector_to_list(vector: Vector3 | None) -> list[float] | None:
    return None if vector is None else list(vector.as_tuple())


def engineering_assessment_to_dict(assessment: EngineeringAssessment) -> dict:
    """Return the stable JSON-compatible EngineeringAssessment contract."""
    equilibrium = assessment.equilibrium
    stress = assessment.stress
    return {
        "assessment_version": assessment.assessment_version,
        "unit_system": "SI",
        "equilibrium_assessment": {
            "scope": equilibrium.scope,
            "applied_force_n": _vector_to_list(equilibrium.applied_force_n),
            "reaction_force_n": _vector_to_list(equilibrium.reaction_force_n),
            "residual_force_n": _vector_to_list(equilibrium.residual_force_n),
            "residual_magnitude_n": equilibrium.residual_magnitude_n,
            "relative_residual": equilibrium.relative_residual,
            "reference_force_magnitude_n": equilibrium.reference_force_magnitude_n,
            "allowable_residual_magnitude_n": equilibrium.allowable_residual_magnitude_n,
            "governing_tolerance": equilibrium.governing_tolerance,
            "status": equilibrium.status,
            "policy": {
                "name": equilibrium.policy.policy_name,
                "version": equilibrium.policy.policy_version,
                "relative_residual_tolerance": equilibrium.policy.relative_residual_tolerance,
                "absolute_residual_tolerance_n": equilibrium.policy.absolute_residual_tolerance_n,
                "minimum_relative_reference_force_n": (
                    equilibrium.policy.minimum_relative_reference_force_n
                ),
                "combination_rule": (
                    "max(absolute_tolerance_n, relative_tolerance * "
                    "reference_force_magnitude_n)"
                ),
                "reference_force_rule": "max(norm(applied_force_n), norm(reaction_force_n))",
            },
        },
        "displacement_evidence": {
            "magnitude_m": assessment.displacement.magnitude_m,
            "vector_m": _vector_to_list(assessment.displacement.vector_m),
            "node_id": assessment.displacement.node_id,
            "position_m": _vector_to_list(assessment.displacement.position_m),
        },
        "stress_evidence": {
            "interpretation": stress.interpretation,
            "representation": stress.representation,
            "von_mises_pa": stress.von_mises_pa,
            "element_id": stress.element_id,
            "integration_point": stress.integration_point,
            "position_m": _vector_to_list(stress.position_m),
            "location_context": stress.location_context,
            "stress_tensor_pa": {
                "sigma_xx": stress.stress_pa.sigma_xx_pa,
                "sigma_yy": stress.stress_pa.sigma_yy_pa,
                "sigma_zz": stress.stress_pa.sigma_zz_pa,
                "sigma_xy": stress.stress_pa.sigma_xy_pa,
                "sigma_xz": stress.stress_pa.sigma_xz_pa,
                "sigma_yz": stress.stress_pa.sigma_yz_pa,
            },
        },
        "assumptions": [
            {"code": item.code, "message": item.message} for item in assessment.assumptions
        ],
        "warnings": [
            {"code": item.code, "message": item.message} for item in assessment.warnings
        ],
    }


def engineering_assessment_to_json(assessment: EngineeringAssessment) -> str:
    """Serialize canonically for repeatable persistence and regression checks."""
    return json.dumps(
        engineering_assessment_to_dict(assessment),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
