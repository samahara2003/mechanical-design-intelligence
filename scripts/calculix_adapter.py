"""Concrete domain-to-CalculiX translation proven by the axial benchmark.

This module owns CalculiX syntax and DOF numbering. Geometry selection and
finite-element surface integration occur before values reach this boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from engineering_domain import (
    AnalysisDefinition,
    AnalysisType,
    BoundaryCondition,
    ForceLoad,
    GeometrySelection,
    MaterialSnapshot,
    MeshElementType,
    TranslationalDof,
)


class CalculixAdapterError(RuntimeError):
    """Raised when the concrete adapter cannot represent resolved domain intent."""


CALCULIX_TRANSLATIONAL_DOF = {
    TranslationalDof.UX: 1,
    TranslationalDof.UY: 2,
    TranslationalDof.UZ: 3,
}


def calculix_dof(dof: TranslationalDof) -> int:
    if not isinstance(dof, TranslationalDof):
        raise CalculixAdapterError(f"Unsupported CalculiX translational DOF: {dof!r}")
    try:
        return CALCULIX_TRANSLATIONAL_DOF[dof]
    except (KeyError, TypeError) as error:
        raise CalculixAdapterError(f"Unsupported CalculiX translational DOF: {dof!r}") from error


def wrapped_ids(values: tuple[int, ...] | list[int], width: int = 16) -> list[str]:
    return [
        ", ".join(str(value) for value in values[index : index + width])
        for index in range(0, len(values), width)
    ]


@dataclass(frozen=True)
class CalculixMaterial:
    name: str
    youngs_modulus_pa: float
    poissons_ratio: float

    def lines(self) -> tuple[str, ...]:
        return (
            f"*MATERIAL, NAME={self.name}",
            "*ELASTIC",
            f"{self.youngs_modulus_pa:.16g}, {self.poissons_ratio}",
        )


def translate_material(material: MaterialSnapshot) -> CalculixMaterial:
    """Translate only the elastic properties used by the current static deck."""
    if not isinstance(material, MaterialSnapshot):
        raise CalculixAdapterError("CalculiX material translation requires a MaterialSnapshot")
    return CalculixMaterial(
        name=material.name,
        youngs_modulus_pa=material.youngs_modulus_pa,
        poissons_ratio=material.poissons_ratio,
    )


@dataclass(frozen=True)
class CalculixBoundary:
    node_set_name: str
    node_ids: tuple[int, ...]
    dof_ranges: tuple[tuple[int, int], ...]

    def node_set_lines(self) -> tuple[str, ...]:
        return (f"*NSET, NSET={self.node_set_name}", *wrapped_ids(self.node_ids))

    def constraint_lines(self) -> tuple[str, ...]:
        return tuple(
            f"{self.node_set_name}, {first}, {last}, 0" for first, last in self.dof_ranges
        )


def _contiguous_ranges(values: list[int]) -> tuple[tuple[int, int], ...]:
    if not values:
        return ()
    ranges = []
    first = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append((first, previous))
        first = previous = value
    ranges.append((first, previous))
    return tuple(ranges)


def translate_boundary_condition(
    condition: BoundaryCondition,
    resolved_selection: GeometrySelection,
    resolved_node_ids: list[int] | tuple[int, ...],
    node_set_name: str,
) -> CalculixBoundary:
    if not isinstance(condition, BoundaryCondition):
        raise CalculixAdapterError("CalculiX boundary translation requires a BoundaryCondition")
    if condition.target != resolved_selection:
        raise CalculixAdapterError("resolved node selection does not match boundary-condition target")
    node_ids = tuple(sorted(set(resolved_node_ids)))
    if not node_ids or any(not isinstance(node, int) or node <= 0 for node in node_ids):
        raise CalculixAdapterError("resolved CalculiX boundary nodes must be positive integer IDs")
    if not node_set_name or not node_set_name.strip():
        raise CalculixAdapterError("CalculiX node-set name must be nonempty")
    dofs = sorted(calculix_dof(dof) for dof in condition.constrained_dofs)
    return CalculixBoundary(node_set_name.strip(), node_ids, _contiguous_ranges(dofs))


@dataclass(frozen=True)
class CalculixConcentratedLoad:
    node_id: int
    dof: int
    value_n: float

    def line(self) -> str:
        return f"{self.node_id}, {self.dof}, {self.value_n:.16g}"


def translate_nodal_force_representation(
    force: ForceLoad,
    resolved_selection: GeometrySelection,
    nodal_forces_n: dict[int, tuple[float, float, float]],
) -> tuple[CalculixConcentratedLoad, ...]:
    """Translate already-integrated nodal vectors into CalculiX *CLOAD rows."""
    if not isinstance(force, ForceLoad):
        raise CalculixAdapterError("CalculiX nodal-force translation requires a ForceLoad")
    if force.target != resolved_selection:
        raise CalculixAdapterError("resolved face selection does not match force target")
    if not nodal_forces_n:
        raise CalculixAdapterError("resolved force representation contains no nodal forces")
    resultant = tuple(
        sum(vector[axis] for vector in nodal_forces_n.values()) for axis in range(3)
    )
    if not all(
        math.isclose(resultant[axis], force.vector_n[axis], rel_tol=1.0e-12, abs_tol=1.0e-8)
        for axis in range(3)
    ):
        raise CalculixAdapterError(
            f"resolved nodal resultant {resultant} does not match domain force {force.vector_n}"
        )
    rows = []
    dofs = (TranslationalDof.UX, TranslationalDof.UY, TranslationalDof.UZ)
    for node, vector in sorted(nodal_forces_n.items()):
        if not isinstance(node, int) or node <= 0 or len(vector) != 3:
            raise CalculixAdapterError("nodal force representation is invalid")
        for axis, value in enumerate(vector):
            if not math.isfinite(value):
                raise CalculixAdapterError("nodal force components must be finite")
            if value != 0.0:
                rows.append(CalculixConcentratedLoad(node, calculix_dof(dofs[axis]), value))
    return tuple(rows)


def validate_axial_analysis_definition(analysis: AnalysisDefinition) -> None:
    """Reject domain definitions outside the adapter's single proven capability."""
    if analysis.solver.solver_identifier.casefold() != "calculix":
        raise CalculixAdapterError("this adapter supports only CalculiX")
    if analysis.solver.analysis_type is not AnalysisType.LINEAR_STATIC:
        raise CalculixAdapterError("this adapter supports only linear-static analysis")
    if analysis.solver.small_deformation is not True:
        raise CalculixAdapterError("this adapter supports only small-deformation analysis")
    if analysis.mesh.element_type is not MeshElementType.C3D10:
        raise CalculixAdapterError("axial adapter currently supports only C3D10")
    if len(analysis.loads) != 1 or not isinstance(analysis.loads[0], ForceLoad):
        raise CalculixAdapterError("axial adapter requires exactly one ForceLoad")
    if len(analysis.boundary_conditions) != 1:
        raise CalculixAdapterError("axial adapter requires exactly one boundary condition")
    required_outputs = {"displacement", "reaction_force", "integration_point_stress"}
    if set(analysis.solver.output_requests) != required_outputs:
        raise CalculixAdapterError(
            "axial adapter requires displacement, reaction_force, and integration_point_stress outputs"
        )


def render_axial_linear_static_deck(
    analysis: AnalysisDefinition,
    nodes: dict[int, tuple[float, float, float]],
    volume_elements: list[dict],
    boundary: CalculixBoundary,
    load_node_ids: list[int],
    mapped_load_surface: list[tuple[int, str]],
    concentrated_loads: tuple[CalculixConcentratedLoad, ...],
) -> str:
    """Render only the established axial C3D10 deck, preserving its exact layout."""
    validate_axial_analysis_definition(analysis)
    material = translate_material(analysis.material)
    lines = [
        "*HEADING",
        "Mechanical Design Intelligence - axial bar C3D10 verification",
        "*NODE, NSET=ALLNODES",
    ]
    lines.extend(
        f"{node}, {x:.16g}, {y:.16g}, {z:.16g}"
        for node, (x, y, z) in sorted(nodes.items())
    )
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=AXIAL_BAR")
    lines.extend(
        f"{element['id']}, " + ", ".join(map(str, element["nodes"]))
        for element in volume_elements
    )
    lines.extend(boundary.node_set_lines())
    lines.append("*NSET, NSET=AXIAL_LOAD_NODES")
    lines.extend(wrapped_ids(sorted(set(load_node_ids))))
    face_sets: dict[str, list[int]] = {}
    for element_id, face_label in mapped_load_surface:
        face_sets.setdefault(face_label, []).append(element_id)
    for face_label, element_ids in sorted(face_sets.items()):
        lines.append(f"*ELSET, ELSET=AXIAL_LOAD_{face_label}")
        lines.extend(wrapped_ids(sorted(element_ids)))
    lines.append("*SURFACE, NAME=AXIAL_LOAD_FACE, TYPE=ELEMENT")
    lines.extend(f"AXIAL_LOAD_{label}, {label}" for label in sorted(face_sets))
    lines.extend(material.lines())
    lines.extend(
        [
            f"*SOLID SECTION, ELSET=AXIAL_BAR, MATERIAL={material.name}",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            *boundary.constraint_lines(),
            "*CLOAD",
        ]
    )
    lines.extend(load.line() for load in concentrated_loads)
    lines.extend(
        [
            "*NODE FILE",
            "U, RF",
            "*EL FILE",
            "S",
            "*EL PRINT, ELSET=AXIAL_BAR",
            "S",
            "*NODE PRINT, NSET=ALLNODES",
            "U",
            f"*NODE PRINT, NSET={boundary.node_set_name}, TOTALS=YES",
            "RF",
            "*END STEP",
            "",
        ]
    )
    return "\n".join(lines)
