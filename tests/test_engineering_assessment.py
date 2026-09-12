import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import (  # noqa: E402
    AnalysisResult,
    DisplacementSummary,
    GlobalRawMaximumVonMises,
    MeshSummary,
    StressSummary,
    build_equilibrium_evidence,
)
from bracket_assessment import build_bracket_assessment  # noqa: E402
from engineering_assessment import (  # noqa: E402
    AssessmentStatement,
    EngineeringAssessmentContext,
    EquilibriumAssessmentPolicy,
    build_engineering_assessment,
    engineering_assessment_to_dict,
    engineering_assessment_to_json,
)
from engineering_domain import ModelVersionReference  # noqa: E402
from numerical_results import StressTensor, Vector3  # noqa: E402


def result_with_balance(applied: Vector3, reaction: Vector3) -> AnalysisResult:
    stress = StressTensor(10.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return AnalysisResult(
        model_version=ModelVersionReference("assessment:test:v1"),
        mesh=MeshSummary(10, 4, "C3D10", 0.01),
        displacement=DisplacementSummary(
            5.0,
            9,
            Vector3(3.0, 4.0, 0.0),
            Vector3(1.0, 2.0, 3.0),
        ),
        equilibrium=build_equilibrium_evidence(applied, reaction),
        stress=StressSummary(
            GlobalRawMaximumVonMises(
                10.0,
                12,
                3,
                stress,
                Vector3(0.1, 0.2, 0.3),
            )
        ),
    )


POLICY = EquilibriumAssessmentPolicy("test_force_balance", "1", 0.1, 0.0, 0.0)
CONTEXT = EngineeringAssessmentContext(
    "declared_test_location",
    assumptions=(AssessmentStatement("test_assumption", "A declared assumption."),),
    warnings=(AssessmentStatement("test_warning", "A declared warning."),),
)


def assess(applied: Vector3, reaction: Vector3):
    return build_engineering_assessment(
        result_with_balance(applied, reaction), POLICY, CONTEXT
    )


class EngineeringAssessmentTests(unittest.TestCase):
    def test_equilibrium_status_includes_configured_tolerance_boundary(self) -> None:
        at_boundary = assess(Vector3(10.0, 0.0, 0.0), Vector3(-9.0, 0.0, 0.0))
        over_boundary = assess(Vector3(10.0, 0.0, 0.0), Vector3(-8.99, 0.0, 0.0))
        self.assertEqual(at_boundary.equilibrium.allowable_residual_magnitude_n, 1.0)
        self.assertEqual(at_boundary.equilibrium.status, "within_tolerance")
        self.assertEqual(at_boundary.equilibrium.scope, "numerical_consistency_only")
        self.assertEqual(at_boundary.equilibrium.governing_tolerance, "relative")
        self.assertEqual(over_boundary.equilibrium.status, "outside_tolerance")
        self.assertEqual(over_boundary.equilibrium.scope, "numerical_consistency_only")
        serialized = engineering_assessment_to_dict(at_boundary)["equilibrium_assessment"]
        self.assertEqual(serialized["scope"], "numerical_consistency_only")
        self.assertEqual(serialized["status"], "within_tolerance")
        self.assertNotIn("interpretation", serialized)

    def test_zero_and_near_zero_reference_use_absolute_policy_without_division(self) -> None:
        policy = EquilibriumAssessmentPolicy(
            "small_force_balance", "1", 1e-8, 1e-9, 1e-9
        )
        zero = build_engineering_assessment(
            result_with_balance(Vector3(0.0, 0.0, 0.0), Vector3(0.0, 0.0, 0.0)),
            policy,
            CONTEXT,
        )
        near_zero = build_engineering_assessment(
            result_with_balance(Vector3(1e-12, 0.0, 0.0), Vector3(0.0, 0.0, 0.0)),
            policy,
            CONTEXT,
        )
        self.assertIsNone(zero.equilibrium.relative_residual)
        self.assertEqual(zero.equilibrium.status, "within_tolerance")
        self.assertIsNone(near_zero.equilibrium.relative_residual)
        self.assertEqual(near_zero.equilibrium.governing_tolerance, "absolute")
        self.assertEqual(near_zero.equilibrium.status, "within_tolerance")
        serialized = engineering_assessment_to_dict(near_zero)["equilibrium_assessment"]
        self.assertIsNone(serialized["relative_residual"])
        self.assertEqual(
            serialized["policy"]["minimum_relative_reference_force_n"], 1e-9
        )

    def test_displacement_and_raw_stress_evidence_are_preserved(self) -> None:
        source = result_with_balance(Vector3(10.0, 0.0, 0.0), Vector3(-10.0, 0.0, 0.0))
        assessment = build_engineering_assessment(source, POLICY, CONTEXT)
        self.assertEqual(assessment.displacement.magnitude_m, 5.0)
        self.assertEqual(assessment.displacement.vector_m, Vector3(3.0, 4.0, 0.0))
        self.assertEqual(assessment.displacement.node_id, 9)
        self.assertEqual(assessment.displacement.position_m, Vector3(1.0, 2.0, 3.0))
        self.assertEqual(assessment.stress.interpretation, "diagnostic_only")
        self.assertEqual(assessment.stress.element_id, 12)
        self.assertEqual(assessment.stress.integration_point, 3)
        self.assertEqual(assessment.stress.position_m, Vector3(0.1, 0.2, 0.3))

    def test_bracket_context_has_required_assumptions_and_warnings(self) -> None:
        assessment = build_bracket_assessment(
            result_with_balance(Vector3(2500.0, 0.0, 0.0), Vector3(-2500.0, 0.0, 0.0))
        )
        assumption_codes = {item.code for item in assessment.assumptions}
        warning_codes = {item.code for item in assessment.warnings}
        self.assertEqual(
            assessment.equilibrium.policy.policy_name,
            "controlled_bracket_force_balance",
        )
        self.assertEqual(assessment.equilibrium.policy.policy_version, "1")
        self.assertEqual(assessment.equilibrium.status, "within_tolerance")
        self.assertIn("fully_fixed_mounting_holes", assumption_codes)
        self.assertEqual(
            {
                "global_raw_stress_diagnostic_only",
                "stress_convergence_not_demonstrated",
                "fixed_boundary_peak_influence",
            },
            warning_codes,
        )
        self.assertNotIn("singularity", engineering_assessment_to_json(assessment).lower())

    def test_serialization_is_deterministic_immutable_and_has_no_design_decision(self) -> None:
        source = result_with_balance(Vector3(10.0, 0.0, 0.0), Vector3(-10.0, 0.0, 0.0))
        first = build_engineering_assessment(source, POLICY, CONTEXT)
        second = build_engineering_assessment(source, POLICY, CONTEXT)
        self.assertEqual(engineering_assessment_to_dict(first), engineering_assessment_to_dict(second))
        self.assertEqual(engineering_assessment_to_json(first), engineering_assessment_to_json(second))
        with self.assertRaises(FrozenInstanceError):
            first.assessment_version = "changed"
        with self.assertRaises(TypeError):
            first.warnings[0] = AssessmentStatement("changed", "Changed.")

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        serialized = json.loads(engineering_assessment_to_json(first))
        forbidden = {"factor_of_safety", "fos", "pass", "passed", "failed", "structural_status"}
        self.assertTrue(forbidden.isdisjoint(keys(serialized)))


if __name__ == "__main__":
    unittest.main()
