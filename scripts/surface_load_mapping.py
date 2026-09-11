"""Solver-neutral finite-element mapping of surface force intent to nodal forces."""

from __future__ import annotations

import math

from engineering_domain import ForceLoad


def triangle_area(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> float:
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * math.sqrt(sum(component**2 for component in cross))


def consistent_quadratic_face_loads(
    faces: list[dict],
    nodes: dict[int, tuple[float, float, float]],
    traction_pa: tuple[float, float, float],
) -> tuple[dict[int, tuple[float, float, float]], float]:
    """Integrate constant traction with six-node triangle shape functions."""
    nodal: dict[int, list[float]] = {}
    total_area = 0.0
    for face in faces:
        if len(face["nodes"]) != 6:
            raise ValueError("surface force mapping requires six-node quadratic triangles")
        area = triangle_area(*(nodes[node] for node in face["nodes"][:3]))
        total_area += area
        # For a constant traction, quadratic corner shape functions integrate
        # to zero and each midside shape function integrates to area/3.
        for node in face["nodes"][3:]:
            vector = nodal.setdefault(node, [0.0, 0.0, 0.0])
            for axis in range(3):
                vector[axis] += traction_pa[axis] * area / 3.0
    return {node: tuple(vector) for node, vector in nodal.items()}, total_area


def map_uniform_force_to_c3d10_faces(
    force: ForceLoad,
    faces: list[dict],
    nodes: dict[int, tuple[float, float, float]],
    expected_area_m2: float,
) -> tuple[dict[int, tuple[float, float, float]], float, tuple[float, float, float]]:
    """Map a resultant force to consistent C3D10 nodal forces via uniform traction."""
    if not isinstance(force, ForceLoad):
        raise TypeError("uniform force mapping requires a ForceLoad")
    if not math.isfinite(expected_area_m2) or expected_area_m2 <= 0.0:
        raise ValueError("expected loaded area must be positive and finite")
    traction = tuple(component / expected_area_m2 for component in force.vector_n)
    nodal_forces, integrated_area = consistent_quadratic_face_loads(faces, nodes, traction)
    return nodal_forces, integrated_area, traction
