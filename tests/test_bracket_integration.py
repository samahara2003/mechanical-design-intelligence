import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_provenance import (  # noqa: E402
    ArtifactProvenance,
    build_analysis_provenance,
)
from analysis_results import ResolvedAnalysisContext, build_analysis_result  # noqa: E402
from bracket_definition import (  # noqa: E402
    BRACKET_FORCE,
    BRACKET_LOAD_FACE,
    BRACKET_MOUNTING_FACES,
    bracket_analysis_definition,
)
from bracket_verification import (  # noqa: E402
    area_weighted_surface_displacement,
    classify_stress_location,
)
from engineering_domain import GeometryEntity, TranslationalDof  # noqa: E402
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NodalDisplacement,
    NodalReaction,
    NumericalResult,
    StressTensor,
    Vector3,
)
from surface_load_mapping import map_uniform_force_to_c3d10_faces  # noqa: E402


class BracketIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = bracket_analysis_definition("4.15.2", "2.23", 0.006)

    def test_definition_preserves_geometry_level_intent(self) -> None:
        self.assertEqual(self.definition.loads, (BRACKET_FORCE,))
        self.assertEqual(BRACKET_FORCE.vector_n, (2500.0, 0.0, 0.0))
        self.assertEqual(BRACKET_FORCE.target, BRACKET_LOAD_FACE)
        self.assertIs(BRACKET_LOAD_FACE.entity, GeometryEntity.FACE)
        boundary = self.definition.boundary_conditions[0]
        self.assertEqual(boundary.target, BRACKET_MOUNTING_FACES)
        self.assertEqual(
            boundary.constrained_dofs,
            (TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
        )
        self.assertFalse(hasattr(BRACKET_MOUNTING_FACES, "node_ids"))

    def test_consistent_surface_mapping_recovers_requested_resultant(self) -> None:
        nodes = {
            1: (0.155, 0.03, 0.07), 2: (0.155, 0.07, 0.07),
            3: (0.155, 0.07, 0.11), 4: (0.155, 0.05, 0.07),
            5: (0.155, 0.07, 0.09), 6: (0.155, 0.05, 0.09),
            7: (0.155, 0.03, 0.07), 8: (0.155, 0.07, 0.11),
            9: (0.155, 0.03, 0.11), 10: (0.155, 0.05, 0.09),
            11: (0.155, 0.05, 0.11), 12: (0.155, 0.03, 0.09),
        }
        faces = [
            {"id": 1, "nodes": [1, 2, 3, 4, 5, 6]},
            {"id": 2, "nodes": [7, 8, 9, 10, 11, 12]},
        ]
        nodal, area, traction = map_uniform_force_to_c3d10_faces(
            BRACKET_FORCE, faces, nodes, 0.0016
        )
        resultant = tuple(sum(value[axis] for value in nodal.values()) for axis in range(3))
        self.assertAlmostEqual(area, 0.0016)
        self.assertEqual(traction, (1_562_500.0, 0.0, 0.0))
        for actual, expected in zip(resultant, BRACKET_FORCE.vector_n):
            self.assertAlmostEqual(actual, expected)

    def test_surface_qoi_recovers_constant_displacement(self) -> None:
        nodes = {
            1: (0.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0), 3: (0.0, 0.0, 1.0),
            4: (0.0, 0.5, 0.0), 5: (0.0, 0.5, 0.5), 6: (0.0, 0.0, 0.5),
        }
        displacement = {node: (2.0, -3.0, 4.0) for node in nodes}
        value, area = area_weighted_surface_displacement(
            [{"id": 1, "nodes": list(nodes)}], nodes, displacement
        )
        self.assertEqual(value, (2.0, -3.0, 4.0))
        self.assertEqual(area, 0.5)

    def test_analysis_result_and_feature_classification_are_reusable(self) -> None:
        numerical = NumericalResult(
            displacements=(NodalDisplacement(1, Vector3(0.001, 0.0, 0.0)),),
            reactions=(NodalReaction(2, Vector3(-2500.0, 0.0, 0.0)),),
            integration_point_stresses=(
                IntegrationPointStress(3, 1, StressTensor(10.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            ),
            reaction_resultant_n=Vector3(-2500.0, 0.0, 0.0),
        )
        result = build_analysis_result(
            self.definition,
            numerical,
            ResolvedAnalysisContext(10, 4, Vector3(2500.0, 0.0, 0.0)),
        )
        self.assertEqual(result.equilibrium.residual_magnitude_n, 0.0)
        self.assertEqual(classify_stress_location((0.041, 0.025, 0.011)), "mounting-hole vicinity")

    def test_real_bracket_definition_builds_existing_provenance_without_core_change(self) -> None:
        def captured(kind, path):
            return ArtifactProvenance(kind, "a" * 64, 10, str(path))

        paths = [Path(name) for name in ("part.step", "mesh.msh", "job.inp", "job.dat", "job.frd")]
        with patch("analysis_provenance.artifact_provenance_from_file", side_effect=captured):
            provenance = build_analysis_provenance(
                self.definition,
                cad_step_path=paths[0], mesh_path=paths[1], solver_input_path=paths[2],
                solver_dat_path=paths[3], solver_frd_path=paths[4],
                gmsh_version="4.15.2", calculix_version="2.23",
            )
        self.assertEqual(provenance.model_version, self.definition.model_version)
        self.assertEqual(len(provenance.analysis_definition_sha256), 64)
        self.assertIsNotNone(provenance.solver_dat)


if __name__ == "__main__":
    unittest.main()
