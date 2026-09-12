"""Explicit deterministic assessment policy/context for the controlled bracket."""

from analysis_results import AnalysisResult
from engineering_assessment import (
    AssessmentStatement,
    EngineeringAssessment,
    EngineeringAssessmentContext,
    EquilibriumAssessmentPolicy,
    build_engineering_assessment,
)


# These reuse the established bracket solver-check tolerance magnitudes under
# an explicit resultant-vector policy; they are named and serialized.
BRACKET_EQUILIBRIUM_POLICY = EquilibriumAssessmentPolicy(
    policy_name="controlled_bracket_force_balance",
    policy_version="1",
    relative_residual_tolerance=1.0e-8,
    absolute_residual_tolerance_n=2.0e-5,
    minimum_relative_reference_force_n=2.0e-5,
)

BRACKET_ASSESSMENT_CONTEXT = EngineeringAssessmentContext(
    stress_location_context="mounting_hole_vicinity_near_simplified_fixed_boundary",
    assumptions=(
        AssessmentStatement(
            "fully_fixed_mounting_holes",
            "The mounting-hole cylindrical faces are fixed in UX, UY, and UZ.",
        ),
        AssessmentStatement(
            "idealized_load_pad_traction",
            "The 2500 N global +X load is uniform vector traction on the load-pad face.",
        ),
    ),
    warnings=(
        AssessmentStatement(
            "global_raw_stress_diagnostic_only",
            "The global raw maximum is not automatically a design-critical stress.",
        ),
        AssessmentStatement(
            "stress_convergence_not_demonstrated",
            "Stress convergence has not been demonstrated.",
        ),
        AssessmentStatement(
            "fixed_boundary_peak_influence",
            "The peak is affected by proximity to the simplified fixed mounting boundary condition.",
        ),
    ),
)


def build_bracket_assessment(result: AnalysisResult) -> EngineeringAssessment:
    """Apply only the predeclared controlled-bracket interpretation."""
    return build_engineering_assessment(
        result,
        BRACKET_EQUILIBRIUM_POLICY,
        BRACKET_ASSESSMENT_CONTEXT,
    )
