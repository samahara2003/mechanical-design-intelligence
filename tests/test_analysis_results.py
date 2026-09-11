import json
import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import (  # noqa: E402
    AnalysisResultBuildError,
    EvidenceWarning,
    ResolvedAnalysisContext,
    analysis_result_to_dict,
    build_analysis_result,
    build_equilibrium_evidence,
    summarize_displacements,
    summarize_stresses,
)
from axial_bar_definition import axial_bar_analysis_definition  # noqa: E402
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NodalDisplacement,
    NodalReaction,
    NumericalResult,
    StressTensor,
    Vector3,
)


class AnalysisResultEvidenceTests(unittest.TestCase):
    def test_displacement_summary_retains_vector_identity_and_location(self) -> None:
        summary = summarize_displacements(
            (
                NodalDisplacement(9, Vector3(0.0, 2.0, 0.0)),
                NodalDisplacement(3, Vector3(3.0, 4.0, 0.0)),
            ),
            {3: Vector3(1.0, 0.02, 0.03)},
        )
        self.assertEqual(summary.global_maximum_magnitude_m, 5.0)
        self.assertEqual(summary.node_id, 3)
        self.assertEqual(summary.vector_m, Vector3(3.0, 4.0, 0.0))
        self.assertEqual(summary.location_m, Vector3(1.0, 0.02, 0.03))

    def test_displacement_tie_uses_lowest_node_id(self) -> None:
        summary = summarize_displacements(
            (
                NodalDisplacement(8, Vector3(-1.0, 0.0, 0.0)),
                NodalDisplacement(2, Vector3(1.0, 0.0, 0.0)),
            )
        )
        self.assertEqual(summary.node_id, 2)
        self.assertEqual(summary.vector_m, Vector3(1.0, 0.0, 0.0))

    def test_empty_displacement_data_fails_clearly(self) -> None:
        with self.assertRaises(AnalysisResultBuildError):
            summarize_displacements(())

    def test_stress_summary_selects_raw_peak_and_retains_tensor(self) -> None:
        uniaxial = StressTensor(100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        shear = StressTensor(0.0, 0.0, 0.0, 100.0, 0.0, 0.0)
        summary = summarize_stresses(
            (
                IntegrationPointStress(4, 2, uniaxial),
                IntegrationPointStress(7, 3, shear),
            ),
            {(7, 3): Vector3(0.1, 0.2, 0.3)},
        )
        peak = summary.global_raw_max_von_mises
        self.assertAlmostEqual(peak.von_mises_pa, math.sqrt(3.0) * 100.0)
        self.assertEqual((peak.element_id, peak.integration_point), (7, 3))
        self.assertIs(peak.stress_pa, shear)
        self.assertEqual(peak.location_m, Vector3(0.1, 0.2, 0.3))
        self.assertEqual(summary.representation, "raw_integration_point_cauchy_stress")

    def test_stress_tie_uses_lowest_element_then_integration_point(self) -> None:
        stress = StressTensor(50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        summary = summarize_stresses(
            (
                IntegrationPointStress(6, 1, stress),
                IntegrationPointStress(2, 4, stress),
                IntegrationPointStress(2, 1, stress),
            )
        )
        peak = summary.global_raw_max_von_mises
        self.assertEqual((peak.element_id, peak.integration_point), (2, 1))

    def test_empty_stress_data_fails_clearly(self) -> None:
        with self.assertRaises(AnalysisResultBuildError):
            summarize_stresses(())

    def test_equilibrium_exposes_exact_and_small_residuals_without_sign_change(self) -> None:
        exact = build_equilibrium_evidence(
            Vector3(1000.0, 0.0, 0.0), Vector3(-1000.0, 0.0, 0.0)
        )
        self.assertEqual(exact.imbalance_n, Vector3(0.0, 0.0, 0.0))
        self.assertEqual(exact.residual_magnitude_n, 0.0)
        self.assertEqual(exact.relative_imbalance, 0.0)

        small = build_equilibrium_evidence(
            Vector3(1000.0, 2.0, 0.0), Vector3(-999.999, -1.5, 0.0)
        )
        self.assertAlmostEqual(small.imbalance_n.x, 0.001)
        self.assertEqual(small.imbalance_n.y, 0.5)
        self.assertAlmostEqual(small.residual_magnitude_n, math.hypot(0.001, 0.5))
        denominator = max(math.hypot(1000.0, 2.0), math.hypot(999.999, 1.5))
        self.assertAlmostEqual(small.relative_imbalance, small.residual_magnitude_n / denominator)

        zero = build_equilibrium_evidence(
            Vector3(0.0, 0.0, 0.0), Vector3(0.0, 0.0, 0.0)
        )
        self.assertIsNone(zero.relative_imbalance)

    def test_analysis_result_is_nested_immutable_and_serializes_deterministically(self) -> None:
        warning_source = [EvidenceWarning("solver_message", "Recorded deterministic message")]
        result = build_analysis_result(
            axial_bar_analysis_definition("4.15.2", "2.23"),
            NumericalResult(
                displacements=(NodalDisplacement(1, Vector3(1.0, 0.0, 0.0)),),
                reactions=(NodalReaction(2, Vector3(-10.0, 0.0, 0.0)),),
                integration_point_stresses=(
                    IntegrationPointStress(
                        4, 1, StressTensor(20.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    ),
                ),
                reaction_resultant_n=Vector3(-10.0, 0.0, 0.0),
            ),
            ResolvedAnalysisContext(
                node_count=2,
                element_count=1,
                integrated_applied_resultant_n=Vector3(10.0, 0.0, 0.0),
                warnings=warning_source,
            ),
        )
        warning_source.append(EvidenceWarning("later_message", "Not retained"))
        self.assertIsInstance(result.warnings, tuple)
        self.assertEqual(len(result.warnings), 1)
        with self.assertRaises(FrozenInstanceError):
            result.mesh.node_count = 3

        serialized = analysis_result_to_dict(result)
        first = json.dumps(serialized, separators=(",", ":"))
        second = json.dumps(analysis_result_to_dict(result), separators=(",", ":"))
        self.assertEqual(first, second)
        self.assertNotIn("NumericalResult", first)
        self.assertNotIn("Calculix", first)
        self.assertNotIn(".dat", first)
        self.assertNotIn("*", first)
        self.assertNotIn("displacements", serialized)
        self.assertNotIn("integration_point_stresses", serialized)


if __name__ == "__main__":
    unittest.main()
