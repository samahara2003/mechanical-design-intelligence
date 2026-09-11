import math
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from axial_bar_definition import axial_bar_analysis_definition  # noqa: E402
from engineering_domain import (  # noqa: E402
    AnalysisDefinition,
    AnalysisType,
    BoundaryCondition,
    ForceLoad,
    GeometryEntity,
    GeometrySelection,
    MaterialSnapshot,
    MeshConfig,
    MeshElementType,
    ModelVersionReference,
    PressureLoad,
    SolverConfig,
    SurfaceNormalDirection,
    TranslationalDof,
)


class EngineeringDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.face = GeometrySelection("loaded_face", GeometryEntity.FACE)
        self.fixed = BoundaryCondition(
            GeometrySelection("fixed", GeometryEntity.FACE),
            (TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
        )
        self.material = MaterialSnapshot("steel", 200.0e9, 0.3)
        self.force = ForceLoad(self.face, 1000.0, (2.0, 0.0, 0.0))
        self.mesh = MeshConfig(MeshElementType.C3D10, 0.0125, "Gmsh/OpenCASCADE", "4.15.2")
        self.solver = SolverConfig(
            "CalculiX",
            "2.23",
            AnalysisType.LINEAR_STATIC,
            True,
            ("displacement", "stress"),
        )

    def analysis(self, **changes) -> AnalysisDefinition:
        values = {
            "model_version": ModelVersionReference("model:v1"),
            "material": self.material,
            "loads": (self.force,),
            "boundary_conditions": (self.fixed,),
            "mesh": self.mesh,
            "solver": self.solver,
            "required_factor_of_safety": None,
        }
        values.update(changes)
        return AnalysisDefinition(**values)

    def test_valid_material_snapshot(self) -> None:
        material = MaterialSnapshot(
            "steel", 200.0e9, 0.3, density_kg_per_m3=7850.0, yield_strength_pa=250.0e6
        )
        self.assertEqual(material.youngs_modulus_pa, 200.0e9)
        self.assertEqual(material.density_kg_per_m3, 7850.0)

    def test_invalid_youngs_modulus_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MaterialSnapshot("invalid", 0.0, 0.3)

    def test_invalid_poisson_ratio_is_rejected(self) -> None:
        for value in (-1.0, 0.5, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MaterialSnapshot("invalid", 1.0, value)

    def test_geometry_selection_requires_named_entity(self) -> None:
        selection = GeometrySelection("fixed", GeometryEntity.FACE)
        self.assertEqual(selection.region_name, "fixed")
        with self.assertRaises(ValueError):
            GeometrySelection(" ", GeometryEntity.FACE)

    def test_boundary_condition_dofs_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            BoundaryCondition(self.face, ())
        with self.assertRaises(ValueError):
            BoundaryCondition(self.face, (TranslationalDof.UX, TranslationalDof.UX))

    def test_force_direction_is_normalized_and_zero_is_rejected(self) -> None:
        self.assertEqual(self.force.direction, (1.0, 0.0, 0.0))
        self.assertEqual(self.force.vector_n, (1000.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            ForceLoad(self.face, 1000.0, (0.0, 0.0, 0.0))

    def test_pressure_has_explicit_geometry_normal_convention(self) -> None:
        pressure = PressureLoad(self.face, 400000.0, SurfaceNormalDirection.INWARD)
        self.assertEqual(pressure.magnitude_pa, 400000.0)
        self.assertEqual(pressure.normal_direction, SurfaceNormalDirection.INWARD)

    def test_mesh_size_and_solver_configuration_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            MeshConfig(MeshElementType.C3D10, 0.0, "Gmsh", "4.15.2")
        self.assertEqual(self.mesh.element_order, 2)
        self.assertEqual(self.solver.analysis_type, AnalysisType.LINEAR_STATIC)
        with self.assertRaises(ValueError):
            SolverConfig("CalculiX", "2.23", AnalysisType.LINEAR_STATIC, False)

    def test_analysis_and_nested_collections_are_immutable(self) -> None:
        mutable_loads = [self.force]
        definition = self.analysis(loads=mutable_loads)
        mutable_loads.clear()
        self.assertEqual(definition.loads, (self.force,))
        self.assertIsInstance(definition.loads, tuple)
        with self.assertRaises(FrozenInstanceError):
            definition.required_factor_of_safety = 2.0
        with self.assertRaises(FrozenInstanceError):
            definition.material.poissons_ratio = 0.29

    def test_required_factor_of_safety_is_validated(self) -> None:
        self.assertEqual(self.analysis(required_factor_of_safety=2.0).required_factor_of_safety, 2.0)
        for value in (-1.0, 0.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.analysis(required_factor_of_safety=value)

    def test_axial_definition_reproduces_verified_inputs(self) -> None:
        definition = axial_bar_analysis_definition("4.15.2", "2.23")
        self.assertEqual(definition.model_version.value, "benchmark:axial-bar-step:v1")
        self.assertEqual(definition.material.youngs_modulus_pa, 200.0e9)
        self.assertEqual(definition.material.poissons_ratio, 0.30)
        self.assertEqual(definition.loads[0].vector_n, (1000.0, 0.0, 0.0))
        self.assertEqual(
            definition.boundary_conditions[0].constrained_dofs,
            (TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
        )
        self.assertEqual(definition.mesh.element_type, MeshElementType.C3D10)
        self.assertEqual(definition.mesh.characteristic_size_m, 0.0125)
        self.assertEqual(definition.solver.analysis_type, AnalysisType.LINEAR_STATIC)
        self.assertTrue(definition.solver.small_deformation)


if __name__ == "__main__":
    unittest.main()
