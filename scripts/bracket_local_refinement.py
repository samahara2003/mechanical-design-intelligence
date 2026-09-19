"""Run the controlled bracket root-fillet local mesh-refinement experiment."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from bracket_verification import BracketVerificationError, analyze_level
from calculix_results import CalculixResultParseError
from engineering_stress import PhysicalCoordinateBoxRegion
from feature_stress_refinement import (
    FeatureMaximumLocalizationPolicy,
    FeatureStressBehaviorPolicy,
    FeatureStressRefinementLevel,
    LocalMeshRefinementDefinition,
    assess_critical_stress_impact,
    build_feature_stress_refinement_study,
    critical_stress_impact_to_dict,
    feature_stress_refinement_study_to_dict,
)
from generate_bracket_mesh import (
    BASE_WIDTH_M,
    MOUNTING_HOLE_CENTRES_M,
    MOUNTING_HOLE_RADIUS_M,
    ROOT_FILLET_RADIUS_M,
    ROOT_LOCAL_SIZING_MAX_M,
    ROOT_LOCAL_SIZING_MIN_M,
    ROOT_LOCAL_TRANSITION_THICKNESS_M,
    UPRIGHT_X_MIN_M,
)
from numerical_results import Vector3
from stress_spatial_profile import (
    StressPathDefinition,
    StressProfileComparisonPolicy,
    assess_stress_profile_impact,
    build_stress_spatial_profile_study,
    stress_profile_assessment_impact_to_dict,
    stress_spatial_profile_study_to_dict,
)


FAR_FIELD_SIZE_M = 0.006
LOCAL_LEVELS = (
    ("local_reference", 0.006),
    ("local_medium", 0.004),
    ("local_fine", 0.004 / 1.5),
)

ROOT_FEATURE_REGION = PhysicalCoordinateBoxRegion(
    region_id="root_fillet_neighborhood",
    region_version="1",
    name="Root-transition and 15 mm fillet neighborhood",
    minimum_m=Vector3(0.110, 0.0, 0.008),
    maximum_m=Vector3(0.135, BASE_WIDTH_M, 0.032),
    feature_context=(
        "physical box enclosing the full root-fillet X/Z projection plus adjacent "
        "base and upright material across the bracket width"
    ),
    constraint_relationship="separated_from_immediate_constrained_surface",
    constraint_reference="both mounting-hole cylindrical faces fixed in UX/UY/UZ",
    minimum_separation_from_constraint_m=(
        0.110 - (MOUNTING_HOLE_CENTRES_M[0][0] + MOUNTING_HOLE_RADIUS_M)
    ),
)

LOCALIZATION_POLICY = FeatureMaximumLocalizationPolicy(
    policy_name="controlled_bracket_root_feature_localization",
    policy_version="1",
    maximum_adjacent_movement_m=0.006,
)

BEHAVIOR_POLICY = FeatureStressBehaviorPolicy(
    policy_name="controlled_bracket_root_feature_refinement_behavior",
    policy_version="1",
    stable_relative_mean_change=0.05,
    sensitive_relative_maximum_change=0.10,
)

ROOT_BASE_TANGENCY_PROFILE = StressPathDefinition(
    path_id="root_fillet_base_tangency_inward_profile",
    path_version="1",
    reference_feature="root_fillet_base_tangency_line",
    origin_m=Vector3(UPRIGHT_X_MIN_M - ROOT_FILLET_RADIUS_M, 0.0, 0.012),
    direction=Vector3(-1.0, 0.0, 0.0),
    extent_m=0.012,
    transverse_z_half_width_m=0.002,
    y_interval_m=(0.0, BASE_WIDTH_M),
    bin_edges_m=(0.0, 0.003, 0.006, 0.009, 0.012),
    physical_rationale=(
        "Profiles stress inward through the base from the CAD line where the 15 mm "
        "circular fillet is tangent to the base top; the full width is retained."
    ),
)

ROOT_PROFILE_POLICY = StressProfileComparisonPolicy(
    policy_name="controlled_bracket_root_base_tangency_profile",
    policy_version="1",
    normalized_mean_absolute_tolerance=0.10,
    high_stress_fraction_of_profile_maximum=0.80,
    high_zone_width_change_tolerance_m=0.0015,
)


def root_maximum_geometry_relationship(location_m: Vector3) -> dict:
    """Relate a located maximum to the exact quarter-circle CAD construction."""
    circle_center_x = UPRIGHT_X_MIN_M
    circle_center_z = 0.012
    tangency_x = UPRIGHT_X_MIN_M - ROOT_FILLET_RADIUS_M
    tangency_z = 0.012
    radial_distance = math.hypot(
        location_m.x - circle_center_x, location_m.z - circle_center_z
    )
    base_side = location_m.x < tangency_x and location_m.z < tangency_z
    return {
        "location_m": list(location_m.as_tuple()),
        "cad_fillet_cross_section": {
            "circle_center_xz_m": [circle_center_x, circle_center_z],
            "radius_m": ROOT_FILLET_RADIUS_M,
            "arc_quadrant": "x<=center_x_and_z>=center_z",
            "base_tangency_line_xz_m": [tangency_x, tangency_z],
            "line_extent_y_m": [0.0, BASE_WIDTH_M],
        },
        "inside_base_material": (
            0.0 <= location_m.x <= 0.140
            and 0.0 <= location_m.y <= BASE_WIDTH_M
            and 0.0 <= location_m.z <= 0.012
        ),
        "inside_fillet_quarter_projection": (
            tangency_x <= location_m.x <= circle_center_x
            and circle_center_z <= location_m.z <= circle_center_z + ROOT_FILLET_RADIUS_M
            and radial_distance <= ROOT_FILLET_RADIUS_M
        ),
        "base_side_of_fillet_tangency": base_side,
        "distance_to_base_tangency_line_m": math.hypot(
            location_m.x - tangency_x, location_m.z - tangency_z
        ),
        "radial_offset_from_full_circle_m": radial_distance - ROOT_FILLET_RADIUS_M,
        "closest_permitted_fillet_boundary_feature": (
            "base_side_tangency_line" if base_side else "not_classified_by_v1"
        ),
        "semantics": "geometric relationship only; no causal or singularity claim",
    }


def refinement_definition(level_id: str, local_size_m: float) -> LocalMeshRefinementDefinition:
    return LocalMeshRefinementDefinition(
        definition_version="1",
        level_id=level_id,
        feature_id="root_transition_fillet",
        feature_version="1",
        sizing_region_minimum_m=Vector3(*ROOT_LOCAL_SIZING_MIN_M),
        sizing_region_maximum_m=Vector3(*ROOT_LOCAL_SIZING_MAX_M),
        far_field_size_m=FAR_FIELD_SIZE_M,
        local_target_size_m=local_size_m,
        transition_thickness_m=ROOT_LOCAL_TRANSITION_THICKNESS_M,
    )


def _load_current_blockers(global_artifact_path: Path) -> tuple[str, ...]:
    artifact = json.loads(global_artifact_path.read_text(encoding="utf-8"))
    assessment = artifact["critical_stress_assessment"]
    if assessment["status"] != "not_established":
        raise BracketVerificationError(
            "Local study expects the current controlled assessment to be not_established"
        )
    return tuple(item["code"] for item in assessment["blocking_reasons"])


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    bracket_root = repository / "artifacts" / "bracket"
    root = bracket_root / "local_root_refinement"
    step_path = bracket_root / "bracket.step"
    started = time.perf_counter()
    try:
        if not (bracket_root / "bracket_validation.json").is_file():
            raise BracketVerificationError(
                "Run the current global bracket verification before the local study"
            )
        executions = tuple(
            analyze_level(
                repository,
                root,
                level_id,
                FAR_FIELD_SIZE_M,
                step_path,
                root_local_size_m=local_size,
                feature_stress_region=ROOT_FEATURE_REGION,
                stress_path=ROOT_BASE_TANGENCY_PROFILE,
            )
            for level_id, local_size in LOCAL_LEVELS
        )
        definitions = tuple(item[0]["analysis_definition"] for item in executions)
        if any(item != definitions[0] for item in definitions[1:]):
            raise BracketVerificationError(
                "Local levels differ in geometry, material, load, BC, far-field mesh, or solver definition"
            )
        levels = tuple(
            FeatureStressRefinementLevel(
                refinement_definition(level_id, local_size),
                execution[5],
                execution[2].stress.global_raw_max_von_mises,
            )
            for (level_id, local_size), execution in zip(LOCAL_LEVELS, executions)
        )
        if any(item.evidence is None for item in levels):
            raise BracketVerificationError("Feature stress evidence is missing")
        study = build_feature_stress_refinement_study(
            "controlled_bracket_root_feature_local_refinement",
            "1",
            levels,
            LOCALIZATION_POLICY,
            BEHAVIOR_POLICY,
        )
        profiles = tuple(item[6] for item in executions)
        if any(item is None for item in profiles):
            raise BracketVerificationError("Root stress profile evidence is missing")
        profile_study = build_stress_spatial_profile_study(
            "controlled_bracket_root_base_tangency_profile",
            "1",
            profiles,
            ROOT_PROFILE_POLICY,
        )
        blockers = _load_current_blockers(bracket_root / "bracket_validation.json")
        impact = assess_critical_stress_impact(study, blockers)
        profile_impact = assess_stress_profile_impact(profile_study, blockers)
        artifact = {
            "status": "controlled root-feature local refinement completed",
            "case_id": "mounting-bracket-root-local-refinement-v1",
            "scope": "targeted local feature refinement; existing global study unchanged",
            "unit_system": "SI",
            "geometry_context": {
                "upright_x_min_m": UPRIGHT_X_MIN_M,
                "root_fillet_radius_m": ROOT_FILLET_RADIUS_M,
                "same_step_geometry_as_global_study": True,
                "feature_maximum_relationships": [
                    root_maximum_geometry_relationship(
                        item.evidence.maximum_location_m
                    )
                    for item in levels
                ],
            },
            "feature_stress_refinement_study": feature_stress_refinement_study_to_dict(study),
            "stress_spatial_profile_study": stress_spatial_profile_study_to_dict(
                profile_study
            ),
            "critical_stress_assessment_impact": critical_stress_impact_to_dict(impact),
            "stress_profile_assessment_impact": (
                stress_profile_assessment_impact_to_dict(profile_impact)
            ),
            "execution_records": [item[0] for item in executions],
            "runtime_seconds": time.perf_counter() - started,
        }
        root.mkdir(parents=True, exist_ok=True)
        artifact_path = root / "root_feature_refinement.json"
        artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    except (
        OSError,
        ValueError,
        KeyError,
        BracketVerificationError,
        CalculixResultParseError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({**artifact, "validation_artifact": str(artifact_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
