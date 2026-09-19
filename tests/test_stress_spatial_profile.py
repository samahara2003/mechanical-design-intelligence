import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bracket_local_refinement import (  # noqa: E402
    ROOT_BASE_TANGENCY_PROFILE,
    ROOT_PROFILE_POLICY,
    root_maximum_geometry_relationship,
)
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NumericalResult,
    StressTensor,
    Vector3,
)
from stress_spatial_profile import (  # noqa: E402
    assess_stress_profile_impact,
    build_stress_spatial_profile_study,
    evaluate_stress_spatial_profile,
    stress_profile_assessment_impact_to_dict,
    stress_spatial_profile_study_to_json,
)


def _stress(identity: int, value: float, location: Vector3) -> IntegrationPointStress:
    return IntegrationPointStress(
        identity,
        1,
        StressTensor(value, 0.0, 0.0, 0.0, 0.0, 0.0),
        location,
    )


def _profile(scale: float, mesh: str, peak_x: float = 0.112) -> object:
    numerical = NumericalResult(
        (),
        (),
        (
            _stress(1, 100.0 * scale, Vector3(peak_x, 0.05, 0.0115)),
            _stress(2, 40.0 * scale, Vector3(0.111, 0.05, 0.0115)),
            _stress(3, 55.0 * scale, Vector3(0.109, 0.05, 0.0115)),
            _stress(4, 60.0 * scale, Vector3(0.106, 0.05, 0.0115)),
            _stress(5, 50.0 * scale, Vector3(0.103, 0.05, 0.0115)),
            _stress(6, 1000.0 * scale, Vector3(0.05, 0.05, 0.006)),
        ),
    )
    return evaluate_stress_spatial_profile(
        ROOT_BASE_TANGENCY_PROFILE,
        numerical,
        mesh_identity=mesh,
        feature_peak_location_m=Vector3(peak_x, 0.05, 0.0115),
    )


class StressSpatialProfileTests(unittest.TestCase):
    def test_path_is_physical_intent_without_mesh_ids_or_interpolation(self) -> None:
        path = ROOT_BASE_TANGENCY_PROFILE
        self.assertEqual(path.origin_m, Vector3(0.113, 0.0, 0.012))
        self.assertEqual(path.direction, Vector3(-1.0, 0.0, 0.0))
        self.assertEqual(path.reference_feature, "root_fillet_base_tangency_line")
        self.assertEqual(path.interpolation, "none")
        text = stress_spatial_profile_study_to_json(
            build_stress_spatial_profile_study(
                "synthetic_root_profile",
                "1",
                (_profile(1.0, "a"), _profile(1.02, "b"), _profile(1.03, "c")),
                ROOT_PROFILE_POLICY,
            )
        )
        for forbidden in ("node_id", "element_id", "integration_point_id"):
            self.assertNotIn(forbidden, text)

    def test_bin_membership_has_deterministic_half_open_boundaries(self) -> None:
        path = ROOT_BASE_TANGENCY_PROFILE
        self.assertEqual(path.bin_index(Vector3(0.113, 0.0, 0.012)), 0)
        self.assertEqual(path.bin_index(Vector3(0.110, 0.05, 0.012)), 1)
        self.assertEqual(path.bin_index(Vector3(0.101, 0.1, 0.012)), 3)
        self.assertIsNone(path.bin_index(Vector3(0.100999, 0.05, 0.012)))
        self.assertIsNone(path.bin_index(Vector3(0.112, 0.05, 0.014001)))

    def test_empty_bins_are_explicit_and_no_stress_is_fabricated(self) -> None:
        numerical = NumericalResult(
            (),
            (),
            (_stress(1, 100.0, Vector3(0.112, 0.05, 0.0115)),),
        )
        profile = evaluate_stress_spatial_profile(
            ROOT_BASE_TANGENCY_PROFILE,
            numerical,
            mesh_identity="single",
            feature_peak_location_m=Vector3(0.112, 0.05, 0.0115),
        )
        self.assertEqual(profile.bins[0].sample_count, 1)
        for item in profile.bins[1:]:
            self.assertEqual(item.sample_count, 0)
            self.assertIsNone(item.arithmetic_mean_von_mises_pa)
            self.assertIsNone(item.maximum_von_mises_pa)
            self.assertIsNone(item.maximum_location_m)
        self.assertTrue(all(item.mean_gradient_pa_per_m is None for item in profile.gradients))

    def test_all_high_samples_and_maximum_location_are_preserved(self) -> None:
        numerical = NumericalResult(
            (),
            (),
            (
                _stress(1, 10.0, Vector3(0.1125, 0.05, 0.0115)),
                _stress(2, 10000.0, Vector3(0.1120, 0.04, 0.0114)),
                _stress(3, 20.0, Vector3(0.1115, 0.03, 0.0113)),
            ),
        )
        profile = evaluate_stress_spatial_profile(
            ROOT_BASE_TANGENCY_PROFILE,
            numerical,
            mesh_identity="high",
            feature_peak_location_m=Vector3(0.1120, 0.04, 0.0114),
        )
        first = profile.bins[0]
        self.assertEqual(first.sample_count, 3)
        self.assertEqual(first.maximum_von_mises_pa, 10000.0)
        self.assertEqual(first.maximum_location_m, Vector3(0.1120, 0.04, 0.0114))
        self.assertEqual(first.arithmetic_mean_von_mises_pa, (10.0 + 10000.0 + 20.0) / 3.0)

    def test_peak_association_and_descriptive_gradient_are_deterministic(self) -> None:
        profile = _profile(1.0, "mesh")
        self.assertTrue(profile.feature_peak.associated)
        self.assertEqual(profile.feature_peak.bin_index, 0)
        self.assertAlmostEqual(profile.feature_peak.path_distance_m, 0.001)
        self.assertAlmostEqual(profile.feature_peak.transverse_distance_m, 0.0005)
        self.assertEqual(
            profile.gradients[0].semantics,
            "finite_difference_of_adjacent_bin_means_descriptive_only",
        )
        self.assertAlmostEqual(profile.gradients[0].mean_gradient_pa_per_m, -5000.0)

    def test_profile_study_reuses_definition_and_serializes_deterministically(self) -> None:
        profiles = (_profile(1.0, "a"), _profile(1.02, "b"), _profile(1.03, "c"))
        study = build_stress_spatial_profile_study(
            "synthetic_root_profile", "1", profiles, ROOT_PROFILE_POLICY
        )
        self.assertTrue(all(item.path == profiles[0].path for item in profiles))
        self.assertEqual(study.peak_association_status, "same_profile_portion")
        self.assertEqual(study.shape_status, "reproducible_within_policy")
        self.assertEqual(
            stress_spatial_profile_study_to_json(study),
            stress_spatial_profile_study_to_json(
                build_stress_spatial_profile_study(
                    "synthetic_root_profile", "1", profiles, ROOT_PROFILE_POLICY
                )
            ),
        )

    def test_actual_geometry_relationship_uses_quarter_circle_and_tangency(self) -> None:
        relationship = root_maximum_geometry_relationship(
            Vector3(0.112555933448028, 0.03998317026768992, 0.011645439259222356)
        )
        self.assertTrue(relationship["inside_base_material"])
        self.assertFalse(relationship["inside_fillet_quarter_projection"])
        self.assertTrue(relationship["base_side_of_fillet_tangency"])
        self.assertEqual(
            relationship["closest_permitted_fillet_boundary_feature"],
            "base_side_tangency_line",
        )

    def test_assessment_is_not_weakened_and_guardrails_remain(self) -> None:
        study = build_stress_spatial_profile_study(
            "synthetic_root_profile",
            "1",
            (_profile(1.0, "a"), _profile(1.02, "b"), _profile(1.03, "c")),
            ROOT_PROFILE_POLICY,
        )
        blockers = (
            "feature_specific_extraction_not_defined",
            "regional_local_maximum_mesh_sensitive",
        )
        impact = stress_profile_assessment_impact_to_dict(
            assess_stress_profile_impact(study, blockers)
        )
        self.assertEqual(impact["resulting_status"], "not_established")
        self.assertEqual(impact["resolved_blocker_codes"], [])
        self.assertEqual(tuple(impact["still_present_blocker_codes"]), blockers)
        self.assertIsNone(impact["established_stress_reference"])
        text = stress_spatial_profile_study_to_json(study).lower()
        for forbidden in (
            "design_critical",
            "factor_of_safety",
            '"fos"',
            "yield_strength",
            '"pass"',
            '"fail"',
            "singularity_confirmed",
            "safe",
            "unsafe",
            "physical_validation",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
