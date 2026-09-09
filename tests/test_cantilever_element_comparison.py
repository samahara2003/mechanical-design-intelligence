import math
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cantilever_element_comparison import group_formulation_results  # noqa: E402


class ElementComparisonGroupingTests(unittest.TestCase):
    def test_changes_are_grouped_within_each_formulation(self) -> None:
        source = [
            {"formulation": "C3D4", "level": "coarse", "centroid_uz_m": -0.001},
            {"formulation": "C3D4", "level": "fine", "centroid_uz_m": -0.002},
            {"formulation": "C3D10", "level": "coarse", "centroid_uz_m": -0.003},
            {"formulation": "C3D10", "level": "fine", "centroid_uz_m": -0.0031},
        ]
        grouped = group_formulation_results(source)
        self.assertEqual(list(grouped), ["C3D4", "C3D10"])
        self.assertIsNone(grouped["C3D4"][0]["relative_change_from_previous"])
        self.assertIsNone(grouped["C3D10"][0]["relative_change_from_previous"])
        self.assertTrue(
            math.isclose(grouped["C3D4"][1]["relative_change_from_previous"], 1.0)
        )
        self.assertTrue(
            math.isclose(
                grouped["C3D10"][1]["relative_change_from_previous"], 1.0 / 30.0
            )
        )
        self.assertEqual(grouped["C3D4"][1]["centroid_uz_m"], -0.002)
        self.assertEqual(grouped["C3D10"][1]["centroid_uz_m"], -0.0031)

    def test_unknown_formulation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            group_formulation_results(
                [{"formulation": "UNKNOWN", "level": "coarse", "centroid_uz_m": -1.0}]
            )


if __name__ == "__main__":
    unittest.main()
