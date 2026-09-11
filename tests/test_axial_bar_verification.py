import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from axial_bar_verification import (  # noqa: E402
    AREA_M2,
    HEIGHT_M,
    LENGTH_M,
    POISSONS_RATIO,
    WIDTH_M,
    YOUNGS_MODULUS_PA,
    axial_references,
    interpolate_face_displacement,
    isotropic_strain_from_stress,
    weighted_component_summary,
)
from run_axial_bar_solve import consistent_quadratic_face_loads  # noqa: E402


class AxialBarLogicTests(unittest.TestCase):
    def test_analytical_references(self) -> None:
        result = axial_references(
            1000.0, AREA_M2, LENGTH_M, YOUNGS_MODULUS_PA, POISSONS_RATIO
        )
        self.assertTrue(math.isclose(result["nominal_sigma_xx_pa"], 400000.0))
        self.assertTrue(math.isclose(result["epsilon_xx"], 2.0e-6))
        self.assertTrue(math.isclose(result["epsilon_yy"], -6.0e-7))
        self.assertTrue(math.isclose(result["epsilon_zz"], -6.0e-7))
        self.assertTrue(math.isclose(result["elongation_m"], 2.0e-6))

    def test_uniaxial_stress_reconstructs_poisson_strains(self) -> None:
        strain = isotropic_strain_from_stress(
            [400000.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            YOUNGS_MODULUS_PA,
            POISSONS_RATIO,
        )
        expected = (2.0e-6, -6.0e-7, -6.0e-7)
        self.assertTrue(all(math.isclose(strain[i], expected[i]) for i in range(3)))

    def test_consistent_quadratic_face_load_integrates_resultant(self) -> None:
        nodes = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, WIDTH_M, 0.0),
            3: (1.0, 0.0, HEIGHT_M),
            4: (1.0, WIDTH_M / 2.0, 0.0),
            5: (1.0, WIDTH_M / 2.0, HEIGHT_M / 2.0),
            6: (1.0, 0.0, HEIGHT_M / 2.0),
        }
        loads, area = consistent_quadratic_face_loads(
            [{"id": 1, "nodes": list(nodes)}], nodes, (400000.0, 0.0, 0.0)
        )
        self.assertTrue(math.isclose(area, AREA_M2 / 2.0))
        resultant = tuple(sum(vector[axis] for vector in loads.values()) for axis in range(3))
        self.assertTrue(math.isclose(resultant[0], 500.0))
        self.assertEqual(resultant[1:], (0.0, 0.0))
        self.assertEqual(set(loads), {4, 5, 6})

    def test_free_end_centroid_quadratic_interpolation_preserves_vector_field(self) -> None:
        coordinates = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, WIDTH_M, 0.0),
            3: (1.0, WIDTH_M, HEIGHT_M),
            4: (1.0, 0.0, HEIGHT_M),
            5: (1.0, WIDTH_M / 2.0, 0.0),
            6: (1.0, WIDTH_M, HEIGHT_M / 2.0),
            7: (1.0, WIDTH_M / 2.0, HEIGHT_M / 2.0),
            8: (1.0, WIDTH_M / 2.0, HEIGHT_M),
            9: (1.0, 0.0, HEIGHT_M / 2.0),
        }
        faces = [
            {"id": 1, "nodes": [1, 2, 3, 5, 6, 7]},
            {"id": 2, "nodes": [1, 3, 4, 7, 8, 9]},
        ]
        displacements = {
            node: (2.0e-6 + y * 1.0e-7, -6.0e-7 * y, -6.0e-7 * z)
            for node, (_, y, z) in coordinates.items()
        }
        value, selection = interpolate_face_displacement(coordinates, faces, displacements)
        expected = (
            2.0e-6 + WIDTH_M / 2.0 * 1.0e-7,
            -6.0e-7 * WIDTH_M / 2.0,
            -6.0e-7 * HEIGHT_M / 2.0,
        )
        self.assertTrue(all(math.isclose(value[i], expected[i], abs_tol=1.0e-20) for i in range(3)))
        self.assertEqual(selection["candidate_count"], 2)

    def test_weighted_stress_summary_uses_physical_weights(self) -> None:
        samples = [
            {"weight_m3": 1.0, "stress_pa": [100.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
            {"weight_m3": 3.0, "stress_pa": [300.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
        ]
        summary = weighted_component_summary(
            samples,
            "stress_pa",
            ("xx", "yy", "zz", "xy", "xz", "yz"),
        )
        self.assertEqual(summary["xx"]["mean"], 250.0)
        self.assertEqual(summary["xx"]["minimum"], 100.0)
        self.assertEqual(summary["xx"]["maximum"], 300.0)


if __name__ == "__main__":
    unittest.main()
