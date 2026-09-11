"""Immutable solver-neutral numerical result values in explicit SI units."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _finite(values: tuple[float, ...], label: str) -> None:
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{label} components must be finite")


@dataclass(frozen=True)
class Vector3:
    """Three physical components; the owning field name declares the SI unit."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        _finite((self.x, self.y, self.z), "vector")

    def as_tuple(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


@dataclass(frozen=True)
class NodalDisplacement:
    """Displacement in metres at one numerical mesh node."""

    node_id: int
    displacement_m: Vector3

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, int) or self.node_id <= 0:
            raise ValueError("displacement node ID must be a positive integer")


@dataclass(frozen=True)
class NodalReaction:
    """Reaction force in newtons at one numerical mesh node."""

    node_id: int
    reaction_n: Vector3

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, int) or self.node_id <= 0:
            raise ValueError("reaction node ID must be a positive integer")


@dataclass(frozen=True)
class StressTensor:
    """Symmetric global Cauchy stress tensor components in pascals."""

    sigma_xx_pa: float
    sigma_yy_pa: float
    sigma_zz_pa: float
    sigma_xy_pa: float
    sigma_xz_pa: float
    sigma_yz_pa: float

    def __post_init__(self) -> None:
        _finite(self.as_tuple(), "stress tensor")

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.sigma_xx_pa,
            self.sigma_yy_pa,
            self.sigma_zz_pa,
            self.sigma_xy_pa,
            self.sigma_xz_pa,
            self.sigma_yz_pa,
        )


@dataclass(frozen=True)
class IntegrationPointStress:
    """Raw integration-point stress tied to a numerical element identity."""

    element_id: int
    integration_point: int
    stress_pa: StressTensor
    location_m: Vector3 | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.element_id, int) or self.element_id <= 0:
            raise ValueError("stress element ID must be a positive integer")
        if not isinstance(self.integration_point, int) or self.integration_point <= 0:
            raise ValueError("integration-point identity must be a positive integer")


@dataclass(frozen=True)
class NumericalResult:
    """Completed solver-neutral numerical snapshot, before engineering interpretation."""

    displacements: tuple[NodalDisplacement, ...]
    reactions: tuple[NodalReaction, ...]
    integration_point_stresses: tuple[IntegrationPointStress, ...]
    reaction_resultant_n: Vector3 | None = None

    def __post_init__(self) -> None:
        displacements = tuple(sorted(tuple(self.displacements), key=lambda item: item.node_id))
        reactions = tuple(sorted(tuple(self.reactions), key=lambda item: item.node_id))
        stresses = tuple(
            sorted(
                tuple(self.integration_point_stresses),
                key=lambda item: (item.element_id, item.integration_point),
            )
        )
        if any(not isinstance(item, NodalDisplacement) for item in displacements):
            raise TypeError("numerical displacements must be NodalDisplacement values")
        if any(not isinstance(item, NodalReaction) for item in reactions):
            raise TypeError("numerical reactions must be NodalReaction values")
        if any(not isinstance(item, IntegrationPointStress) for item in stresses):
            raise TypeError("numerical stresses must be IntegrationPointStress values")
        if len({item.node_id for item in displacements}) != len(displacements):
            raise ValueError("duplicate displacement node ID")
        if len({item.node_id for item in reactions}) != len(reactions):
            raise ValueError("duplicate reaction node ID")
        stress_keys = {(item.element_id, item.integration_point) for item in stresses}
        if len(stress_keys) != len(stresses):
            raise ValueError("duplicate integration-point stress identity")
        if self.reaction_resultant_n is not None and not isinstance(
            self.reaction_resultant_n, Vector3
        ):
            raise TypeError("reaction resultant must be a Vector3")
        object.__setattr__(self, "displacements", displacements)
        object.__setattr__(self, "reactions", reactions)
        object.__setattr__(self, "integration_point_stresses", stresses)

    def displacement_tuples_by_node(self) -> dict[int, tuple[float, float, float]]:
        return {item.node_id: item.displacement_m.as_tuple() for item in self.displacements}
