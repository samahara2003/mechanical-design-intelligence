"""Raw integration-point stress profiles tied to one physical CAD feature path."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from engineering_postprocessing import von_mises_stress_pa
from numerical_results import NumericalResult, Vector3


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty")
    return value.strip()


def _code(value: str, label: str) -> str:
    value = _text(value, label)
    if re.fullmatch(r"[a-z][a-z0-9_]*", value) is None:
        raise ValueError(f"{label} must use lower snake case")
    return value


@dataclass(frozen=True)
class StressPathDefinition:
    path_id: str
    path_version: str
    reference_feature: str
    origin_m: Vector3
    direction: Vector3
    extent_m: float
    transverse_z_half_width_m: float
    y_interval_m: tuple[float, float]
    bin_edges_m: tuple[float, ...]
    physical_rationale: str
    selection_method: str = field(
        default="located_raw_integration_points_in_physical_path_bins", init=False
    )
    interpolation: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path_id", _code(self.path_id, "path ID"))
        object.__setattr__(self, "path_version", _text(self.path_version, "path version"))
        object.__setattr__(
            self, "reference_feature", _code(self.reference_feature, "reference feature")
        )
        if not isinstance(self.origin_m, Vector3) or not isinstance(self.direction, Vector3):
            raise TypeError("path origin and direction must be Vector3 values")
        norm = math.sqrt(sum(value * value for value in self.direction.as_tuple()))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1.0e-15):
            raise ValueError("path direction must be a unit vector")
        if self.direction != Vector3(-1.0, 0.0, 0.0):
            raise ValueError("V1 supports only the controlled base-side fillet path")
        if not math.isfinite(self.extent_m) or self.extent_m <= 0.0:
            raise ValueError("path extent must be positive and finite")
        if not math.isfinite(self.transverse_z_half_width_m) or self.transverse_z_half_width_m <= 0.0:
            raise ValueError("path transverse half-width must be positive and finite")
        y_interval = tuple(self.y_interval_m)
        if len(y_interval) != 2 or not y_interval[0] < y_interval[1]:
            raise ValueError("path Y interval must be increasing")
        edges = tuple(self.bin_edges_m)
        if len(edges) < 2 or edges[0] != 0.0 or edges[-1] != self.extent_m:
            raise ValueError("path bins must cover zero through the declared extent")
        if any(not math.isfinite(value) for value in edges) or any(
            left >= right for left, right in zip(edges, edges[1:])
        ):
            raise ValueError("path bin edges must be finite and strictly increasing")
        object.__setattr__(self, "y_interval_m", y_interval)
        object.__setattr__(self, "bin_edges_m", edges)
        object.__setattr__(
            self, "physical_rationale", _text(self.physical_rationale, "physical rationale")
        )

    def coordinates(self, location_m: Vector3) -> tuple[float, float]:
        delta = Vector3(
            location_m.x - self.origin_m.x,
            location_m.y - self.origin_m.y,
            location_m.z - self.origin_m.z,
        )
        distance = sum(
            value * direction
            for value, direction in zip(delta.as_tuple(), self.direction.as_tuple())
        )
        return distance, abs(location_m.z - self.origin_m.z)

    def bin_index(self, location_m: Vector3) -> int | None:
        distance, transverse = self.coordinates(location_m)
        if (
            not self.y_interval_m[0] <= location_m.y <= self.y_interval_m[1]
            or transverse > self.transverse_z_half_width_m
            or distance < 0.0
            or distance > self.extent_m
        ):
            return None
        for index, (lower, upper) in enumerate(zip(self.bin_edges_m, self.bin_edges_m[1:])):
            if lower <= distance < upper or (
                index == len(self.bin_edges_m) - 2 and distance == upper
            ):
                return index
        return None


@dataclass(frozen=True)
class StressGradientBinEvidence:
    lower_distance_m: float
    upper_distance_m: float
    upper_bound_inclusive: bool
    sample_count: int
    minimum_von_mises_pa: float | None
    arithmetic_mean_von_mises_pa: float | None
    maximum_von_mises_pa: float | None
    maximum_location_m: Vector3 | None

    def __post_init__(self) -> None:
        if not 0.0 <= self.lower_distance_m < self.upper_distance_m:
            raise ValueError("stress-profile bin bounds must increase")
        if not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError("stress-profile sample count must be nonnegative")
        statistics = (
            self.minimum_von_mises_pa,
            self.arithmetic_mean_von_mises_pa,
            self.maximum_von_mises_pa,
        )
        if self.sample_count == 0:
            if any(value is not None for value in (*statistics, self.maximum_location_m)):
                raise ValueError("empty profile bins must carry explicit null statistics")
        else:
            if any(value is None or not math.isfinite(value) or value < 0.0 for value in statistics):
                raise ValueError("populated profile bins require nonnegative finite statistics")
            if not statistics[0] <= statistics[1] <= statistics[2]:
                raise ValueError("profile statistics must be minimum/mean/maximum ordered")
            if not isinstance(self.maximum_location_m, Vector3):
                raise TypeError("populated profile bin requires maximum XYZ location")


@dataclass(frozen=True)
class DescriptiveStressGradient:
    from_bin_index: int
    to_bin_index: int
    mean_gradient_pa_per_m: float | None
    semantics: str = field(
        default="finite_difference_of_adjacent_bin_means_descriptive_only", init=False
    )

    def __post_init__(self) -> None:
        if self.to_bin_index != self.from_bin_index + 1:
            raise ValueError("descriptive gradients must join adjacent bins")
        if self.mean_gradient_pa_per_m is not None and not math.isfinite(
            self.mean_gradient_pa_per_m
        ):
            raise ValueError("descriptive stress gradient must be finite when available")


@dataclass(frozen=True)
class PeakToProfileAssociation:
    associated: bool
    bin_index: int | None
    path_distance_m: float
    transverse_distance_m: float
    location_m: Vector3

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(value) or value < 0.0
            for value in (self.path_distance_m, self.transverse_distance_m)
        ):
            raise ValueError("peak-to-profile distances must be finite and nonnegative")
        if self.associated != (self.bin_index is not None):
            raise ValueError("peak association and bin identity must agree")


@dataclass(frozen=True)
class StressSpatialProfile:
    profile_version: str
    mesh_identity: str
    path: StressPathDefinition
    bins: tuple[StressGradientBinEvidence, ...]
    gradients: tuple[DescriptiveStressGradient, ...]
    feature_peak: PeakToProfileAssociation
    stress_representation: str = field(
        default="raw_integration_point_cauchy_stress_derived_von_mises", init=False
    )
    scope: str = field(default="physical_path_binned_diagnostic_evidence_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_version", _text(self.profile_version, "profile version"))
        object.__setattr__(self, "mesh_identity", _text(self.mesh_identity, "mesh identity"))
        if not isinstance(self.path, StressPathDefinition):
            raise TypeError("stress profile requires a StressPathDefinition")
        if not isinstance(self.feature_peak, PeakToProfileAssociation):
            raise TypeError("stress profile requires peak-to-profile evidence")
        bins = tuple(self.bins)
        gradients = tuple(self.gradients)
        if len(bins) != len(self.path.bin_edges_m) - 1:
            raise ValueError("profile must preserve every declared path bin")
        if len(gradients) != len(bins) - 1:
            raise ValueError("profile must retain every adjacent descriptive gradient")
        for index, item in enumerate(bins):
            if (
                item.lower_distance_m != self.path.bin_edges_m[index]
                or item.upper_distance_m != self.path.bin_edges_m[index + 1]
            ):
                raise ValueError("profile bin bounds must match the physical path definition")
        object.__setattr__(self, "bins", bins)
        object.__setattr__(self, "gradients", gradients)


@dataclass(frozen=True)
class StressProfileComparisonPolicy:
    policy_name: str
    policy_version: str
    normalized_mean_absolute_tolerance: float
    high_stress_fraction_of_profile_maximum: float
    high_zone_width_change_tolerance_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _text(self.policy_name, "policy name"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy version"))
        if not 0.0 <= self.normalized_mean_absolute_tolerance <= 1.0:
            raise ValueError("normalized profile tolerance must be between zero and one")
        if not 0.0 < self.high_stress_fraction_of_profile_maximum <= 1.0:
            raise ValueError("high-stress fraction must be in (0, 1]")
        if not math.isfinite(self.high_zone_width_change_tolerance_m) or self.high_zone_width_change_tolerance_m < 0.0:
            raise ValueError("high-zone width tolerance must be finite and nonnegative")


@dataclass(frozen=True)
class StressSpatialProfileStudy:
    study_id: str
    study_version: str
    profiles: tuple[StressSpatialProfile, ...]
    policy: StressProfileComparisonPolicy
    adjacent_normalized_mean_max_differences: tuple[float | None, ...]
    shape_status: str
    high_stress_zone_widths_m: tuple[float | None, ...]
    high_stress_zone_behavior: str
    peak_bin_indices: tuple[int | None, ...]
    peak_association_status: str
    scope: str = field(default="three_level_physical_stress_profile_comparison_only", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "study_id", _code(self.study_id, "study ID"))
        object.__setattr__(self, "study_version", _text(self.study_version, "study version"))
        profiles = tuple(self.profiles)
        if len(profiles) != 3 or any(item.path != profiles[0].path for item in profiles[1:]):
            raise ValueError("profile study requires three meshes and one physical path")
        if not isinstance(self.policy, StressProfileComparisonPolicy):
            raise TypeError("profile study requires an explicit comparison policy")
        differences = tuple(self.adjacent_normalized_mean_max_differences)
        widths = tuple(self.high_stress_zone_widths_m)
        peak_bins = tuple(self.peak_bin_indices)
        if len(differences) != 2 or len(widths) != 3 or len(peak_bins) != 3:
            raise ValueError("profile study comparison fields must match three ordered levels")
        for value in differences + widths:
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ValueError("profile comparison values must be nonnegative and finite")
        if self.shape_status not in {"reproducible_within_policy", "not_reproducible_within_policy", "indeterminate"}:
            raise ValueError("unsupported stress-profile shape status")
        if self.high_stress_zone_behavior not in {"narrowed", "broadened", "remains_similar", "indeterminate"}:
            raise ValueError("unsupported high-stress-zone behavior")
        if self.peak_association_status not in {"same_profile_portion", "different_profile_portions", "not_associated"}:
            raise ValueError("unsupported peak association status")
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "adjacent_normalized_mean_max_differences", differences)
        object.__setattr__(self, "high_stress_zone_widths_m", widths)
        object.__setattr__(self, "peak_bin_indices", peak_bins)


@dataclass(frozen=True)
class StressProfileAssessmentImpact:
    impact_version: str
    prior_status: str
    resulting_status: str
    improves_spatial_characterization: bool
    resolved_blocker_codes: tuple[str, ...]
    still_present_blocker_codes: tuple[str, ...]
    next_evidence_requirement_codes: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "impact_version", _text(self.impact_version, "impact version"))
        if self.prior_status != "not_established" or self.resulting_status != "not_established":
            raise ValueError("stress profile evidence cannot promote V1 critical-stress status")
        groups = tuple(
            tuple(values)
            for values in (
                self.resolved_blocker_codes,
                self.still_present_blocker_codes,
                self.next_evidence_requirement_codes,
            )
        )
        for values in groups:
            if any(re.fullmatch(r"[a-z][a-z0-9_]*", value) is None for value in values):
                raise ValueError("assessment-impact codes must use lower snake case")
        object.__setattr__(self, "resolved_blocker_codes", groups[0])
        object.__setattr__(self, "still_present_blocker_codes", groups[1])
        object.__setattr__(self, "next_evidence_requirement_codes", groups[2])
        object.__setattr__(self, "explanation", _text(self.explanation, "impact explanation"))


def evaluate_stress_spatial_profile(
    path: StressPathDefinition,
    numerical: NumericalResult,
    *,
    mesh_identity: str,
    feature_peak_location_m: Vector3,
) -> StressSpatialProfile:
    selected: list[list[tuple[float, int, int, Vector3]]] = [
        [] for _ in range(len(path.bin_edges_m) - 1)
    ]
    for stress in numerical.integration_point_stresses:
        if stress.location_m is None:
            raise ValueError("stress profile requires every raw stress location")
        index = path.bin_index(stress.location_m)
        if index is not None:
            selected[index].append(
                (
                    von_mises_stress_pa(stress.stress_pa),
                    stress.element_id,
                    stress.integration_point,
                    stress.location_m,
                )
            )
    bins = []
    for index, samples in enumerate(selected):
        lower, upper = path.bin_edges_m[index : index + 2]
        if not samples:
            bins.append(StressGradientBinEvidence(lower, upper, index == len(selected) - 1, 0, None, None, None, None))
            continue
        ordered = sorted(samples, key=lambda item: (-item[0], item[1], item[2]))
        values = tuple(item[0] for item in samples)
        bins.append(
            StressGradientBinEvidence(
                lower,
                upper,
                index == len(selected) - 1,
                len(samples),
                min(values),
                sum(values) / len(values),
                ordered[0][0],
                ordered[0][3],
            )
        )
    gradients = []
    for index, (left, right) in enumerate(zip(bins, bins[1:])):
        center_distance = (
            (right.lower_distance_m + right.upper_distance_m)
            - (left.lower_distance_m + left.upper_distance_m)
        ) / 2.0
        gradient = (
            None
            if left.arithmetic_mean_von_mises_pa is None
            or right.arithmetic_mean_von_mises_pa is None
            else (
                right.arithmetic_mean_von_mises_pa
                - left.arithmetic_mean_von_mises_pa
            )
            / center_distance
        )
        gradients.append(DescriptiveStressGradient(index, index + 1, gradient))
    peak_distance, peak_transverse = path.coordinates(feature_peak_location_m)
    peak_bin = path.bin_index(feature_peak_location_m)
    return StressSpatialProfile(
        "1",
        mesh_identity,
        path,
        tuple(bins),
        tuple(gradients),
        PeakToProfileAssociation(
            peak_bin is not None,
            peak_bin,
            abs(peak_distance),
            peak_transverse,
            feature_peak_location_m,
        ),
    )


def build_stress_spatial_profile_study(
    study_id: str,
    study_version: str,
    profiles: Sequence[StressSpatialProfile],
    policy: StressProfileComparisonPolicy,
) -> StressSpatialProfileStudy:
    ordered = tuple(profiles)
    if len(ordered) != 3:
        raise ValueError("stress profile study requires exactly three profiles")

    def normalized_means(profile: StressSpatialProfile) -> tuple[float, ...] | None:
        means = tuple(item.arithmetic_mean_von_mises_pa for item in profile.bins)
        if any(value is None for value in means):
            return None
        total = sum(means)
        return None if total == 0.0 else tuple(value / total for value in means)

    normalized = tuple(normalized_means(item) for item in ordered)
    differences = tuple(
        None
        if left is None or right is None
        else max(abs(a - b) for a, b in zip(left, right))
        for left, right in zip(normalized, normalized[1:])
    )
    shape_status = (
        "indeterminate"
        if any(value is None for value in differences)
        else (
            "reproducible_within_policy"
            if all(value <= policy.normalized_mean_absolute_tolerance for value in differences)
            else "not_reproducible_within_policy"
        )
    )

    def high_width(profile: StressSpatialProfile) -> float | None:
        maxima = tuple(item.maximum_von_mises_pa for item in profile.bins)
        available = tuple(value for value in maxima if value is not None)
        if not available:
            return None
        threshold = policy.high_stress_fraction_of_profile_maximum * max(available)
        return sum(
            item.upper_distance_m - item.lower_distance_m
            for item in profile.bins
            if item.maximum_von_mises_pa is not None
            and item.maximum_von_mises_pa >= threshold
        )

    widths = tuple(high_width(item) for item in ordered)
    if any(value is None for value in widths):
        width_behavior = "indeterminate"
    else:
        change = widths[-1] - widths[0]
        width_behavior = (
            "broadened"
            if change > policy.high_zone_width_change_tolerance_m
            else (
                "narrowed"
                if change < -policy.high_zone_width_change_tolerance_m
                else "remains_similar"
            )
        )
    peak_bins = tuple(item.feature_peak.bin_index for item in ordered)
    if any(index is None for index in peak_bins):
        peak_status = "not_associated"
    elif len(set(peak_bins)) == 1:
        peak_status = "same_profile_portion"
    else:
        peak_status = "different_profile_portions"
    return StressSpatialProfileStudy(
        _code(study_id, "study ID"),
        _text(study_version, "study version"),
        ordered,
        policy,
        differences,
        shape_status,
        widths,
        width_behavior,
        peak_bins,
        peak_status,
    )


def assess_stress_profile_impact(
    study: StressSpatialProfileStudy,
    prior_blocker_codes: Sequence[str],
) -> StressProfileAssessmentImpact:
    improves = study.peak_association_status == "same_profile_portion"
    requirements = ["explicit_profile_to_stress_extraction_methodology"]
    if study.shape_status != "reproducible_within_policy":
        requirements.append("additional_profile_shape_reproducibility_evidence")
    return StressProfileAssessmentImpact(
        "1",
        "not_established",
        "not_established",
        improves,
        (),
        tuple(prior_blocker_codes),
        tuple(requirements),
        (
            "The binned raw-stress profile improves physical localization only; it does "
            "not define a design-stress extraction or resolve refinement/modeling blockers."
        ),
    )


def stress_spatial_profile_study_to_dict(study: StressSpatialProfileStudy) -> dict:
    def profile_to_dict(profile: StressSpatialProfile) -> dict:
        return {
            "profile_version": profile.profile_version,
            "mesh_identity": profile.mesh_identity,
            "bins": [
                {
                    "bin_index": index,
                    "distance_interval_m": {
                        "lower": item.lower_distance_m,
                        "upper": item.upper_distance_m,
                        "upper_bound_inclusive": item.upper_bound_inclusive,
                    },
                    "sample_count": item.sample_count,
                    "minimum_von_mises_pa": item.minimum_von_mises_pa,
                    "arithmetic_mean_von_mises_pa": item.arithmetic_mean_von_mises_pa,
                    "maximum_von_mises_pa": item.maximum_von_mises_pa,
                    "maximum_location_m": (
                        None if item.maximum_location_m is None else list(item.maximum_location_m.as_tuple())
                    ),
                }
                for index, item in enumerate(profile.bins)
            ],
            "descriptive_mean_gradients_pa_per_m": [
                {
                    "from_bin_index": item.from_bin_index,
                    "to_bin_index": item.to_bin_index,
                    "value": item.mean_gradient_pa_per_m,
                    "semantics": item.semantics,
                }
                for item in profile.gradients
            ],
            "feature_peak_association": {
                "associated": profile.feature_peak.associated,
                "bin_index": profile.feature_peak.bin_index,
                "path_distance_m": profile.feature_peak.path_distance_m,
                "transverse_distance_m": profile.feature_peak.transverse_distance_m,
                "location_m": list(profile.feature_peak.location_m.as_tuple()),
            },
        }

    path = study.profiles[0].path
    return {
        "study_id": study.study_id,
        "study_version": study.study_version,
        "scope": study.scope,
        "path_definition": {
            "path_id": path.path_id,
            "path_version": path.path_version,
            "reference_feature": path.reference_feature,
            "origin_m": list(path.origin_m.as_tuple()),
            "direction": list(path.direction.as_tuple()),
            "extent_m": path.extent_m,
            "transverse_z_half_width_m": path.transverse_z_half_width_m,
            "y_interval_m": list(path.y_interval_m),
            "bin_edges_m": list(path.bin_edges_m),
            "selection_method": path.selection_method,
            "interpolation": path.interpolation,
            "physical_rationale": path.physical_rationale,
        },
        "profiles": [profile_to_dict(item) for item in study.profiles],
        "comparison": {
            "policy": {
                "name": study.policy.policy_name,
                "version": study.policy.policy_version,
                "normalized_mean_absolute_tolerance": study.policy.normalized_mean_absolute_tolerance,
                "high_stress_fraction_of_profile_maximum": study.policy.high_stress_fraction_of_profile_maximum,
                "high_zone_width_change_tolerance_m": study.policy.high_zone_width_change_tolerance_m,
            },
            "adjacent_normalized_mean_max_differences": list(
                study.adjacent_normalized_mean_max_differences
            ),
            "shape_status": study.shape_status,
            "high_stress_zone_widths_m": list(study.high_stress_zone_widths_m),
            "high_stress_zone_behavior": study.high_stress_zone_behavior,
            "feature_peak_bin_indices": list(study.peak_bin_indices),
            "feature_peak_association_status": study.peak_association_status,
            "semantics": "descriptive profile comparison only; not convergence or design stress",
        },
    }


def stress_profile_assessment_impact_to_dict(
    impact: StressProfileAssessmentImpact,
) -> dict:
    return {
        "impact_version": impact.impact_version,
        "prior_status": impact.prior_status,
        "resulting_status": impact.resulting_status,
        "improves_spatial_characterization": impact.improves_spatial_characterization,
        "resolved_blocker_codes": list(impact.resolved_blocker_codes),
        "still_present_blocker_codes": list(impact.still_present_blocker_codes),
        "next_evidence_requirement_codes": list(impact.next_evidence_requirement_codes),
        "explanation": impact.explanation,
        "established_stress_reference": None,
    }


def stress_spatial_profile_study_to_json(study: StressSpatialProfileStudy) -> str:
    return json.dumps(
        stress_spatial_profile_study_to_dict(study),
        sort_keys=True,
        separators=(",", ":"),
    )
