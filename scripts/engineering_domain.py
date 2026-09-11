"""Immutable engineering-intent definitions for the current V1 analysis scope.

All dimensional values use SI units. These objects describe geometry-level
engineering intent and resolved mesh/solver configuration; they deliberately do
not contain mesh node IDs, element IDs, generated artifacts, or solver results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonempty")
    return value.strip()


def _positive_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{field_name} must be positive and finite")


class GeometryEntity(str, Enum):
    FACE = "face"
    VOLUME = "volume"


class TranslationalDof(str, Enum):
    UX = "UX"
    UY = "UY"
    UZ = "UZ"


class SurfaceNormalDirection(str, Enum):
    OUTWARD = "outward"
    INWARD = "inward"


class MeshElementType(str, Enum):
    C3D10 = "C3D10"

    @property
    def order(self) -> int:
        return 2


class AnalysisType(str, Enum):
    LINEAR_STATIC = "linear_static"


@dataclass(frozen=True)
class MaterialSource:
    """Small immutable reference to material-property provenance."""

    reference: str
    revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", _required_text(self.reference, "material source"))
        if self.revision is not None:
            object.__setattr__(self, "revision", _required_text(self.revision, "source revision"))


@dataclass(frozen=True)
class MaterialSnapshot:
    """Executed linear-isotropic material values, snapshotted in SI units."""

    name: str
    youngs_modulus_pa: float
    poissons_ratio: float
    density_kg_per_m3: float | None = None
    yield_strength_pa: float | None = None
    source: MaterialSource | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "material name"))
        _positive_finite(self.youngs_modulus_pa, "Young's modulus")
        if not math.isfinite(self.poissons_ratio) or not -1.0 < self.poissons_ratio < 0.5:
            raise ValueError("Poisson ratio must be finite and satisfy -1 < nu < 0.5")
        if self.density_kg_per_m3 is not None:
            _positive_finite(self.density_kg_per_m3, "density")
        if self.yield_strength_pa is not None:
            _positive_finite(self.yield_strength_pa, "yield strength")
        if self.source is not None and not isinstance(self.source, MaterialSource):
            raise TypeError("material source must be a MaterialSource")


@dataclass(frozen=True)
class ModelVersionReference:
    """Stable reference to one immutable geometry revision; not a database ID."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _required_text(self.value, "model version reference"))


@dataclass(frozen=True)
class GeometrySelection:
    """Named geometry region carried into meshing; not persistent CAD topology."""

    region_name: str
    entity: GeometryEntity

    def __post_init__(self) -> None:
        object.__setattr__(self, "region_name", _required_text(self.region_name, "region name"))
        if not isinstance(self.entity, GeometryEntity):
            raise TypeError("geometry selection entity must be a GeometryEntity")


@dataclass(frozen=True)
class BoundaryCondition:
    """Zero-valued constraints on selected translational degrees of freedom."""

    target: GeometrySelection
    constrained_dofs: tuple[TranslationalDof, ...]

    def __post_init__(self) -> None:
        dofs = tuple(self.constrained_dofs)
        if self.target.entity is not GeometryEntity.FACE:
            raise ValueError("current V1 boundary conditions must target a geometry face")
        if not dofs:
            raise ValueError("boundary condition must constrain at least one DOF")
        if any(not isinstance(dof, TranslationalDof) for dof in dofs):
            raise TypeError("boundary-condition DOFs must be TranslationalDof values")
        if len(set(dofs)) != len(dofs):
            raise ValueError("boundary condition contains duplicate DOFs")
        object.__setattr__(self, "constrained_dofs", dofs)


@dataclass(frozen=True)
class ForceLoad:
    """Resultant force intent as positive magnitude in N plus a unit direction."""

    target: GeometrySelection
    magnitude_n: float
    direction: tuple[float, float, float]

    def __post_init__(self) -> None:
        _positive_finite(self.magnitude_n, "force magnitude")
        if self.target.entity is not GeometryEntity.FACE:
            raise ValueError("current V1 force loads must target a geometry face")
        direction = tuple(self.direction)
        if len(direction) != 3 or any(not math.isfinite(value) for value in direction):
            raise ValueError("force direction must contain three finite components")
        norm = math.sqrt(sum(value**2 for value in direction))
        if norm == 0.0:
            raise ValueError("force direction must be nonzero")
        object.__setattr__(self, "direction", tuple(value / norm for value in direction))

    @property
    def vector_n(self) -> tuple[float, float, float]:
        return tuple(self.magnitude_n * component for component in self.direction)


@dataclass(frozen=True)
class PressureLoad:
    """Pressure magnitude in Pa with an explicit selected-face normal direction."""

    target: GeometrySelection
    magnitude_pa: float
    normal_direction: SurfaceNormalDirection

    def __post_init__(self) -> None:
        _positive_finite(self.magnitude_pa, "pressure magnitude")
        if self.target.entity is not GeometryEntity.FACE:
            raise ValueError("pressure loads must target a geometry face")
        if not isinstance(self.normal_direction, SurfaceNormalDirection):
            raise TypeError("pressure normal direction must be explicit")


EngineeringLoad: TypeAlias = ForceLoad | PressureLoad


@dataclass(frozen=True)
class MeshConfig:
    """Resolved reproducible meshing configuration for the current solid path."""

    element_type: MeshElementType
    characteristic_size_m: float
    mesher_identifier: str
    mesher_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.element_type, MeshElementType):
            raise TypeError("mesh element type must be a MeshElementType")
        _positive_finite(self.characteristic_size_m, "characteristic mesh size")
        object.__setattr__(
            self, "mesher_identifier", _required_text(self.mesher_identifier, "mesher identifier")
        )
        object.__setattr__(
            self, "mesher_version", _required_text(self.mesher_version, "mesher version")
        )

    @property
    def element_order(self) -> int:
        return self.element_type.order


@dataclass(frozen=True)
class SolverConfig:
    """Resolved CalculiX-compatible linear-static solution configuration."""

    solver_identifier: str
    solver_version: str
    analysis_type: AnalysisType
    small_deformation: bool
    output_requests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "solver_identifier", _required_text(self.solver_identifier, "solver identifier")
        )
        object.__setattr__(
            self, "solver_version", _required_text(self.solver_version, "solver version")
        )
        if self.analysis_type is not AnalysisType.LINEAR_STATIC:
            raise ValueError("only linear-static analysis is currently supported")
        if self.small_deformation is not True:
            raise ValueError("current V1 solver configuration requires small deformation")
        requests = tuple(_required_text(value, "output request") for value in self.output_requests)
        if len(set(requests)) != len(requests):
            raise ValueError("solver output requests must be unique")
        object.__setattr__(self, "output_requests", requests)


@dataclass(frozen=True)
class AnalysisDefinition:
    """Immutable engineering definition suitable for an executed analysis snapshot.

    Mutable draft application state is intentionally outside this object. Any
    engineering edit after execution requires constructing a new definition and,
    eventually, a new Analysis record.
    """

    model_version: ModelVersionReference
    material: MaterialSnapshot
    loads: tuple[EngineeringLoad, ...]
    boundary_conditions: tuple[BoundaryCondition, ...]
    mesh: MeshConfig
    solver: SolverConfig
    required_factor_of_safety: float | None = None

    def __post_init__(self) -> None:
        loads = tuple(self.loads)
        boundary_conditions = tuple(self.boundary_conditions)
        if not isinstance(self.model_version, ModelVersionReference):
            raise TypeError("analysis model version must be a ModelVersionReference")
        if not isinstance(self.material, MaterialSnapshot):
            raise TypeError("analysis material must be a MaterialSnapshot")
        if not isinstance(self.mesh, MeshConfig):
            raise TypeError("analysis mesh must be a MeshConfig")
        if not isinstance(self.solver, SolverConfig):
            raise TypeError("analysis solver must be a SolverConfig")
        if not loads:
            raise ValueError("analysis definition requires at least one load")
        if not boundary_conditions:
            raise ValueError("analysis definition requires at least one boundary condition")
        if any(not isinstance(load, (ForceLoad, PressureLoad)) for load in loads):
            raise TypeError("analysis loads contain an unsupported domain object")
        if any(not isinstance(item, BoundaryCondition) for item in boundary_conditions):
            raise TypeError("analysis boundary conditions contain an unsupported domain object")
        if self.required_factor_of_safety is not None:
            _positive_finite(self.required_factor_of_safety, "required factor of safety")
        object.__setattr__(self, "loads", loads)
        object.__setattr__(self, "boundary_conditions", boundary_conditions)


def analysis_definition_to_dict(definition: AnalysisDefinition) -> dict:
    """Return a JSON-compatible, explicitly unit-labelled domain snapshot."""
    loads = []
    for load in definition.loads:
        common = {
            "target": {
                "region_name": load.target.region_name,
                "entity": load.target.entity.value,
            }
        }
        if isinstance(load, ForceLoad):
            loads.append(
                {
                    "type": "force",
                    "magnitude_n": load.magnitude_n,
                    "unit_direction": list(load.direction),
                    "vector_n": list(load.vector_n),
                    **common,
                }
            )
        else:
            loads.append(
                {
                    "type": "pressure",
                    "magnitude_pa": load.magnitude_pa,
                    "normal_direction": load.normal_direction.value,
                    **common,
                }
            )
    source = None
    if definition.material.source is not None:
        source = {
            "reference": definition.material.source.reference,
            "revision": definition.material.source.revision,
        }
    return {
        "unit_system": "SI",
        "model_version_reference": definition.model_version.value,
        "material_snapshot": {
            "name": definition.material.name,
            "youngs_modulus_pa": definition.material.youngs_modulus_pa,
            "poissons_ratio": definition.material.poissons_ratio,
            "density_kg_per_m3": definition.material.density_kg_per_m3,
            "yield_strength_pa": definition.material.yield_strength_pa,
            "source": source,
        },
        "loads": loads,
        "boundary_conditions": [
            {
                "target": {
                    "region_name": condition.target.region_name,
                    "entity": condition.target.entity.value,
                },
                "constrained_dofs": [dof.value for dof in condition.constrained_dofs],
            }
            for condition in definition.boundary_conditions
        ],
        "mesh_config": {
            "element_type": definition.mesh.element_type.value,
            "element_order": definition.mesh.element_order,
            "characteristic_size_m": definition.mesh.characteristic_size_m,
            "mesher_identifier": definition.mesh.mesher_identifier,
            "mesher_version": definition.mesh.mesher_version,
        },
        "solver_config": {
            "solver_identifier": definition.solver.solver_identifier,
            "solver_version": definition.solver.solver_version,
            "analysis_type": definition.solver.analysis_type.value,
            "small_deformation": definition.solver.small_deformation,
            "output_requests": list(definition.solver.output_requests),
        },
        "required_factor_of_safety": definition.required_factor_of_safety,
    }
