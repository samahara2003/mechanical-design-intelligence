import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cantilever_mesh_convergence import (  # noqa: E402
    add_successive_changes,
    successive_change,
)


class ConvergenceChangeTests(unittest.TestCase):
    def test_first_level_has_no_previous_change(self) -> None:
        self.assertEqual(successive_change(-0.003, None), (None, None))

    def test_successive_absolute_and_relative_change(self) -> None:
        absolute, relative = successive_change(-0.0031, -0.003)
        self.assertTrue(math.isclose(absolute, 0.0001, rel_tol=1.0e-14))
        self.assertTrue(math.isclose(relative, 1.0 / 30.0, rel_tol=1.0e-14))

    def test_record_order_and_signed_displacement_are_preserved(self) -> None:
        source = [
            {"level": "coarse", "centroid_uz_m": -0.0030},
            {"level": "fine", "centroid_uz_m": -0.0031},
        ]
        results = add_successive_changes(source)
        self.assertEqual([result["level"] for result in results], ["coarse", "fine"])
        self.assertEqual([result["centroid_uz_m"] for result in results], [-0.0030, -0.0031])
        self.assertIsNone(results[0]["relative_change_from_previous"])
        self.assertTrue(
            math.isclose(results[1]["relative_change_from_previous"], 1.0 / 30.0)
        )

    def test_zero_previous_displacement_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            successive_change(-0.003, 0.0)


if __name__ == "__main__":
    unittest.main()
