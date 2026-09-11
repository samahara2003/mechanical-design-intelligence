import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cantilever_stress_verification import (  # noqa: E402
    HEIGHT_M,
    GAUSS_NATURAL_COORDINATES,
    SECTION_X_M,
    WIDTH_M,
    c3d10_shape_weights,
    fit_section_field,
    interpolate_coordinates,
    simple_z_cross_check,
    tetrahedron_volume,
    von_mises,
)


class CantileverStressLogicTests(unittest.TestCase):
    def test_c3d10_shape_weights_reproduce_straight_tetrahedron_coordinates(self) -> None:
        nodes = {
            1: (0.0, 0.0, 0.0),
            2: (1.0, 0.0, 0.0),
            3: (0.0, 1.0, 0.0),
            4: (0.0, 0.0, 1.0),
            5: (0.5, 0.0, 0.0),
            6: (0.5, 0.5, 0.0),
            7: (0.0, 0.5, 0.0),
            8: (0.0, 0.0, 0.5),
            9: (0.5, 0.0, 0.5),
            10: (0.0, 0.5, 0.5),
        }
        natural = (0.2, 0.3, 0.1)
        self.assertTrue(math.isclose(sum(c3d10_shape_weights(natural)), 1.0))
        actual = interpolate_coordinates(list(nodes), nodes, natural)
        self.assertTrue(
            all(math.isclose(actual[index], natural[index], abs_tol=1.0e-15) for index in range(3))
        )

    def test_four_integration_point_records_map_to_independent_affine_coordinates(self) -> None:
        corners = ((0.1, 0.2, 0.3), (1.1, 0.4, 0.2), (0.2, 1.4, 0.5), (0.3, 0.1, 1.7))
        edge_pairs = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        coordinates = list(corners) + [
            tuple((corners[first][axis] + corners[second][axis]) / 2.0 for axis in range(3))
            for first, second in edge_pairs
        ]
        nodes = {index + 1: value for index, value in enumerate(coordinates)}
        for integration_point, natural in enumerate(GAUSS_NATURAL_COORDINATES, start=1):
            barycentric = (1.0 - sum(natural), *natural)
            expected = tuple(
                sum(barycentric[node] * corners[node][axis] for node in range(4))
                for axis in range(3)
            )
            actual = interpolate_coordinates(list(nodes), nodes, natural)
            self.assertTrue(
                all(math.isclose(actual[axis], expected[axis], abs_tol=1.0e-15) for axis in range(3)),
                msg=f"integration point {integration_point}",
            )

    def test_linear_bending_field_reconstruction_and_signs(self) -> None:
        intercept = 1250.0
        depth_slope = -1.536e9
        samples = []
        for x in (SECTION_X_M - 0.01, SECTION_X_M + 0.01):
            for y in (0.01, 0.04):
                for z in (0.005, 0.025, 0.045):
                    xc = x - SECTION_X_M
                    yc = y - WIDTH_M / 2.0
                    zc = z - HEIGHT_M / 2.0
                    stress = intercept + 2.0e6 * xc + 3.0e6 * yc + depth_slope * zc + 8.0e8 * xc * zc
                    samples.append(
                        {
                            "coordinates_m": [x, y, z],
                            "stress_pa": [stress, 0.0, 0.0, 0.0, 0.0, 0.0],
                            "weight_m3": 1.0,
                        }
                    )
        result = fit_section_field(samples)
        self.assertTrue(math.isclose(result["neutral_axis_sigma_xx_pa"], intercept, abs_tol=1.0e-5))
        self.assertTrue(
            math.isclose(
                result["coefficients"]["section_depth_slope_pa_per_m"],
                depth_slope,
                rel_tol=1.0e-12,
            )
        )
        self.assertLess(result["upper_outer_fiber_sigma_xx_pa"], 0.0)
        self.assertGreater(result["lower_outer_fiber_sigma_xx_pa"], 0.0)
        self.assertTrue(result["outer_fibers_have_opposite_signs"])
        self.assertLess(result["weighted_rmse_pa"], 1.0e-5)

    def test_simple_z_cross_check_omits_main_reconstruction_terms(self) -> None:
        samples = []
        for z in (0.005, 0.015, 0.035, 0.045):
            stress = 1.536e9 * (z - HEIGHT_M / 2.0)
            samples.append(
                {
                    "coordinates_m": [SECTION_X_M, WIDTH_M / 2.0, z],
                    "stress_pa": [stress, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "weight_m3": 1.0,
                }
            )
        result = simple_z_cross_check(samples)
        self.assertTrue(math.isclose(result["outer_fiber_magnitude_pa"], 38.4e6))
        self.assertEqual(result["percent_euler_bernoulli_disagreement"], 0.0)
        self.assertEqual(result["rmse_pa"], 0.0)

    def test_straight_tetrahedron_equal_quadrature_weights_sum_to_volume(self) -> None:
        volume = tetrahedron_volume(
            [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 4.0)]
        )
        self.assertEqual(volume, 4.0)
        self.assertEqual(sum([volume / 4.0] * 4), volume)

    def test_von_mises_uniaxial_and_hydrostatic(self) -> None:
        self.assertEqual(von_mises([12.0e6, 12.0e6, 12.0e6, 0.0, 0.0, 0.0]), 0.0)
        self.assertTrue(
            math.isclose(von_mises([38.4e6, 0.0, 0.0, 0.0, 0.0, 0.0]), 38.4e6)
        )
        self.assertTrue(
            math.isclose(von_mises([0.0, 0.0, 0.0, 10.0e6, 0.0, 0.0]), math.sqrt(3.0) * 10.0e6)
        )


if __name__ == "__main__":
    unittest.main()
