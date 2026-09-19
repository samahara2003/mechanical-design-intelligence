import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import MeshSummary  # noqa: E402
from bracket_quantities import (  # noqa: E402
    BRACKET_STRESS_SPATIAL_BANDS,
    BRACKET_STRESS_SPATIAL_POLICY,
    LOWER_UPRIGHT_WEB_STRESS_REGION,
)
from bracket_verification import regional_maximum_feature_evidence  # noqa: E402
from engineering_stress import (  # noqa: E402
    RegionalStressEvidenceError,
    build_regional_stress_mesh_study,
    build_spatial_stress_band_studies,
    build_stress_spatial_diagnostic,
    evaluate_regional_stress,
    evaluate_spatial_stress_bands,
    physical_coordinate_box_region_to_dict,
    regional_stress_evidence_to_dict,
    regional_stress_mesh_study_to_json,
    spatial_stress_band_study_to_dict,
    spatial_stress_band_to_dict,
    stress_spatial_diagnostic_to_json,
    validate_spatial_stress_bands,
)
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NumericalResult,
    StressTensor,
    Vector3,
)


def stress(value_pa: float, identity: int, location: Vector3 | None) -> IntegrationPointStress:
    return IntegrationPointStress(
        identity,
        1,
        StressTensor(value_pa, 0.0, 0.0, 0.0, 0.0, 0.0),
        location,
    )


def numerical(values_pa: tuple[float, ...]) -> NumericalResult:
    return NumericalResult(
        (),
        (),
        tuple(
            stress(value, index, Vector3(0.134, 0.05, 0.045))
            for index, value in enumerate(values_pa, start=1)
        ),
    )


def evidence(mesh_size_m: float, values_pa: tuple[float, ...]):
    return evaluate_regional_stress(
        LOWER_UPRIGHT_WEB_STRESS_REGION,
        numerical(values_pa),
        mesh_identity=f"synthetic:{mesh_size_m}",
        mesh=MeshSummary(len(values_pa) * 10, len(values_pa) * 5, "C3D10", mesh_size_m),
    )


class EngineeringStressTests(unittest.TestCase):
    def test_region_is_physical_intent_without_mesh_identities(self) -> None:
        serialized = physical_coordinate_box_region_to_dict(
            LOWER_UPRIGHT_WEB_STRESS_REGION
        )
        self.assertEqual(serialized["region_id"], "lower_upright_web_stress")
        self.assertEqual(serialized["closed_bounds_m"]["minimum"], [0.128, 0.0, 0.03])
        self.assertEqual(serialized["closed_bounds_m"]["maximum"], [0.14, 0.1, 0.06])
        self.assertEqual(
            serialized["constraint_context"]["relationship"],
            "separated_from_immediate_constrained_surface",
        )
        text = json.dumps(serialized, sort_keys=True)
        for forbidden in ("node_id", "element_id", "integration_point_id"):
            self.assertNotIn(forbidden, text)

    def test_selection_von_mises_statistics_and_high_sample_preservation(self) -> None:
        result = NumericalResult(
            (),
            (),
            (
                stress(10.0, 1, Vector3(0.134, 0.05, 0.045)),
                stress(20.0, 2, Vector3(0.128, 0.0, 0.03)),
                stress(1000.0, 3, Vector3(0.140, 0.1, 0.06)),
                stress(5000.0, 4, Vector3(0.05, 0.05, 0.006)),
            ),
        )
        evaluated = evaluate_regional_stress(
            LOWER_UPRIGHT_WEB_STRESS_REGION,
            result,
            mesh_identity="synthetic:mesh",
            mesh=MeshSummary(20, 10, "C3D10", 0.006),
        )
        self.assertEqual(evaluated.sample_count, 3)
        self.assertEqual(evaluated.minimum_von_mises_pa, 10.0)
        self.assertEqual(evaluated.arithmetic_mean_von_mises_pa, 1030.0 / 3.0)
        self.assertEqual(evaluated.maximum_von_mises_pa, 1000.0)
        serialized = regional_stress_evidence_to_dict(evaluated)
        self.assertEqual(
            serialized["stress_representation"],
            "raw_integration_point_cauchy_stress_derived_von_mises",
        )

    def test_three_meshes_preserve_region_with_different_sample_counts(self) -> None:
        evaluations = (
            evidence(0.009, (10.0, 20.0)),
            evidence(0.006, (10.0, 20.0, 30.0)),
            evidence(0.004, (10.0, 20.0, 30.0, 40.0)),
        )
        study = build_regional_stress_mesh_study(evaluations)
        self.assertTrue(all(item.region == study.region for item in study.evaluations))
        self.assertEqual(tuple(item.sample_count for item in study.evaluations), (2, 3, 4))
        self.assertEqual(len(study.adjacent_changes), 2)
        self.assertEqual(study.adjacent_changes[0].signed_mean_change_pa, 5.0)
        self.assertEqual(study.adjacent_changes[1].signed_maximum_change_pa, 10.0)
        first = regional_stress_mesh_study_to_json(study)
        second = regional_stress_mesh_study_to_json(
            build_regional_stress_mesh_study(evaluations)
        )
        self.assertEqual(first, second)
        for forbidden in (
            "factor_of_safety",
            '"fos"',
            "yield",
            "failure",
            "singularity",
            "design-critical",
            '"pass"',
            '"fail"',
        ):
            self.assertNotIn(forbidden, first.lower())

    def test_empty_or_unlocated_region_is_explicit_error(self) -> None:
        unlocated = NumericalResult(
            (),
            (),
            (stress(10.0, 1, Vector3(0.05, 0.05, 0.006)), stress(20.0, 2, None)),
        )
        with self.assertRaisesRegex(RegionalStressEvidenceError, "location for every"):
            evaluate_regional_stress(
                LOWER_UPRIGHT_WEB_STRESS_REGION,
                unlocated,
                mesh_identity="synthetic:empty",
                mesh=MeshSummary(20, 10, "C3D10", 0.006),
            )
        outside = NumericalResult(
            (), (), (stress(10.0, 1, Vector3(0.05, 0.05, 0.006)),)
        )
        with self.assertRaisesRegex(RegionalStressEvidenceError, "contains no"):
            evaluate_regional_stress(
                LOWER_UPRIGHT_WEB_STRESS_REGION,
                outside,
                mesh_identity="synthetic:outside",
                mesh=MeshSummary(20, 10, "C3D10", 0.006),
            )

    def test_maximum_location_and_tie_handling_are_deterministic(self) -> None:
        tied = NumericalResult(
            (),
            (),
            (
                stress(100.0, 1, Vector3(0.139, 0.08, 0.05)),
                stress(100.0, 2, Vector3(0.129, 0.09, 0.05)),
                stress(20.0, 3, Vector3(0.130, 0.04, 0.04)),
            ),
        )
        evaluated = evaluate_regional_stress(
            LOWER_UPRIGHT_WEB_STRESS_REGION,
            tied,
            mesh_identity="synthetic:tied",
            mesh=MeshSummary(30, 15, "C3D10", 0.006),
        )
        self.assertEqual(evaluated.maximum_von_mises_pa, 100.0)
        self.assertEqual(evaluated.maximum_location_m, Vector3(0.129, 0.09, 0.05))
        serialized = regional_stress_evidence_to_dict(evaluated)
        self.assertEqual(serialized["maximum_location_m"], [0.129, 0.09, 0.05])

    def test_spatial_bands_are_explicit_nonoverlapping_physical_intent(self) -> None:
        bands = validate_spatial_stress_bands(BRACKET_STRESS_SPATIAL_BANDS)
        self.assertEqual(len(bands), 4)
        self.assertEqual(
            tuple((item.lower_x_m, item.upper_x_m) for item in bands),
            ((0.0, 0.07), (0.07, 0.113), (0.113, 0.128), (0.128, 0.155)),
        )
        for previous, current in zip(bands, bands[1:]):
            self.assertEqual(previous.upper_x_m, current.lower_x_m)
            self.assertFalse(previous.upper_bound_inclusive)
        serialized = json.dumps(
            [spatial_stress_band_to_dict(item) for item in bands], sort_keys=True
        )
        self.assertIn("global_x_coordinate_proxy_not_shortest_feature_distance", serialized)
        for forbidden in ("node_id", "element_id", "integration_point_id"):
            self.assertNotIn(forbidden, serialized)

    def test_band_statistics_locations_and_high_samples_are_preserved(self) -> None:
        result = NumericalResult(
            (),
            (),
            (
                stress(1000.0, 1, Vector3(0.04, 0.025, 0.011)),
                stress(10.0, 2, Vector3(0.02, 0.05, 0.006)),
                stress(20.0, 3, Vector3(0.08, 0.05, 0.006)),
                stress(30.0, 4, Vector3(0.12, 0.05, 0.015)),
                stress(40.0, 5, Vector3(0.14, 0.05, 0.05)),
            ),
        )
        evaluated = evaluate_spatial_stress_bands(
            BRACKET_STRESS_SPATIAL_BANDS,
            result,
            mesh_identity="synthetic:bands",
            mesh=MeshSummary(50, 25, "C3D10", 0.006),
        )
        support = evaluated[0]
        self.assertEqual(support.sample_count, 2)
        self.assertEqual(support.minimum_von_mises_pa, 10.0)
        self.assertEqual(support.arithmetic_mean_von_mises_pa, 505.0)
        self.assertEqual(support.maximum_von_mises_pa, 1000.0)
        self.assertEqual(support.maximum_location_m, Vector3(0.04, 0.025, 0.011))
        self.assertEqual(tuple(item.sample_count for item in evaluated), (2, 1, 1, 1))

    def test_same_bands_across_meshes_and_spatial_diagnostic_serialization(self) -> None:
        def level(size: float, scale: float):
            samples = NumericalResult(
                (),
                (),
                (
                    stress(100.0 * scale, 1, Vector3(0.04, 0.025, 0.011)),
                    stress(50.0 * scale, 2, Vector3(0.08, 0.05, 0.006)),
                    stress(40.0 * scale, 3, Vector3(0.12, 0.05, 0.015)),
                    stress(30.0 * scale, 4, Vector3(0.14, 0.05, 0.04)),
                ),
            )
            mesh = MeshSummary(100, 50, "C3D10", size)
            return evaluate_spatial_stress_bands(
                BRACKET_STRESS_SPATIAL_BANDS,
                samples,
                mesh_identity=f"synthetic:{size}",
                mesh=mesh,
            )

        band_levels = (level(0.009, 1.0), level(0.006, 1.05), level(0.004, 1.20))
        studies = build_spatial_stress_band_studies(band_levels)
        self.assertTrue(
            all(
                study.band == BRACKET_STRESS_SPATIAL_BANDS[index]
                for index, study in enumerate(studies)
            )
        )

        region_locations = (
            Vector3(0.139, 0.002, 0.034),
            Vector3(0.129, 0.048, 0.032),
            Vector3(0.129, 0.045, 0.030),
        )
        regional = tuple(
            evaluate_regional_stress(
                LOWER_UPRIGHT_WEB_STRESS_REGION,
                NumericalResult((), (), (stress(50.0 + index, 1, location),)),
                mesh_identity=f"regional:{index}",
                mesh=MeshSummary(100, 50, "C3D10", size),
            )
            for index, (size, location) in enumerate(
                zip((0.009, 0.006, 0.004), region_locations)
            )
        )
        diagnostic = build_stress_spatial_diagnostic(
            tuple(regional_maximum_feature_evidence(item) for item in regional),
            studies,
            BRACKET_STRESS_SPATIAL_POLICY,
        )
        self.assertEqual(
            diagnostic.regional_location_behavior, "materially_moved_with_refinement"
        )
        self.assertGreater(diagnostic.adjacent_maximum_location_distances_m[0], 0.02)
        self.assertLess(diagnostic.adjacent_maximum_location_distances_m[1], 0.01)
        contexts = diagnostic.regional_maxima[0].feature_relationships
        self.assertEqual(contexts[0].feature_id, "simplified_fixed_mounting_bores")
        self.assertEqual(
            contexts[0].relationship, "separated_beyond_controlled_near_distance"
        )
        first = stress_spatial_diagnostic_to_json(diagnostic)
        second = stress_spatial_diagnostic_to_json(
            build_stress_spatial_diagnostic(
                tuple(regional_maximum_feature_evidence(item) for item in regional),
                studies,
                BRACKET_STRESS_SPATIAL_POLICY,
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(
            len(
                spatial_stress_band_study_to_dict(studies[0])[
                    "ordered_mesh_evaluations"
                ]
            ),
            3,
        )
        for forbidden in (
            "singularity_confirmed",
            "design_critical",
            "factor_of_safety",
            '"fos"',
            '"pass"',
            '"fail"',
            "physical_validation",
            "caused_by",
        ):
            self.assertNotIn(forbidden, first.lower())

    def test_empty_spatial_band_is_explicit_error(self) -> None:
        result = NumericalResult(
            (), (), (stress(10.0, 1, Vector3(0.04, 0.025, 0.011)),)
        )
        with self.assertRaisesRegex(RegionalStressEvidenceError, "contains no samples"):
            evaluate_spatial_stress_bands(
                BRACKET_STRESS_SPATIAL_BANDS,
                result,
                mesh_identity="synthetic:empty-band",
                mesh=MeshSummary(20, 10, "C3D10", 0.006),
            )


if __name__ == "__main__":
    unittest.main()
