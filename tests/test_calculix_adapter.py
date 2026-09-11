import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from axial_bar_definition import axial_bar_analysis_definition  # noqa: E402
from calculix_adapter import (  # noqa: E402
    CalculixAdapterError,
    calculix_dof,
    render_axial_linear_static_deck,
    translate_boundary_condition,
    translate_material,
    translate_nodal_force_representation,
)
from engineering_domain import (  # noqa: E402
    AnalysisType,
    BoundaryCondition,
    GeometryEntity,
    GeometrySelection,
    MaterialSnapshot,
    SolverConfig,
    TranslationalDof,
)
from surface_load_mapping import map_uniform_force_to_c3d10_faces  # noqa: E402


class CalculixAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis = axial_bar_analysis_definition("4.15.2", "2.23")

    def test_translational_dof_mapping(self) -> None:
        self.assertEqual(calculix_dof(TranslationalDof.UX), 1)
        self.assertEqual(calculix_dof(TranslationalDof.UY), 2)
        self.assertEqual(calculix_dof(TranslationalDof.UZ), 3)
        with self.assertRaises(CalculixAdapterError):
            calculix_dof("UX")

    def test_material_translation_uses_only_linear_elastic_values(self) -> None:
        material = MaterialSnapshot(
            "STEEL",
            200.0e9,
            0.3,
            density_kg_per_m3=7850.0,
            yield_strength_pa=250.0e6,
        )
        before = material
        translated = translate_material(material)
        self.assertEqual(
            translated.lines(),
            ("*MATERIAL, NAME=STEEL", "*ELASTIC", "200000000000, 0.3"),
        )
        self.assertEqual(material, before)
        self.assertNotIn("7850", "\n".join(translated.lines()))
        self.assertNotIn("250000000", "\n".join(translated.lines()))

    def test_fixed_boundary_translation_uses_resolved_nodes(self) -> None:
        boundary = translate_boundary_condition(
            self.analysis.boundary_conditions[0],
            self.analysis.boundary_conditions[0].target,
            [9, 3, 9, 6],
            "FIXED",
        )
        self.assertEqual(boundary.node_ids, (3, 6, 9))
        self.assertEqual(boundary.dof_ranges, ((1, 3),))
        self.assertEqual(boundary.constraint_lines(), ("FIXED, 1, 3, 0",))
        with self.assertRaises(CalculixAdapterError):
            translate_boundary_condition(
                self.analysis.boundary_conditions[0],
                GeometrySelection("other", GeometryEntity.FACE),
                [1],
                "FIXED",
            )

    def test_noncontiguous_domain_dofs_are_not_silently_widened(self) -> None:
        condition = BoundaryCondition(
            self.analysis.boundary_conditions[0].target,
            (TranslationalDof.UX, TranslationalDof.UZ),
        )
        boundary = translate_boundary_condition(condition, condition.target, [1], "FIXED")
        self.assertEqual(boundary.dof_ranges, ((1, 1), (3, 3)))
        self.assertEqual(
            boundary.constraint_lines(),
            ("FIXED, 1, 1, 0", "FIXED, 3, 3, 0"),
        )

    def test_force_mapping_and_calculix_translation_preserve_domain_load(self) -> None:
        width = 0.05
        height = 0.05
        nodes = {
            1: (1.0, 0.0, 0.0),
            2: (1.0, width, 0.0),
            3: (1.0, 0.0, height),
            4: (1.0, width / 2.0, 0.0),
            5: (1.0, width / 2.0, height / 2.0),
            6: (1.0, 0.0, height / 2.0),
        }
        face = {"id": 1, "nodes": [1, 2, 3, 4, 5, 6]}
        force_before = self.analysis.loads[0]
        nodal, area, traction = map_uniform_force_to_c3d10_faces(
            force_before, [face, face], nodes, width * height
        )
        rows = translate_nodal_force_representation(
            force_before, force_before.target, nodal
        )
        self.assertEqual(area, width * height)
        self.assertTrue(math.isclose(traction[0], 400000.0))
        self.assertEqual(traction[1:], (0.0, 0.0))
        self.assertTrue(math.isclose(sum(row.value_n for row in rows), 1000.0))
        self.assertTrue(all(row.dof == 1 for row in rows))
        self.assertIs(self.analysis.loads[0], force_before)

    def test_deterministic_axial_deck_formatting(self) -> None:
        nodes = {node: (float(node), 0.0, 0.0) for node in range(1, 11)}
        elements = [{"id": 20, "nodes": list(range(1, 11))}]
        boundary = translate_boundary_condition(
            self.analysis.boundary_conditions[0],
            self.analysis.boundary_conditions[0].target,
            [1, 2, 3],
            "FIXED",
        )
        loads = translate_nodal_force_representation(
            self.analysis.loads[0],
            self.analysis.loads[0].target,
            {4: (1000.0, 0.0, 0.0)},
        )
        deck = render_axial_linear_static_deck(
            self.analysis, nodes, elements, boundary, [4, 5], [(20, "S1")], loads
        )
        expected = [
            "*HEADING",
            "Mechanical Design Intelligence - axial bar C3D10 verification",
            "*NODE, NSET=ALLNODES",
            *(f"{node}, {float(node):.16g}, 0, 0" for node in range(1, 11)),
            "*ELEMENT, TYPE=C3D10, ELSET=AXIAL_BAR",
            "20, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10",
            "*NSET, NSET=FIXED",
            "1, 2, 3",
            "*NSET, NSET=AXIAL_LOAD_NODES",
            "4, 5",
            "*ELSET, ELSET=AXIAL_LOAD_S1",
            "20",
            "*SURFACE, NAME=AXIAL_LOAD_FACE, TYPE=ELEMENT",
            "AXIAL_LOAD_S1, S1",
            "*MATERIAL, NAME=STEEL",
            "*ELASTIC",
            "200000000000, 0.3",
            "*SOLID SECTION, ELSET=AXIAL_BAR, MATERIAL=STEEL",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            "FIXED, 1, 3, 0",
            "*CLOAD",
            "4, 1, 1000",
            "*NODE FILE",
            "U, RF",
            "*EL FILE",
            "S",
            "*EL PRINT, ELSET=AXIAL_BAR",
            "S",
            "*NODE PRINT, NSET=ALLNODES",
            "U",
            "*NODE PRINT, NSET=FIXED, TOTALS=YES",
            "RF",
            "*END STEP",
            "",
        ]
        self.assertEqual(deck, "\n".join(expected))

    def test_unsupported_solver_is_rejected(self) -> None:
        unsupported = replace(
            self.analysis,
            solver=SolverConfig(
                "other-solver",
                "1.0",
                AnalysisType.LINEAR_STATIC,
                True,
                self.analysis.solver.output_requests,
            ),
        )
        with self.assertRaisesRegex(CalculixAdapterError, "only CalculiX"):
            render_axial_linear_static_deck(unsupported, {}, [], None, [], [], ())


if __name__ == "__main__":
    unittest.main()
