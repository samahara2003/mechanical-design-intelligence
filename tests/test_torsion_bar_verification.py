import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_torsion_bar_solve import (  # noqa: E402
    FACE_CENTROID_M,
    consistent_torque_face_loads,
    force_and_moment_resultants,
)
from torsion_bar_verification import (  # noqa: E402
    LENGTH_M,
    POISSONS_RATIO,
    SIDE_M,
    TORQUE_N_M,
    YOUNGS_MODULUS_PA,
    analytical_twist,
    fit_rotation_observations,
    fit_section_rotation,
    fit_twist_rate,
    interpolate_c3d10_displacement,
    shear_modulus,
    square_torsional_constant,
    tetrahedron_plane_polygon,
    warping_summary,
)


class TorsionBarLogicTests(unittest.TestCase):
    def test_analytical_torsion_reference(self) -> None:
        modulus = shear_modulus(YOUNGS_MODULUS_PA, POISSONS_RATIO)
        torsional_constant = square_torsional_constant(SIDE_M)
        self.assertTrue(math.isclose(modulus, 76.92307692307692e9))
        self.assertTrue(math.isclose(torsional_constant, 0.1406 * SIDE_M**4))
        self.assertTrue(
            math.isclose(
                analytical_twist(TORQUE_N_M, LENGTH_M, modulus, torsional_constant),
                0.001479374110953058,
            )
        )

    def test_distributed_face_load_has_pure_requested_torque(self) -> None:
        nodes = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, SIDE_M, 0.0),
            3: (1.0, SIDE_M, SIDE_M),
            4: (1.0, 0.0, SIDE_M),
            5: (1.0, SIDE_M / 2.0, 0.0),
            6: (1.0, SIDE_M, SIDE_M / 2.0),
            7: (1.0, SIDE_M / 2.0, SIDE_M / 2.0),
            8: (1.0, SIDE_M / 2.0, SIDE_M),
            9: (1.0, 0.0, SIDE_M / 2.0),
        }
        faces = [
            {"id": 1, "nodes": [1, 2, 3, 5, 6, 7]},
            {"id": 2, "nodes": [1, 3, 4, 7, 8, 9]},
        ]
        loads, evidence = consistent_torque_face_loads(faces, nodes, TORQUE_N_M)
        force, moment = force_and_moment_resultants(loads, nodes, FACE_CENTROID_M)
        self.assertTrue(all(abs(value) < 1.0e-12 for value in force))
        self.assertTrue(math.isclose(moment[0], TORQUE_N_M, rel_tol=1e-14))
        self.assertTrue(abs(moment[1]) < 1.0e-12 and abs(moment[2]) < 1.0e-12)
        self.assertTrue(math.isclose(evidence["face_area_m2"], SIDE_M**2))
        self.assertGreater(evidence["traction_scale_pa_per_m"], 0.0)

    def test_force_and_moment_resultants(self) -> None:
        nodes = {1: (1.0, 0.0, 0.0), 2: (1.0, 0.05, 0.0)}
        loads = {1: (0.0, 0.0, -10.0), 2: (0.0, 0.0, 10.0)}
        force, moment = force_and_moment_resultants(loads, nodes, (1.0, 0.025, 0.0))
        self.assertEqual(force, (0.0, 0.0, 0.0))
        self.assertTrue(math.isclose(moment[0], 0.5))
        self.assertEqual(moment[1:], (0.0, 0.0))

    def test_rotation_fit_ignores_translation_and_axial_warping(self) -> None:
        nodes = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, SIDE_M, 0.0),
            3: (1.0, SIDE_M, SIDE_M),
            4: (1.0, 0.0, SIDE_M),
            5: (1.0, SIDE_M / 2.0, SIDE_M / 2.0),
        }
        theta = 0.0015
        translation_y = 2.0e-7
        translation_z = -3.0e-7
        displacements = {}
        for node, (_, y, z) in nodes.items():
            ux_warping = 1.0e-5 * (y - SIDE_M / 2.0) * (z - SIDE_M / 2.0)
            displacements[node] = (
                ux_warping,
                translation_y - theta * (z - SIDE_M / 2.0),
                translation_z + theta * (y - SIDE_M / 2.0),
            )
        result = fit_section_rotation(list(nodes), nodes, displacements)
        self.assertTrue(math.isclose(result["rotation_rad"], theta, abs_tol=1e-15))
        self.assertTrue(math.isclose(result["fitted_rigid_translation_y_m"], translation_y, abs_tol=1e-15))
        self.assertTrue(math.isclose(result["fitted_rigid_translation_z_m"], translation_z, abs_tol=1e-15))
        self.assertLess(result["residual_rms_m"], 1e-18)
        warping = warping_summary(list(nodes), displacements)
        self.assertLess(warping["minimum_ux_m"], 0.0)
        self.assertGreater(warping["maximum_ux_m"], 0.0)

    def test_weighted_rotation_observations_recover_rotation_and_translation(self) -> None:
        theta = 0.0012
        translation_y = -4.0e-7
        translation_z = 7.0e-7
        observations = []
        for index, (y, z) in enumerate(((0.0, 0.0), (0.05, 0.0), (0.05, 0.05), (0.0, 0.05))):
            observations.append(
                {
                    "y_m": y,
                    "z_m": z,
                    "uy_m": translation_y - theta * (z - 0.025),
                    "uz_m": translation_z + theta * (y - 0.025),
                    "weight_m2": float(index + 1),
                }
            )
        result = fit_rotation_observations(observations)
        self.assertTrue(math.isclose(result["rotation_rad"], theta, abs_tol=1e-15))
        self.assertTrue(math.isclose(result["fitted_rigid_translation_y_m"], translation_y, abs_tol=1e-15))
        self.assertTrue(math.isclose(result["fitted_rigid_translation_z_m"], translation_z, abs_tol=1e-15))
        self.assertLess(result["residual_rms_m"], 1e-18)

    def test_linear_twist_rate_recovery(self) -> None:
        alpha = 2.0e-6
        beta = 0.00147
        sections = [
            {"actual_evaluated_x_m": x, "rotation_rad": alpha + beta * x}
            for x in (0.25, 0.50, 0.75)
        ]
        result = fit_twist_rate(sections)
        self.assertTrue(math.isclose(result["alpha_rad"], alpha, abs_tol=1e-18))
        self.assertTrue(math.isclose(result["beta_rad_per_m"], beta, abs_tol=1e-18))
        self.assertLess(result["residual_rms_rad"], 1e-18)
        self.assertTrue(math.isclose(result["r_squared"], 1.0))

    def test_plane_intersection_and_c3d10_interpolation(self) -> None:
        coordinates = {
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
        polygon = tetrahedron_plane_polygon(
            [coordinates[index] for index in range(1, 5)], 0.25
        )
        self.assertEqual(len(polygon), 3)
        self.assertTrue(all(math.isclose(point[0], 0.25) for point in polygon))
        displacements = {
            node: (x + y, 2.0 * x - 3.0 * z, 4.0 * y + x)
            for node, (x, y, z) in coordinates.items()
        }
        point = (0.25, 0.25, 0.25)
        value = interpolate_c3d10_displacement(
            point, list(range(1, 11)), coordinates, displacements
        )
        self.assertTrue(
            all(
                math.isclose(value[index], expected, abs_tol=1e-15)
                for index, expected in enumerate((0.5, -0.25, 1.25))
            )
        )


if __name__ == "__main__":
    unittest.main()
