import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cantilever_verification import (  # noqa: E402
    cantilever_tip_displacement,
    compare_displacements,
    quadratic_triangle_weights,
    rectangular_second_moment,
)


class CantileverAnalyticalTests(unittest.TestCase):
    def test_rectangular_second_moment(self) -> None:
        self.assertTrue(
            math.isclose(
                rectangular_second_moment(0.05, 0.05),
                5.208333333333335e-7,
                rel_tol=1.0e-15,
            )
        )

    def test_euler_bernoulli_tip_displacement(self) -> None:
        second_moment = rectangular_second_moment(0.05, 0.05)
        displacement = cantilever_tip_displacement(1000.0, 1.0, 200.0e9, second_moment)
        self.assertTrue(math.isclose(displacement, 0.0032, rel_tol=1.0e-15))

    def test_error_uses_magnitude_and_preserves_correct_sign(self) -> None:
        result = compare_displacements(-0.0031, 0.0032)
        self.assertTrue(result["fea_sign_is_negative_z"])
        self.assertTrue(math.isclose(result["absolute_error_m"], 0.0001, rel_tol=1.0e-14))
        self.assertTrue(math.isclose(result["percent_error"], 3.125, rel_tol=1.0e-14))

    def test_wrong_sign_is_reported_even_when_magnitudes_match(self) -> None:
        result = compare_displacements(0.0032, 0.0032)
        self.assertFalse(result["fea_sign_is_negative_z"])
        self.assertEqual(result["absolute_error_m"], 0.0)

    def test_quadratic_triangle_weights_reproduce_centroid_partition(self) -> None:
        weights = quadratic_triangle_weights((1.0 / 3.0,) * 3)
        self.assertTrue(math.isclose(sum(weights), 1.0, rel_tol=1.0e-15))
        self.assertEqual(len(weights), 6)


if __name__ == "__main__":
    unittest.main()
