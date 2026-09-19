"""Run the controlled bracket root-fillet local mesh-refinement experiment."""

from __future__ import annotations

import json
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
        blockers = _load_current_blockers(bracket_root / "bracket_validation.json")
        impact = assess_critical_stress_impact(study, blockers)
        artifact = {
            "status": "controlled root-feature local refinement completed",
            "case_id": "mounting-bracket-root-local-refinement-v1",
            "scope": "targeted local feature refinement; existing global study unchanged",
            "unit_system": "SI",
            "geometry_context": {
                "upright_x_min_m": UPRIGHT_X_MIN_M,
                "root_fillet_radius_m": ROOT_FILLET_RADIUS_M,
                "same_step_geometry_as_global_study": True,
            },
            "feature_stress_refinement_study": feature_stress_refinement_study_to_dict(study),
            "critical_stress_assessment_impact": critical_stress_impact_to_dict(impact),
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
