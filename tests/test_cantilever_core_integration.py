import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_results import (  # noqa: E402
    ResolvedAnalysisContext,
    analysis_result_to_dict,
    build_analysis_result,
)
from calculix_adapter import (  # noqa: E402
    render_c3d10_linear_static_deck,
    translate_boundary_condition,
    translate_nodal_force_representation,
)
from cantilever_definition import (  # noqa: E402
    CANTILEVER_FIXED_FACE,
    CANTILEVER_FORCE,
    CANTILEVER_LOAD_FACE,
    cantilever_analysis_definition,
)
from engineering_domain import GeometryEntity  # noqa: E402
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NodalDisplacement,
    NodalReaction,
    NumericalResult,
    StressTensor,
    Vector3,
)
from surface_load_mapping import map_uniform_force_to_c3d10_faces  # noqa: E402


class CantileverCoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = cantilever_analysis_definition("4.15.2", "2.23", 0.0125)

    def test_definition_preserves_geometry_level_negative_z_force_intent(self) -> None:
        force = self.definition.loads[0]
        self.assertEqual(force.direction, (0.0, 0.0, -1.0))
        self.assertEqual(force.vector_n, (0.0, 0.0, -1000.0))
        self.assertEqual(force.target, CANTILEVER_LOAD_FACE)
        self.assertIs(force.target.entity, GeometryEntity.FACE)
        self.assertFalse(hasattr(force.target, "node_ids"))
        self.assertEqual(self.definition.boundary_conditions[0].target, CANTILEVER_FIXED_FACE)

    def test_shared_surface_mapping_and_adapter_preserve_transverse_resultant(self) -> None:
        nodes = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, 0.05, 0.0),
            3: (1.0, 0.05, 0.05),
            4: (1.0, 0.025, 0.0),
            5: (1.0, 0.05, 0.025),
            6: (1.0, 0.025, 0.025),
            7: (1.0, 0.0, 0.0),
            8: (1.0, 0.05, 0.05),
            9: (1.0, 0.0, 0.05),
            10: (1.0, 0.025, 0.025),
            11: (1.0, 0.025, 0.05),
            12: (1.0, 0.0, 0.025),
        }
        faces = [
            {"id": 20, "nodes": [1, 2, 3, 4, 5, 6]},
            {"id": 21, "nodes": [7, 8, 9, 10, 11, 12]},
        ]
        original_force = self.definition.loads[0]
        nodal, area, traction = map_uniform_force_to_c3d10_faces(
            original_force, faces, nodes, 0.0025
        )
        resultant = tuple(sum(vector[axis] for vector in nodal.values()) for axis in range(3))
        self.assertAlmostEqual(area, 0.0025)
        self.assertEqual(traction, (0.0, 0.0, -400000.0))
        for actual, expected in zip(resultant, (0.0, 0.0, -1000.0)):
            self.assertAlmostEqual(actual, expected)

        boundary = translate_boundary_condition(
            self.definition.boundary_conditions[0], CANTILEVER_FIXED_FACE, [1, 2, 3], "FIXED"
        )
        loads = translate_nodal_force_representation(
            original_force, CANTILEVER_LOAD_FACE, nodal
        )
        deck = render_c3d10_linear_static_deck(
            self.definition,
            nodes,
            [{"id": 1, "nodes": list(range(1, 11))}],
            boundary,
            list(nodal),
            [(1, "S1")],
            loads,
            heading="cantilever test",
            volume_set_name="BEAM",
            load_node_set_name="LOAD_NODES",
            load_face_set_prefix="LOAD_",
            load_surface_name="LOAD_FACE",
        )
        self.assertIn("*EL PRINT, ELSET=BEAM", deck)
        self.assertTrue(any(row.dof == 3 and row.value_n < 0.0 for row in loads))
        self.assertIs(self.definition.loads[0], original_force)

    def test_reusable_result_keeps_benchmark_qois_outside_global_summaries(self) -> None:
        numerical = NumericalResult(
            displacements=(
                NodalDisplacement(2, Vector3(0.0, 0.0, -0.0032)),
                NodalDisplacement(1, Vector3(0.0, 0.0, -0.001)),
            ),
            reactions=(NodalReaction(3, Vector3(0.0, 0.0, 1000.0)),),
            integration_point_stresses=(
                IntegrationPointStress(
                    8,
                    2,
                    StressTensor(-50.0e6, 0.0, 0.0, 0.0, 0.0, 0.0),
                ),
            ),
            reaction_resultant_n=Vector3(0.0, 0.0, 1000.0),
        )
        result = build_analysis_result(
            self.definition,
            numerical,
            ResolvedAnalysisContext(2, 1, Vector3(0.0, 0.0, -1000.0)),
        )
        serialized = analysis_result_to_dict(result)
        self.assertEqual(result.displacement.node_id, 2)
        self.assertEqual(result.equilibrium.imbalance_n, Vector3(0.0, 0.0, 0.0))
        peak = result.stress.global_raw_max_von_mises
        self.assertEqual((peak.element_id, peak.integration_point), (8, 2))
        self.assertEqual(peak.stress_pa.sigma_xx_pa, -50.0e6)
        self.assertNotIn("analytical", serialized)
        self.assertNotIn("tip_centroid", serialized)
        self.assertNotIn("section_x", serialized)


if __name__ == "__main__":
    unittest.main()
