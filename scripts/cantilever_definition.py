"""Engineering-domain definition for the verified C3D10 cantilever benchmark."""

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


CANTILEVER_MODEL_VERSION = ModelVersionReference("benchmark:cantilever-step:v1")
CANTILEVER_VOLUME = GeometrySelection("beam", GeometryEntity.VOLUME)
CANTILEVER_FIXED_FACE = GeometrySelection("fixed", GeometryEntity.FACE)
CANTILEVER_LOAD_FACE = GeometrySelection("load", GeometryEntity.FACE)
CANTILEVER_MATERIAL = MaterialSnapshot(
    name="STEEL",
    youngs_modulus_pa=200.0e9,
    poissons_ratio=0.30,
)
CANTILEVER_FORCE = ForceLoad(
    target=CANTILEVER_LOAD_FACE,
    magnitude_n=1000.0,
    direction=(0.0, 0.0, -1.0),
)
CANTILEVER_FIXED_BOUNDARY = BoundaryCondition(
    target=CANTILEVER_FIXED_FACE,
    constrained_dofs=(TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
)
CANTILEVER_FINE_MESH_SIZE_M = 0.0125


def cantilever_analysis_definition(
    gmsh_version: str,
    calculix_version: str,
    mesh_size_m: float = CANTILEVER_FINE_MESH_SIZE_M,
) -> AnalysisDefinition:
    """Create the immutable resolved definition for one C3D10 cantilever execution."""
    return AnalysisDefinition(
        model_version=CANTILEVER_MODEL_VERSION,
        material=CANTILEVER_MATERIAL,
        loads=(CANTILEVER_FORCE,),
        boundary_conditions=(CANTILEVER_FIXED_BOUNDARY,),
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
