"""Engineering-domain definition for the verified axial-bar benchmark."""

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


AXIAL_BAR_MODEL_VERSION = ModelVersionReference("benchmark:axial-bar-step:v1")
AXIAL_BAR_VOLUME = GeometrySelection("axial_bar", GeometryEntity.VOLUME)
AXIAL_FIXED_FACE = GeometrySelection("fixed", GeometryEntity.FACE)
AXIAL_LOAD_FACE = GeometrySelection("axial_load", GeometryEntity.FACE)
AXIAL_MATERIAL = MaterialSnapshot(
    name="STEEL",
    youngs_modulus_pa=200.0e9,
    poissons_ratio=0.30,
)
AXIAL_FORCE = ForceLoad(
    target=AXIAL_LOAD_FACE,
    magnitude_n=1000.0,
    direction=(1.0, 0.0, 0.0),
)
AXIAL_FIXED_BOUNDARY = BoundaryCondition(
    target=AXIAL_FIXED_FACE,
    constrained_dofs=(TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ),
)
AXIAL_MESH_SIZE_M = 0.0125


def axial_bar_analysis_definition(
    gmsh_version: str,
    calculix_version: str,
) -> AnalysisDefinition:
    """Create the immutable resolved definition used by one axial benchmark execution."""
    return AnalysisDefinition(
        model_version=AXIAL_BAR_MODEL_VERSION,
        material=AXIAL_MATERIAL,
        loads=(AXIAL_FORCE,),
        boundary_conditions=(AXIAL_FIXED_BOUNDARY,),
        mesh=MeshConfig(
            element_type=MeshElementType.C3D10,
            characteristic_size_m=AXIAL_MESH_SIZE_M,
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
