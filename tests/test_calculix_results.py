import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from calculix_results import (  # noqa: E402
    CalculixResultParseError,
    parse_calculix_dat_text,
    parse_displacements_dat,
    parse_integration_point_stresses_dat,
    parse_reactions_dat,
)
from engineering_postprocessing import vector_magnitude, von_mises_stress_pa  # noqa: E402
from numerical_results import (  # noqa: E402
    IntegrationPointStress,
    NodalDisplacement,
    NodalReaction,
    NumericalResult,
    StressTensor,
    Vector3,
)


REPRESENTATIVE_DAT = """
 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set AXIAL_BAR and time  0.1000000E+01

       8   2  4.000000E+05  2.000000E+01 -3.000000E+01  4.000000E+00  5.000000E+00 -6.000000E+00
       8   1  3.999000E+05  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00  0.000000E+00

 displacements (vx,vy,vz) for set NALL and time  0.1000000E+01

       2  2.000000E-06 -3.000000E-07  4.000000E-07
       1  0.000000E+00  0.000000E+00  0.000000E+00

 forces (fx,fy,fz) for set FIXED and time  0.1000000E+01

       5 -4.000000E+02  2.000000E-10 -3.000000E-10
       3 -6.000000E+02 -2.000000E-10  3.000000E-10

 total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

         -1.000000E+03  0.000000E+00  0.000000E+00
"""


class CalculixResultParserTests(unittest.TestCase):
    def test_parses_dat_component_semantics_into_neutral_result(self) -> None:
        result = parse_calculix_dat_text(REPRESENTATIVE_DAT)

        self.assertEqual([item.node_id for item in result.displacements], [1, 2])
        self.assertEqual(result.displacements[1].displacement_m.as_tuple(), (2e-6, -3e-7, 4e-7))
        self.assertEqual([item.node_id for item in result.reactions], [3, 5])
        self.assertEqual(result.reaction_resultant_n.as_tuple(), (-1000.0, 0.0, 0.0))
        self.assertEqual(
            [(item.element_id, item.integration_point) for item in result.integration_point_stresses],
            [(8, 1), (8, 2)],
        )
        self.assertEqual(
            result.integration_point_stresses[1].stress_pa.as_tuple(),
            (400000.0, 20.0, -30.0, 4.0, 5.0, -6.0),
        )

    def test_missing_or_truncated_displacement_components_are_rejected(self) -> None:
        text = (
            "displacements (vx,vy,vz) for set NALL\n"
            "  1  1.0  2.0  3.0\n"
            "  2  1.0  2.0\n"
        )
        with self.assertRaises(CalculixResultParseError):
            parse_displacements_dat(text)
        with self.assertRaises(CalculixResultParseError):
            parse_displacements_dat("unrelated output")

    def test_missing_or_truncated_stress_components_are_rejected(self) -> None:
        text = (
            "stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set AXIAL_BAR\n"
            "  1  1  10.0  20.0  30.0  40.0  50.0  60.0\n"
            "  1  1  10.0  20.0  30.0  40.0  50.0\n"
        )
        with self.assertRaises(CalculixResultParseError):
            parse_integration_point_stresses_dat(text)

    def test_truncated_reaction_components_are_rejected(self) -> None:
        text = (
            "forces (fx,fy,fz) for set FIXED\n"
            "  1  -500.0  0.0  0.0\n"
            "  2  -500.0  0.0\n"
        )
        with self.assertRaises(CalculixResultParseError):
            parse_reactions_dat(text)


class NumericalResultTests(unittest.TestCase):
    def test_snapshot_and_nested_values_are_immutable(self) -> None:
        source_displacements = [NodalDisplacement(2, Vector3(1.0, 2.0, 3.0))]
        result = NumericalResult(
            displacements=source_displacements,
            reactions=[NodalReaction(1, Vector3(-1.0, 0.0, 0.0))],
            integration_point_stresses=[
                IntegrationPointStress(3, 1, StressTensor(1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            ],
        )
        source_displacements.append(NodalDisplacement(4, Vector3(0.0, 0.0, 0.0)))
        self.assertIsInstance(result.displacements, tuple)
        self.assertEqual(len(result.displacements), 1)
        with self.assertRaises(FrozenInstanceError):
            result.displacements[0].node_id = 9
        with self.assertRaises(FrozenInstanceError):
            result.integration_point_stresses[0].stress_pa.sigma_xx_pa = 2.0

    def test_duplicate_numerical_identities_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            NumericalResult(
                displacements=(
                    NodalDisplacement(1, Vector3(0.0, 0.0, 0.0)),
                    NodalDisplacement(1, Vector3(1.0, 0.0, 0.0)),
                ),
                reactions=(),
                integration_point_stresses=(),
            )

    def test_von_mises_reference_states(self) -> None:
        self.assertEqual(
            von_mises_stress_pa(StressTensor(400000.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            400000.0,
        )
        self.assertEqual(
            von_mises_stress_pa(StressTensor(12.0, 12.0, 12.0, 0.0, 0.0, 0.0)),
            0.0,
        )
        self.assertAlmostEqual(
            von_mises_stress_pa(StressTensor(0.0, 0.0, 0.0, 10.0, 0.0, 0.0)),
            math.sqrt(3.0) * 10.0,
        )
        self.assertEqual(
            von_mises_stress_pa(StressTensor(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            0.0,
        )

    def test_vector_magnitude(self) -> None:
        self.assertEqual(vector_magnitude(Vector3(3.0, 4.0, 0.0)), 5.0)


if __name__ == "__main__":
    unittest.main()
