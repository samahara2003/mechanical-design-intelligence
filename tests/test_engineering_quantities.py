import json
import sys
import unittest
from dataclasses import replace
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
from bracket_definition import BRACKET_LOAD_FACE, bracket_analysis_definition  # noqa: E402
from bracket_quantities import (  # noqa: E402
    BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY,
    BRACKET_DISCRETIZATION_ESTIMATE_POLICY,
    BRACKET_MESH_STUDY_ID,
    BRACKET_MESH_STUDY_VERSION,
    LOAD_PAD_AVERAGE_UX,
    LOAD_PAD_UX_REFINEMENT_POLICY,
)
from engineering_quantities import (  # noqa: E402
    QuantityAggregation,
    QuantityComponent,
    QuantityEvaluation,
    assess_raw_stress_discretization_eligibility,
    asymptotic_consistency_evidence_to_dict,
    build_asymptotic_consistency_evidence,
    build_mesh_convergence_study,
    build_raw_stress_mesh_trend,
    compare_mesh_refinement,
    compare_raw_stress_diagnostic,
    discretization_error_estimate_to_dict,
    estimate_mesh_discretization_error,
    evaluate_regional_displacement,
    mesh_refinement_comparison_to_dict,
    mesh_convergence_study_to_dict,
    quantity_evaluation_to_dict,
    quantity_of_interest_to_json,
    raw_stress_diagnostic_to_dict,
    raw_stress_mesh_trend_to_dict,
)
from numerical_results import (  # noqa: E402
    NodalDisplacement,
    NumericalResult,
    StressTensor,
    Vector3,
)


BASELINE_UX_M = 0.000580187226580943
FINER_UX_M = 0.0005826523352911262
COARSE_UX_M = 0.0005730201096575444

NODES = {
    1: (0.0, 0.0, 0.0),
    2: (0.0, 1.0, 0.0),
    3: (0.0, 0.0, 1.0),
    4: (0.0, 0.5, 0.0),
    5: (0.0, 0.5, 0.5),
    6: (0.0, 0.0, 0.5),
}
FACES = ({"id": 101, "nodes": (1, 2, 3, 4, 5, 6)},)


def analysis_result(mesh_size_m: float, stress_pa: float, nodes: int) -> AnalysisResult:
    tensor = StressTensor(stress_pa, 0.0, 0.0, 0.0, 0.0, 0.0)
    return AnalysisResult(
        model_version=bracket_analysis_definition("4.15.2", "2.23").model_version,
        mesh=MeshSummary(nodes, nodes // 2, "C3D10", mesh_size_m),
        displacement=DisplacementSummary(0.001, 1, Vector3(0.001, 0.0, 0.0)),
        equilibrium=build_equilibrium_evidence(
            Vector3(2500.0, 0.0, 0.0), Vector3(-2500.0, 0.0, 0.0)
        ),
        stress=StressSummary(
            GlobalRawMaximumVonMises(
                stress_pa, 20, 1, tensor, Vector3(0.04, 0.025, 0.011)
            )
        ),
    )


def numerical_result(ux_m: float) -> NumericalResult:
    return NumericalResult(
        displacements=tuple(
            NodalDisplacement(node_id, Vector3(ux_m, 0.0, 0.0))
            for node_id in NODES
        ),
        reactions=(),
        integration_point_stresses=(),
    )


def evaluation(mesh_size_m: float, ux_m: float, mesh_identity: str) -> QuantityEvaluation:
    definition = bracket_analysis_definition("4.15.2", "2.23", mesh_size_m)
    nodes = {0.009: 50, 0.006: 100, 0.004: 200}[mesh_size_m]
    result = analysis_result(mesh_size_m, 1.0, nodes)
    return evaluate_regional_displacement(
        LOAD_PAD_AVERAGE_UX,
        definition,
        result,
        numerical_result(ux_m),
        resolved_region_identity="gmsh_physical_surface:load_pad",
        mesh_identity=mesh_identity,
        faces=FACES,
        node_coordinates_m=NODES,
    )


class EngineeringQuantityTests(unittest.TestCase):
    def test_qoi_is_deterministic_geometry_intent_without_mesh_ids(self) -> None:
        first = quantity_of_interest_to_json(LOAD_PAD_AVERAGE_UX)
        second = quantity_of_interest_to_json(LOAD_PAD_AVERAGE_UX)
        self.assertEqual(first, second)
        serialized = json.loads(first)
        self.assertEqual(serialized["quantity_id"], "load_pad_average_ux")
        self.assertEqual(serialized["target"], {"region_name": "load_pad", "entity": "face"})
        self.assertNotIn("node", first.lower())
        self.assertNotIn("element", first.lower())

    def test_baseline_and_finer_load_pad_average_ux_evaluation(self) -> None:
        baseline = evaluation(0.006, BASELINE_UX_M, "controlled_bracket:baseline")
        finer = evaluation(0.004, FINER_UX_M, "controlled_bracket:finer")
        self.assertEqual(baseline.value, BASELINE_UX_M)
        self.assertEqual(finer.value, FINER_UX_M)
        self.assertEqual(baseline.quantity.target, BRACKET_LOAD_FACE)
        self.assertEqual(baseline.integrated_region_area_m2, 0.5)
        self.assertEqual(baseline.evaluation_method, "three_point_c3d10_surface_quadrature")
        first = json.dumps(quantity_evaluation_to_dict(baseline), sort_keys=True)
        second = json.dumps(
            quantity_evaluation_to_dict(
                evaluation(0.006, BASELINE_UX_M, "controlled_bracket:baseline")
            ),
            sort_keys=True,
        )
        self.assertEqual(first, second)

    def test_regional_maximum_support_is_explicitly_nodal(self) -> None:
        quantity = replace(
            LOAD_PAD_AVERAGE_UX,
            component=QuantityComponent.MAGNITUDE,
            aggregation=QuantityAggregation.MAXIMUM,
        )
        displacements = tuple(
            NodalDisplacement(
                node_id,
                Vector3(3.0, 4.0, 0.0) if node_id == 6 else Vector3(1.0, 0.0, 0.0),
            )
            for node_id in NODES
        )
        evaluated = evaluate_regional_displacement(
            quantity,
            bracket_analysis_definition("4.15.2", "2.23", 0.006),
            analysis_result(0.006, 1.0, 100),
            NumericalResult(displacements, (), ()),
            resolved_region_identity="gmsh_physical_surface:load_pad",
            mesh_identity="synthetic:mesh",
            faces=FACES,
            node_coordinates_m=NODES,
        )
        self.assertEqual(evaluated.value, 5.0)
        self.assertEqual(evaluated.evaluation_method, "resolved_surface_nodal_maximum")

    def test_known_bracket_comparison_is_within_case_specific_tolerance(self) -> None:
        comparison = compare_mesh_refinement(
            evaluation(0.006, BASELINE_UX_M, "controlled_bracket:baseline"),
            evaluation(0.004, FINER_UX_M, "controlled_bracket:finer"),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertAlmostEqual(comparison.relative_change, 0.004248815894672568)
        self.assertEqual(comparison.status, "within_tolerance")
        self.assertEqual(comparison.scope, "mesh_refinement_comparison_only")
        serialized = mesh_refinement_comparison_to_dict(comparison)
        self.assertEqual(serialized["policy"]["relative_change_tolerance"], 0.01)
        text = json.dumps(serialized, sort_keys=True).lower()
        for forbidden in ("converged", "factor_of_safety", '"fos"', '"pass"', '"fail"'):
            self.assertNotIn(forbidden, text)

    def test_outside_tolerance_and_near_zero_reference_semantics(self) -> None:
        baseline = evaluation(0.006, 1.0, "controlled_bracket:baseline")
        outside = compare_mesh_refinement(
            baseline,
            evaluation(0.004, 1.02, "controlled_bracket:finer"),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertEqual(outside.status, "outside_tolerance")
        near_zero = compare_mesh_refinement(
            evaluation(0.006, 1e-13, "controlled_bracket:baseline"),
            evaluation(0.004, 2e-13, "controlled_bracket:finer"),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertIsNone(near_zero.relative_change)
        self.assertEqual(near_zero.status, "relative_change_not_applicable")

    def test_incompatible_comparisons_are_rejected(self) -> None:
        baseline = evaluation(0.006, 1.0, "controlled_bracket:baseline")
        finer = evaluation(0.004, 1.0, "controlled_bracket:finer")
        incompatible_quantity = replace(
            finer,
            quantity=replace(
                finer.quantity,
                component=QuantityComponent.UY,
                aggregation=QuantityAggregation.MAXIMUM,
            ),
        )
        with self.assertRaisesRegex(ValueError, "same quantity"):
            compare_mesh_refinement(
                baseline, incompatible_quantity, LOAD_PAD_UX_REFINEMENT_POLICY
            )
        with self.assertRaisesRegex(ValueError, "resolved region"):
            compare_mesh_refinement(
                baseline,
                replace(finer, resolved_region_identity="another:load_pad"),
                LOAD_PAD_UX_REFINEMENT_POLICY,
            )
        changed_definition = replace(
            bracket_analysis_definition("4.15.2", "2.23", 0.004),
            material=replace(
                bracket_analysis_definition("4.15.2", "2.23", 0.004).material,
                poissons_ratio=0.29,
            ),
        )
        changed_basis = evaluate_regional_displacement(
            LOAD_PAD_AVERAGE_UX,
            changed_definition,
            analysis_result(0.004, 1.0, 200),
            numerical_result(1.0),
            resolved_region_identity="gmsh_physical_surface:load_pad",
            mesh_identity="controlled_bracket:finer",
            faces=FACES,
            node_coordinates_m=NODES,
        )
        with self.assertRaisesRegex(ValueError, "more than characteristic mesh size"):
            compare_mesh_refinement(
                baseline, changed_basis, LOAD_PAD_UX_REFINEMENT_POLICY
            )

    def test_raw_stress_change_remains_diagnostic_without_decision_semantics(self) -> None:
        diagnostic = compare_raw_stress_diagnostic(
            analysis_result(0.006, 145277077.43112603, 17314),
            analysis_result(0.004, 164411764.50266418, 47836),
        )
        serialized = raw_stress_diagnostic_to_dict(diagnostic)
        self.assertEqual(serialized["interpretation"], "diagnostic_only")
        self.assertAlmostEqual(serialized["relative_change"], 0.1317116740637191)
        text = json.dumps(serialized, sort_keys=True).lower()
        for forbidden in ("converged", "factor_of_safety", '"fos"', '"status"', '"pass"'):
            self.assertNotIn(forbidden, text)

    def test_three_level_study_is_ordered_compatible_and_deterministic(self) -> None:
        evaluations = (
            evaluation(0.009, 0.00055, "mesh:coarse"),
            evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
            evaluation(0.004, FINER_UX_M, "mesh:fine"),
        )
        study = build_mesh_convergence_study(
            BRACKET_MESH_STUDY_ID,
            BRACKET_MESH_STUDY_VERSION,
            evaluations,
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertEqual(
            tuple(item.mesh.characteristic_size_m for item in study.evaluations),
            (0.009, 0.006, 0.004),
        )
        self.assertEqual(len({item.quantity for item in study.evaluations}), 1)
        self.assertEqual(
            len({item.analysis_comparison_basis_sha256 for item in study.evaluations}), 1
        )
        self.assertEqual(len(study.adjacent_comparisons), 2)
        self.assertAlmostEqual(
            study.adjacent_comparisons[0].absolute_change,
            BASELINE_UX_M - 0.00055,
        )
        self.assertAlmostEqual(
            study.adjacent_comparisons[1].relative_change,
            (FINER_UX_M - BASELINE_UX_M) / BASELINE_UX_M,
        )
        self.assertEqual(study.trend, "stabilizing")
        first = json.dumps(mesh_convergence_study_to_dict(study), sort_keys=True)
        second = json.dumps(
            mesh_convergence_study_to_dict(
                build_mesh_convergence_study(
                    BRACKET_MESH_STUDY_ID,
                    BRACKET_MESH_STUDY_VERSION,
                    evaluations,
                    LOAD_PAD_UX_REFINEMENT_POLICY,
                )
            ),
            sort_keys=True,
        )
        self.assertEqual(first, second)
        for forbidden in ("converged", "factor_of_safety", '"fos"', '"pass"', '"fail"'):
            self.assertNotIn(forbidden, first.lower())

    def test_three_level_trend_not_stabilizing_and_indeterminate(self) -> None:
        not_stabilizing = build_mesh_convergence_study(
            "test_trend",
            "1",
            (
                evaluation(0.009, 1.0, "mesh:coarse"),
                evaluation(0.006, 1.1, "mesh:baseline"),
                evaluation(0.004, 1.25, "mesh:fine"),
            ),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertEqual(not_stabilizing.trend, "not_stabilizing")
        indeterminate = build_mesh_convergence_study(
            "test_near_zero",
            "1",
            (
                evaluation(0.009, 1e-13, "mesh:coarse"),
                evaluation(0.006, 2e-13, "mesh:baseline"),
                evaluation(0.004, 3e-13, "mesh:fine"),
            ),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        self.assertEqual(indeterminate.trend, "indeterminate")
        self.assertTrue(
            all(item.relative_change is None for item in indeterminate.adjacent_comparisons)
        )

    def test_three_level_study_rejects_a_different_quantity(self) -> None:
        evaluations = [
            evaluation(0.009, 1.0, "mesh:coarse"),
            evaluation(0.006, 1.1, "mesh:baseline"),
            evaluation(0.004, 1.11, "mesh:fine"),
        ]
        evaluations[1] = replace(
            evaluations[1],
            quantity=replace(evaluations[1].quantity, component=QuantityComponent.UY),
        )
        with self.assertRaisesRegex(ValueError, "same quantity"):
            build_mesh_convergence_study(
                "incompatible_study",
                "1",
                evaluations,
                LOAD_PAD_UX_REFINEMENT_POLICY,
            )

    def test_three_level_raw_stress_is_diagnostic_only(self) -> None:
        evaluations = (
            evaluation(0.009, 0.00055, "mesh:coarse"),
            evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
            evaluation(0.004, FINER_UX_M, "mesh:fine"),
        )
        results = (
            analysis_result(0.009, 130e6, 50),
            analysis_result(0.006, 145277077.43112603, 100),
            analysis_result(0.004, 164411764.50266418, 200),
        )
        study = build_raw_stress_mesh_trend(results, evaluations)
        serialized = raw_stress_mesh_trend_to_dict(study)
        self.assertEqual(serialized["interpretation"], "diagnostic_only")
        self.assertEqual(len(serialized["ordered_mesh_levels"]), 3)
        self.assertEqual(len(serialized["adjacent_changes"]), 2)
        text = json.dumps(serialized, sort_keys=True).lower()
        for forbidden in ("converged", "factor_of_safety", '"fos"', '"status"', '"pass"', '"fail"'):
            self.assertNotIn(forbidden, text)

    def test_bracket_discretization_estimate_regression_and_serialization(self) -> None:
        evaluations = (
            evaluation(0.009, COARSE_UX_M, "mesh:coarse"),
            evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
            evaluation(0.004, FINER_UX_M, "mesh:fine"),
        )
        study = build_mesh_convergence_study(
            BRACKET_MESH_STUDY_ID,
            BRACKET_MESH_STUDY_VERSION,
            evaluations,
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        estimate = estimate_mesh_discretization_error(
            study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        self.assertEqual(estimate.eligibility.status, "eligible")
        self.assertEqual(estimate.grid_sizes_m, (0.009, 0.006, 0.004))
        self.assertEqual(estimate.refinement_ratios, (1.5, 1.5))
        self.assertAlmostEqual(estimate.observed_order, 2.6322056859000242)
        self.assertAlmostEqual(
            estimate.richardson_extrapolated_value, 0.000583944710947966
        )
        self.assertAlmostEqual(
            estimate.signed_fine_to_extrapolated_difference,
            1.2923756568397962e-06,
        )
        self.assertAlmostEqual(estimate.approximate_relative_error, 0.0042308398351332405)
        self.assertAlmostEqual(estimate.fine_grid_gci, 0.0027726132261060472)
        serialized = discretization_error_estimate_to_dict(estimate)
        self.assertEqual(serialized["interpretation"], "qoi_discretization_evidence")
        self.assertEqual(serialized["policy"]["safety_factor"], 1.25)
        self.assertEqual(serialized["relative_values_semantics"], "dimensionless_fraction")
        self.assertEqual(serialized["asymptotic_range_assessment"], "not_established_v1")
        first = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
        second = json.dumps(
            discretization_error_estimate_to_dict(
                estimate_mesh_discretization_error(
                    study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
                )
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(first, second)
        for forbidden in ("factor_of_safety", '"fos"', '"pass"', '"fail"'):
            self.assertNotIn(forbidden, first.lower())

    def test_discretization_estimate_ineligible_sequences_have_null_formal_values(self) -> None:
        cases = (
            (
                "near_zero",
                (1.0, 1.0 + 5e-13, 1.0 + 8e-13),
                "successive_difference_below_policy_minimum",
            ),
            (
                "oscillatory",
                (1.0, 1.1, 1.05),
                "nonmonotonic_sequence",
            ),
            (
                "nonpositive_order",
                (1.0, 1.1, 1.25),
                "observed_order_not_positive",
            ),
        )
        for name, values, reason in cases:
            with self.subTest(name=name):
                study = build_mesh_convergence_study(
                    f"test_{name}",
                    "1",
                    tuple(
                        evaluation(size, value, f"mesh:{index}")
                        for index, (size, value) in enumerate(
                            zip((0.009, 0.006, 0.004), values)
                        )
                    ),
                    LOAD_PAD_UX_REFINEMENT_POLICY,
                )
                estimate = estimate_mesh_discretization_error(
                    study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
                )
                serialized = discretization_error_estimate_to_dict(estimate)
                self.assertEqual(serialized["eligibility"]["status"], "ineligible")
                self.assertEqual(serialized["eligibility"]["reason_code"], reason)
                for key in (
                    "observed_order",
                    "richardson_extrapolated_value",
                    "fine_grid_gci",
                    "approximate_relative_error",
                ):
                    self.assertIsNone(serialized[key])

    def test_nonuniform_ratio_is_explicitly_ineligible_and_invalid_order_is_rejected(self) -> None:
        evaluations = (
            evaluation(0.009, 1.0, "mesh:coarse"),
            evaluation(0.006, 1.1, "mesh:baseline"),
            replace(
                evaluation(0.004, 1.15, "mesh:fine"),
                mesh=replace(
                    evaluation(0.004, 1.15, "mesh:fine").mesh,
                    characteristic_size_m=0.0035,
                ),
            ),
        )
        study = build_mesh_convergence_study(
            "nonuniform", "1", evaluations, LOAD_PAD_UX_REFINEMENT_POLICY
        )
        estimate = estimate_mesh_discretization_error(
            study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        self.assertEqual(
            estimate.eligibility.reason_code, "nonuniform_refinement_ratio_unsupported"
        )
        with self.assertRaisesRegex(ValueError, "smaller characteristic mesh size"):
            build_mesh_convergence_study(
                "bad_order",
                "1",
                (evaluations[1], evaluations[0], evaluations[2]),
                LOAD_PAD_UX_REFINEMENT_POLICY,
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            replace(evaluations[0], value=float("nan"))

    def test_raw_stress_sequence_is_ineligible_for_formal_estimate(self) -> None:
        evaluations = (
            evaluation(0.009, COARSE_UX_M, "mesh:coarse"),
            evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
            evaluation(0.004, FINER_UX_M, "mesh:fine"),
        )
        results = (
            analysis_result(0.009, 143417306.94058615, 50),
            analysis_result(0.006, 145277077.43112603, 100),
            analysis_result(0.004, 164411764.50266418, 200),
        )
        diagnostic = build_raw_stress_mesh_trend(results, evaluations)
        estimate = assess_raw_stress_discretization_eligibility(
            diagnostic, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        serialized = discretization_error_estimate_to_dict(estimate)
        self.assertEqual(serialized["interpretation"], "diagnostic_only")
        self.assertEqual(serialized["eligibility"]["status"], "ineligible")
        self.assertEqual(
            serialized["eligibility"]["reason_code"], "observed_order_not_positive"
        )
        self.assertIsNone(serialized["observed_order"])
        self.assertIsNone(serialized["richardson_extrapolated_value"])
        self.assertIsNone(serialized["fine_grid_gci"])

    def test_bracket_adjacent_gci_pair_and_consistency_regression(self) -> None:
        study = build_mesh_convergence_study(
            BRACKET_MESH_STUDY_ID,
            BRACKET_MESH_STUDY_VERSION,
            (
                evaluation(0.009, COARSE_UX_M, "mesh:coarse"),
                evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
                evaluation(0.004, FINER_UX_M, "mesh:fine"),
            ),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        estimate = estimate_mesh_discretization_error(
            study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        evidence = build_asymptotic_consistency_evidence(
            estimate, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
        )
        self.assertAlmostEqual(estimate.coarse_medium_gci, 0.008095413417591617)
        self.assertAlmostEqual(estimate.fine_grid_gci, 0.0027726132261060472)
        self.assertAlmostEqual(evidence.consistency_ratio, 1.0042488158946725)
        self.assertEqual(evidence.status, "consistent")
        serialized = asymptotic_consistency_evidence_to_dict(evidence)
        self.assertEqual(serialized["gci_32"], estimate.coarse_medium_gci)
        self.assertEqual(serialized["gci_21"], estimate.fine_grid_gci)
        self.assertEqual(serialized["policy"]["target_ratio"], 1.0)
        self.assertEqual(serialized["policy"]["allowable_absolute_deviation"], 0.01)
        self.assertEqual(
            serialized["policy"]["formula_identity"],
            "gci_32_over_r_to_p_gci_21",
        )
        first = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
        second = json.dumps(
            asymptotic_consistency_evidence_to_dict(
                build_asymptotic_consistency_evidence(
                    estimate, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
                )
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(first, second)
        for forbidden in (
            "physical_validation",
            "factor_of_safety",
            '"fos"',
            '"failure"',
            '"pass"',
            "structural_pass_fail",
            "universal_convergence",
        ):
            self.assertNotIn(forbidden, first.lower())

    def test_asymptotic_consistency_outside_tolerance_and_denominator_policy(self) -> None:
        study = build_mesh_convergence_study(
            "synthetic_outside_consistency",
            "1",
            (
                evaluation(0.009, 1.0, "mesh:coarse"),
                evaluation(0.006, 2.0, "mesh:medium"),
                evaluation(0.004, 2.5, "mesh:fine"),
            ),
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        estimate = estimate_mesh_discretization_error(
            study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        evidence = build_asymptotic_consistency_evidence(
            estimate, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
        )
        self.assertAlmostEqual(evidence.consistency_ratio, 1.25)
        self.assertEqual(evidence.status, "outside_tolerance")
        denominator_guard = replace(
            BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY,
            minimum_denominator=1.0,
        )
        guarded = build_asymptotic_consistency_evidence(estimate, denominator_guard)
        self.assertEqual(guarded.status, "not_applicable")
        self.assertEqual(guarded.reason_code, "invalid_consistency_denominator")

    def test_ineligible_and_raw_stress_consistency_are_not_applicable(self) -> None:
        evaluations = (
            evaluation(0.009, COARSE_UX_M, "mesh:coarse"),
            evaluation(0.006, BASELINE_UX_M, "mesh:baseline"),
            evaluation(0.004, FINER_UX_M, "mesh:fine"),
        )
        raw_estimate = assess_raw_stress_discretization_eligibility(
            build_raw_stress_mesh_trend(
                (
                    analysis_result(0.009, 143417306.94058615, 50),
                    analysis_result(0.006, 145277077.43112603, 100),
                    analysis_result(0.004, 164411764.50266418, 200),
                ),
                evaluations,
            ),
            BRACKET_DISCRETIZATION_ESTIMATE_POLICY,
        )
        evidence = build_asymptotic_consistency_evidence(
            raw_estimate, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
        )
        serialized = asymptotic_consistency_evidence_to_dict(evidence)
        self.assertEqual(serialized["status"], "not_applicable")
        self.assertEqual(serialized["interpretation"], "diagnostic_only")
        self.assertEqual(
            serialized["reason_code"], "formal_discretization_estimate_ineligible"
        )
        for key in (
            "gci_32",
            "gci_21",
            "refinement_ratio",
            "observed_order",
            "asymptotic_consistency_ratio",
        ):
            self.assertIsNone(serialized[key])


if __name__ == "__main__":
    unittest.main()
