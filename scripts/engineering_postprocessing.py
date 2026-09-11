"""Small solver-neutral deterministic operations on numerical result values."""

from __future__ import annotations

import math

from numerical_results import StressTensor, Vector3


def vector_magnitude(vector: Vector3) -> float:
    """Return the Euclidean magnitude in the vector's owning SI unit."""
    return math.sqrt(vector.x**2 + vector.y**2 + vector.z**2)


def von_mises_stress_pa(stress: StressTensor) -> float:
    """Return 3D von Mises stress in Pa from a symmetric Cauchy tensor."""
    sxx, syy, szz, sxy, sxz, syz = stress.as_tuple()
    return math.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + sxz**2)
    )
