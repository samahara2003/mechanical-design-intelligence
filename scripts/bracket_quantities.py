"""Predeclared quantity and two-mesh policy for the controlled bracket."""

import math

from bracket_definition import BRACKET_LOAD_FACE
from engineering_stress import (
    PhysicalCoordinateBoxRegion,
    SpatialStressBand,
    StressSpatialDiagnosticPolicy,
)
from engineering_quantities import (
    AsymptoticConsistencyPolicy,
    DiscretizationErrorEstimatePolicy,
    MeshRefinementComparisonPolicy,
    QuantityAggregation,
    QuantityComponent,
    QuantityField,
    QuantityOfInterest,
)
from generate_bracket_mesh import (
    BASE_LENGTH_M,
    BASE_THICKNESS_M,
    BASE_WIDTH_M,
    LOAD_PAD_X_MAX_M,
    MOUNTING_HOLE_CENTRES_M,
    MOUNTING_HOLE_RADIUS_M,
    ROOT_FILLET_RADIUS_M,
    UPRIGHT_X_MIN_M,
)
from numerical_results import Vector3


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

BRACKET_MESH_STUDY_ID = "controlled_bracket_load_pad_average_ux_three_level"
BRACKET_MESH_STUDY_VERSION = "1"

BRACKET_DISCRETIZATION_ESTIMATE_POLICY = DiscretizationErrorEstimatePolicy(
    policy_name="controlled_bracket_three_grid_discretization_estimate",
    policy_version="1",
    safety_factor=1.25,
    minimum_difference_magnitude=1.0e-12,
    minimum_relative_reference_magnitude=1.0e-12,
    refinement_ratio_relative_tolerance=1.0e-12,
)

BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY = AsymptoticConsistencyPolicy(
    policy_name="controlled_bracket_asymptotic_consistency",
    policy_version="1",
    formula_identity="gci_32_over_r_to_p_gci_21",
    target_ratio=1.0,
    allowable_absolute_deviation=0.01,
    minimum_denominator=1.0e-15,
)

LOWER_UPRIGHT_WEB_STRESS_REGION = PhysicalCoordinateBoxRegion(
    region_id="lower_upright_web_stress",
    region_version="1",
    name="Lower upright web above root fillet",
    minimum_m=Vector3(UPRIGHT_X_MIN_M, 0.0, 0.030),
    maximum_m=Vector3(BASE_LENGTH_M, BASE_WIDTH_M, 0.060),
    feature_context=(
        "upright load-path band above the 15 mm root fillet and below the load pad"
    ),
    constraint_relationship="separated_from_immediate_constrained_surface",
    constraint_reference="both mounting-hole cylindrical faces fixed in UX/UY/UZ",
    minimum_separation_from_constraint_m=math.hypot(
        UPRIGHT_X_MIN_M
        - (MOUNTING_HOLE_CENTRES_M[0][0] + MOUNTING_HOLE_RADIUS_M),
        0.030 - BASE_THICKNESS_M,
    ),
)

BRACKET_STRESS_SPATIAL_BANDS = (
    SpatialStressBand(
        "support_side_base",
        "1",
        "Support-side base",
        0.0,
        0.070,
        False,
        "base volume containing both simplified fixed mounting-hole bores",
    ),
    SpatialStressBand(
        "base_transfer",
        "1",
        "Base transfer span",
        0.070,
        UPRIGHT_X_MIN_M - ROOT_FILLET_RADIUS_M,
        False,
        "base span between the support-side band and root-fillet X projection",
    ),
    SpatialStressBand(
        "root_transition",
        "1",
        "Root-fillet X projection",
        UPRIGHT_X_MIN_M - ROOT_FILLET_RADIUS_M,
        UPRIGHT_X_MIN_M,
        False,
        "global-X projection occupied by the 15 mm root transition",
    ),
    SpatialStressBand(
        "upright_load_path",
        "1",
        "Upright and load-pad span",
        UPRIGHT_X_MIN_M,
        LOAD_PAD_X_MAX_M,
        True,
        "upright web and protruding load-pad global-X span",
    ),
)

BRACKET_STRESS_SPATIAL_POLICY = StressSpatialDiagnosticPolicy(
    policy_name="controlled_bracket_stress_spatial_diagnostic",
    policy_version="1",
    near_feature_distance_m=0.015,
    localized_maximum_distance_m=0.010,
    material_movement_distance_m=0.020,
    stable_relative_mean_change=0.05,
    sensitive_relative_maximum_change=0.10,
)
