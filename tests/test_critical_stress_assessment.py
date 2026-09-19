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
from bracket_definition import bracket_analysis_definition  # noqa: E402
from bracket_quantities import (  # noqa: E402
    BRACKET_CRITICAL_STRESS_MODEL_ASSUMPTIONS,
    BRACKET_CRITICAL_STRESS_POLICY,
    BRACKET_STRESS_SPATIAL_BANDS,
    BRACKET_STRESS_SPATIAL_POLICY,
    LOAD_PAD_AVERAGE_UX,
    LOWER_UPRIGHT_WEB_STRESS_REGION,
)
from bracket_verification import regional_maximum_feature_evidence  # noqa: E402
from critical_stress_assessment import (  # noqa: E402
    ModelAssumptionLimitation,
    StressExtractionCandidate,
    build_critical_stress_assessment,
    critical_stress_assessment_to_dict,
    critical_stress_assessment_to_json,
)
from engineering_quantities import (  # noqa: E402
    QuantityEvaluation,
    build_raw_stress_mesh_trend,
)
from engineering_stress import (  # noqa: E402
    build_regional_stress_mesh_study,
    build_spatial_stress_band_studies,
    build_stress_spatial_diagnostic,
    evaluate_regional_stress,
    evaluate_spatial_stress_bands,
)
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NumericalResult,
    StressTensor,
    Vector3,
)


SIZES = (0.009, 0.006, 0.004)


def _analysis_result(size: float, peak_pa: float) -> AnalysisResult:
    mesh = MeshSummary(100, 50, "C3D10", size)
    tensor = StressTensor(peak_pa, 0.0, 0.0, 0.0, 0.0, 0.0)
    return AnalysisResult(
        bracket_analysis_definition("4.15.2", "2.23").model_version,
        mesh,
        DisplacementSummary(0.001, 1, Vector3(0.001, 0.0, 0.0)),
        build_equilibrium_evidence(
            Vector3(2500.0, 0.0, 0.0), Vector3(-2500.0, 0.0, 0.0)
        ),
        StressSummary(
            GlobalRawMaximumVonMises(
                peak_pa, 1, 1, tensor, Vector3(0.04, 0.025, 0.011)
            )
        ),
    )


def _quantity_evaluation(size: float) -> QuantityEvaluation:
    return QuantityEvaluation(
        LOAD_PAD_AVERAGE_UX,
        0.0005,
        "m",
        "controlled-bracket-v1",
        "a" * 64,
        "gmsh_physical_surface:load_pad",
        f"synthetic:{size}",
        MeshSummary(100, 50, "C3D10", size),
        "synthetic_test_evaluation",
        0.001,
    )


def _stress(value: float, identity: int, location: Vector3) -> IntegrationPointStress:
    return IntegrationPointStress(
        identity,
        1,
        StressTensor(value, 0.0, 0.0, 0.0, 0.0, 0.0),
        location,
    )


def _evidence_bundle(*, stable: bool):
    raw_values = (100.0, 102.0, 125.0) if not stable else (100.0, 102.0, 103.0)
    raw = build_raw_stress_mesh_trend(
        tuple(_analysis_result(size, value) for size, value in zip(SIZES, raw_values)),
        tuple(_quantity_evaluation(size) for size in SIZES),
    )
    if stable:
        region_locations = (
            Vector3(0.1340, 0.0500, 0.0450),
            Vector3(0.1345, 0.0500, 0.0450),
            Vector3(0.1347, 0.0500, 0.0450),
        )
        scales = (1.0, 1.03, 1.04)
    else:
        region_locations = (
            Vector3(0.1390, 0.0020, 0.0340),
            Vector3(0.1290, 0.0480, 0.0320),
            Vector3(0.1290, 0.0450, 0.0305),
        )
        scales = (1.0, 1.20, 1.50)

    regional = []
    band_levels = []
    for index, (size, scale, location) in enumerate(
        zip(SIZES, scales, region_locations), start=1
    ):
        mesh = MeshSummary(100, 50, "C3D10", size)
        regional_result = NumericalResult(
            (), (), (_stress(40.0 * scale, index, location),)
        )
        regional.append(
            evaluate_regional_stress(
                LOWER_UPRIGHT_WEB_STRESS_REGION,
                regional_result,
                mesh_identity=f"regional:{size}",
                mesh=mesh,
            )
        )
        band_result = NumericalResult(
            (),
            (),
            (
                _stress(100.0 * scale, 10, Vector3(0.04, 0.025, 0.011)),
                _stress(70.0 * scale, 11, Vector3(0.08, 0.05, 0.006)),
                _stress(60.0 * scale, 12, Vector3(0.12, 0.05, 0.015)),
                _stress(40.0 * scale, 13, location),
            ),
        )
        band_levels.append(
            evaluate_spatial_stress_bands(
                BRACKET_STRESS_SPATIAL_BANDS,
                band_result,
                mesh_identity=f"bands:{size}",
                mesh=mesh,
            )
        )
    regional_study = build_regional_stress_mesh_study(regional)
    band_studies = build_spatial_stress_band_studies(band_levels)
    diagnostic = build_stress_spatial_diagnostic(
        tuple(regional_maximum_feature_evidence(item) for item in regional),
        band_studies,
        BRACKET_STRESS_SPATIAL_POLICY,
    )
    return raw, regional_study, band_studies, diagnostic


def _assess(*, stable: bool = False, candidate=None, assumptions=None):
    raw, regional, bands, diagnostic = _evidence_bundle(stable=stable)
    return build_critical_stress_assessment(
        raw,
        regional,
        bands,
        diagnostic,
        BRACKET_CRITICAL_STRESS_POLICY,
        (
            BRACKET_CRITICAL_STRESS_MODEL_ASSUMPTIONS
            if assumptions is None
            else assumptions
        ),
        candidate=candidate,
    )


class CriticalStressAssessmentTests(unittest.TestCase):
    def test_controlled_bracket_evidence_is_conservatively_not_established(self) -> None:
        assessment = _assess()
        self.assertEqual(assessment.status, "not_established")
        self.assertIsNone(assessment.established_stress_reference)
        codes = {item.code for item in assessment.blocking_reasons}
        self.assertIn("global_raw_peak_is_diagnostic_only", codes)
        self.assertIn("global_raw_peak_mesh_sensitive", codes)
        self.assertIn("feature_specific_extraction_not_defined", codes)
        self.assertIn("regional_local_maximum_mesh_sensitive", codes)
        self.assertIn("regional_maximum_location_not_stable", codes)
        self.assertIn("model_assumption_limitations_unresolved", codes)

    def test_no_numeric_design_stress_or_design_critical_candidate_is_emitted(self) -> None:
        serialized = critical_stress_assessment_to_dict(_assess())
        self.assertIsNone(serialized["established_stress_reference"])
        self.assertTrue(serialized["candidate_investigation_regions"])
        self.assertTrue(
            all(
                item["interpretation"] == "investigation_only"
                for item in serialized["candidate_investigation_regions"]
            )
        )
        self.assertNotIn("design_critical", json.dumps(serialized).lower())

    def test_global_or_regional_value_alone_cannot_establish_stress(self) -> None:
        for source in ("global_raw_peak", "regional_maximum", "regional_aggregate"):
            with self.subTest(source=source):
                candidate = StressExtractionCandidate(
                    source,
                    "lower_upright_web_stress",
                    "1",
                    "synthetic_extraction",
                    50.0,
                    "mesh_sensitive",
                    "materially_moved_with_refinement",
                    "unresolved",
                )
                self.assertEqual(
                    _assess(candidate=candidate).status, "not_established"
                )

    def test_stable_mean_alone_is_insufficient_without_peak_treatment(self) -> None:
        candidate = StressExtractionCandidate(
            "regional_aggregate",
            "lower_upright_web_stress",
            "1",
            "arithmetic_mean",
            25.0,
            "comparatively_stable",
            "spatially_localized_within_policy",
            "not_justified",
        )
        assumptions = (
            ModelAssumptionLimitation(
                "mounting_constraint", "Synthetic characterized assumption", "adequately_characterized"
            ),
        )
        assessed = _assess(stable=True, candidate=candidate, assumptions=assumptions)
        self.assertEqual(assessed.status, "not_established")
        self.assertIn(
            "local_peak_treatment_not_justified",
            {item.code for item in assessed.blocking_reasons},
        )

    def test_blockers_preserve_assumption_and_structured_next_evidence(self) -> None:
        assessed = _assess()
        self.assertEqual(assessed.model_assumptions[0].status, "unresolved")
        self.assertEqual(assessed.model_assumptions[0].code, "fully_fixed_mounting_holes")
        requirements = {item.code for item in assessed.next_evidence_requirements}
        self.assertEqual(
            requirements,
            {
                "feature_specific_local_refinement",
                "feature_specific_stress_convergence_evidence",
                "explicit_stress_extraction_methodology",
                "localized_peak_tracking_evidence",
                "constraint_sensitive_stress_characterization",
            },
        )

    def test_fully_qualified_synthetic_path_can_be_established(self) -> None:
        candidate = StressExtractionCandidate(
            "regional_maximum",
            "lower_upright_web_stress",
            "1",
            "qualified_feature_maximum",
            41.6,
            "comparatively_stable",
            "spatially_localized_within_policy",
            "explicitly_retained_and_justified",
        )
        assumptions = (
            ModelAssumptionLimitation(
                "mounting_constraint", "Synthetic characterized assumption", "adequately_characterized"
            ),
        )
        assessed = _assess(stable=True, candidate=candidate, assumptions=assumptions)
        self.assertEqual(assessed.status, "established")
        self.assertEqual(assessed.established_stress_reference.value_pa, 41.6)
        self.assertEqual(assessed.blocking_reasons, ())
        self.assertEqual(assessed.next_evidence_requirements, ())

    def test_immutable_deterministic_serialization_and_guardrail_semantics(self) -> None:
        first_assessment = _assess()
        first = critical_stress_assessment_to_json(first_assessment)
        second = critical_stress_assessment_to_json(_assess())
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first_assessment.status = "established"
        for forbidden in (
            "factor_of_safety",
            '"fos"',
            "yield_strength",
            '"pass"',
            '"fail"',
            "singularity_confirmed",
            "safe",
            "unsafe",
            "allowable_stress",
            "physical_validation",
        ):
            self.assertNotIn(forbidden, first.lower())


if __name__ == "__main__":
    unittest.main()
