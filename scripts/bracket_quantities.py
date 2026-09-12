"""Predeclared quantity and two-mesh policy for the controlled bracket."""

from bracket_definition import BRACKET_LOAD_FACE
from engineering_quantities import (
    MeshRefinementComparisonPolicy,
    QuantityAggregation,
    QuantityComponent,
    QuantityField,
    QuantityOfInterest,
)


LOAD_PAD_AVERAGE_UX = QuantityOfInterest(
    quantity_id="load_pad_average_ux",
    name="Load-pad average global X displacement",
    target=BRACKET_LOAD_FACE,
    field=QuantityField.DISPLACEMENT,
    component=QuantityComponent.UX,
    aggregation=QuantityAggregation.AVERAGE,
    units="m",
)

LOAD_PAD_UX_REFINEMENT_POLICY = MeshRefinementComparisonPolicy(
    policy_name="controlled_bracket_load_pad_average_ux",
    policy_version="1",
    relative_change_tolerance=0.01,
    minimum_reference_magnitude=1.0e-12,
)
