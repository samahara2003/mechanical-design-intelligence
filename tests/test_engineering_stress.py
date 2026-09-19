import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import MeshSummary  # noqa: E402
from bracket_quantities import LOWER_UPRIGHT_WEB_STRESS_REGION  # noqa: E402
from engineering_stress import (  # noqa: E402
    RegionalStressEvidenceError,
    build_regional_stress_mesh_study,
    evaluate_regional_stress,
    physical_coordinate_box_region_to_dict,
    regional_stress_evidence_to_dict,
    regional_stress_mesh_study_to_json,
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


if __name__ == "__main__":
    unittest.main()
