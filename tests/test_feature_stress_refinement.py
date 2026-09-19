import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import GlobalRawMaximumVonMises, MeshSummary  # noqa: E402
from bracket_definition import (  # noqa: E402
    BASELINE_MESH_SIZE_M,
    COARSE_MESH_SIZE_M,
    FINER_MESH_SIZE_M,
    bracket_analysis_definition,
)
from bracket_local_refinement import (  # noqa: E402
    BEHAVIOR_POLICY,
    FAR_FIELD_SIZE_M,
    LOCALIZATION_POLICY,
    LOCAL_LEVELS,
    ROOT_FEATURE_REGION,
    refinement_definition,
)
from feature_stress_refinement import (  # noqa: E402
    FeatureStressRefinementLevel,
    assess_critical_stress_impact,
    build_feature_stress_refinement_study,
    critical_stress_impact_to_dict,
    feature_stress_refinement_study_to_json,
    local_refinement_definition_to_dict,
)
from engineering_stress import RegionalStressEvidence  # noqa: E402
from generate_bracket_mesh import write_mesh_script  # noqa: E402
from numerical_results import StressTensor, Vector3  # noqa: E402


def _level(
    level_id: str,
    local_size: float,
    samples: int,
    minimum: float,
    mean: float,
    maximum: float,
    location: Vector3,
) -> FeatureStressRefinementLevel:
    mesh = MeshSummary(samples * 2, samples, "C3D10", FAR_FIELD_SIZE_M)
    evidence = RegionalStressEvidence(
        "1",
        ROOT_FEATURE_REGION,
        f"synthetic:{level_id}",
        mesh,
        samples,
        minimum,
        mean,
        maximum,
        location,
    )
    tensor = StressTensor(maximum * 2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return FeatureStressRefinementLevel(
        refinement_definition(level_id, local_size),
        evidence,
        GlobalRawMaximumVonMises(
            maximum * 2.0,
            10,
            1,
            tensor,
            Vector3(0.04, 0.025, 0.011),
        ),
    )


def _study():
    levels = (
        _level(
            "local_reference",
            0.006,
            100,
            1.0,
            20.0,
            100.0,
            Vector3(0.112, 0.028, 0.011),
        ),
        _level(
            "local_medium",
            0.004,
            200,
            0.8,
            19.5,
            120.0,
            Vector3(0.112, 0.044, 0.011),
        ),
        _level(
            "local_fine",
            0.004 / 1.5,
            400,
            0.6,
            19.6,
            145.0,
            Vector3(0.1125, 0.040, 0.0115),
        ),
    )
    return build_feature_stress_refinement_study(
        "controlled_bracket_root_feature_local_refinement",
        "1",
        levels,
        LOCALIZATION_POLICY,
        BEHAVIOR_POLICY,
    )


class FeatureStressRefinementTests(unittest.TestCase):
    def test_refinement_is_physical_and_contains_no_mesh_identity(self) -> None:
        serialized = local_refinement_definition_to_dict(
            refinement_definition("local_medium", 0.004)
        )
        self.assertEqual(
            serialized["selection_semantics"],
            "physical_global_coordinate_box_not_mesh_entities",
        )
        self.assertEqual(serialized["gmsh_field_type"], "Box")
        self.assertEqual(serialized["sizing_region_bounds_m"]["minimum"], [0.108, 0.0, 0.006])
        text = json.dumps(serialized)
        for forbidden in ("node_id", "element_id", "integration_point"):
            self.assertNotIn(forbidden, text)

    def test_far_field_is_fixed_and_local_sizes_have_ratio_1_5(self) -> None:
        definitions = tuple(refinement_definition(*item) for item in LOCAL_LEVELS)
        self.assertEqual({item.far_field_size_m for item in definitions}, {0.006})
        local = tuple(item.local_target_size_m for item in definitions)
        self.assertTrue(local[0] > local[1] > local[2])
        self.assertAlmostEqual(local[0] / local[1], 1.5)
        self.assertAlmostEqual(local[1] / local[2], 1.5)

    def test_only_local_prescription_varies_in_analysis_configuration(self) -> None:
        definitions = tuple(
            bracket_analysis_definition("4.15.2", "2.23", FAR_FIELD_SIZE_M)
            for _ in LOCAL_LEVELS
        )
        self.assertTrue(all(item == definitions[0] for item in definitions[1:]))
        self.assertEqual(definitions[0].material, definitions[2].material)
        self.assertEqual(definitions[0].loads, definitions[2].loads)
        self.assertEqual(definitions[0].boundary_conditions, definitions[2].boundary_conditions)

    def test_gmsh_script_uses_deterministic_box_field(self) -> None:
        script = Mock()
        write_mesh_script(
            script,
            Path("source.step"),
            Path("mesh.msh"),
            Path("mesh.inp"),
            0.006,
            0.004,
        )
        text = script.write_text.call_args.args[0]
        self.assertIn("Field[1] = Box;", text)
        self.assertIn("Field[1].VIn = 0.004;", text)
        self.assertIn("Field[1].VOut = 0.006;", text)
        self.assertIn("Background Field = 1;", text)

    def test_study_preserves_region_samples_high_values_and_changes(self) -> None:
        study = _study()
        self.assertTrue(all(item.evidence.region == ROOT_FEATURE_REGION for item in study.levels))
        self.assertEqual(tuple(item.evidence.sample_count for item in study.levels), (100, 200, 400))
        self.assertEqual(study.levels[-1].evidence.maximum_von_mises_pa, 145.0)
        self.assertAlmostEqual(study.adjacent_changes[0].signed_mean_change_pa, -0.5)
        self.assertAlmostEqual(study.adjacent_changes[1].signed_maximum_change_pa, 25.0)
        self.assertGreater(study.adjacent_changes[0].maximum_location_movement_m, 0.006)
        self.assertEqual(study.mean_behavior, "comparatively_stable")
        self.assertEqual(study.maximum_behavior, "mesh_sensitive")
        self.assertEqual(study.localization_status, "not_localized_within_policy")

    def test_formal_uniform_grid_estimate_is_not_misapplied(self) -> None:
        eligibility = _study().formal_convergence
        self.assertEqual(eligibility.status, "ineligible")
        self.assertEqual(
            eligibility.reason_code,
            "existing_uniform_grid_contract_not_applicable_to_fixed_far_field_local_sizing",
        )

    def test_critical_assessment_is_not_weakened(self) -> None:
        blockers = (
            "feature_specific_extraction_not_defined",
            "regional_local_maximum_mesh_sensitive",
            "model_assumption_limitations_unresolved",
        )
        impact = assess_critical_stress_impact(_study(), blockers)
        serialized = critical_stress_impact_to_dict(impact)
        self.assertEqual(serialized["resulting_status"], "not_established")
        self.assertEqual(serialized["resolved_blocker_codes"], [])
        self.assertEqual(tuple(serialized["still_present_blocker_codes"]), blockers)
        self.assertIsNone(serialized["established_stress_reference"])

    def test_serialization_is_deterministic_and_global_sequence_is_unchanged(self) -> None:
        self.assertEqual(
            feature_stress_refinement_study_to_json(_study()),
            feature_stress_refinement_study_to_json(_study()),
        )
        self.assertEqual(
            (COARSE_MESH_SIZE_M, BASELINE_MESH_SIZE_M, FINER_MESH_SIZE_M),
            (0.009, 0.006, 0.004),
        )
        text = feature_stress_refinement_study_to_json(_study()).lower()
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
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
