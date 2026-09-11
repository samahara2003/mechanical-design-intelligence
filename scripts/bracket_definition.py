"""Engineering definition for the first realistic mounting-bracket case."""

from __future__ import annotations

from engineering_domain import (
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
    SolverConfig,
    TranslationalDof,
)


BRACKET_MODEL_VERSION = ModelVersionReference("integration:mounting-bracket-step:v1")
BRACKET_VOLUME = GeometrySelection("bracket", GeometryEntity.VOLUME)
BRACKET_MOUNTING_FACES = GeometrySelection("mounting_holes", GeometryEntity.FACE)
BRACKET_LOAD_FACE = GeometrySelection("load_pad", GeometryEntity.FACE)
BRACKET_MATERIAL = MaterialSnapshot(
    name="STEEL",
    youngs_modulus_pa=200.0e9,
    poissons_ratio=0.30,
    density_kg_per_m3=7850.0,
)
BRACKET_FORCE = ForceLoad(
    target=BRACKET_LOAD_FACE,
    magnitude_n=2500.0,
    direction=(1.0, 0.0, 0.0),
)
BRACKET_FIXED_BOUNDARY = BoundaryCondition(
    target=BRACKET_MOUNTING_FACES,
    constrained_dofs=(TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
)
BASELINE_MESH_SIZE_M = 0.006
FINER_MESH_SIZE_M = 0.004


def bracket_analysis_definition(
    gmsh_version: str,
    calculix_version: str,
    mesh_size_m: float = BASELINE_MESH_SIZE_M,
) -> AnalysisDefinition:
    """Create one immutable, resolved C3D10 bracket definition in SI units."""
    return AnalysisDefinition(
        model_version=BRACKET_MODEL_VERSION,
        material=BRACKET_MATERIAL,
        loads=(BRACKET_FORCE,),
        boundary_conditions=(BRACKET_FIXED_BOUNDARY,),
        mesh=MeshConfig(
            element_type=MeshElementType.C3D10,
            characteristic_size_m=mesh_size_m,
            mesher_identifier="Gmsh/OpenCASCADE",
            mesher_version=gmsh_version,
        ),
        solver=SolverConfig(
            solver_identifier="CalculiX",
            solver_version=calculix_version,
            analysis_type=AnalysisType.LINEAR_STATIC,
            small_deformation=True,
            output_requests=("displacement", "reaction_force", "integration_point_stress"),
        ),
        required_factor_of_safety=None,
    )
