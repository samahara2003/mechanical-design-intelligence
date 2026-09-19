"""Run and review three mesh levels for the realistic bracket integration case."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

from analysis_provenance import (
    analysis_provenance_to_dict,
    build_analysis_provenance,
)
from analysis_results import (
    AnalysisResult,
    ResolvedAnalysisContext,
    analysis_result_to_dict,
    build_analysis_result,
)
from bracket_assessment import build_bracket_assessment
from bracket_definition import (
    BASELINE_MESH_SIZE_M,
    BRACKET_FORCE,
    BRACKET_LOAD_FACE,
    COARSE_MESH_SIZE_M,
    FINER_MESH_SIZE_M,
    bracket_analysis_definition,
)
from bracket_quantities import (
    BRACKET_CRITICAL_STRESS_MODEL_ASSUMPTIONS,
    BRACKET_CRITICAL_STRESS_POLICY,
    BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY,
    BRACKET_DISCRETIZATION_ESTIMATE_POLICY,
    BRACKET_MESH_STUDY_ID,
    BRACKET_MESH_STUDY_VERSION,
    BRACKET_STRESS_SPATIAL_BANDS,
    BRACKET_STRESS_SPATIAL_POLICY,
    LOAD_PAD_AVERAGE_UX,
    LOAD_PAD_UX_REFINEMENT_POLICY,
    LOWER_UPRIGHT_WEB_STRESS_REGION,
)
from critical_stress_assessment import (
    build_critical_stress_assessment,
    critical_stress_assessment_to_dict,
)
from calculix_results import CalculixResultParseError, parse_calculix_dat
from engineering_assessment import engineering_assessment_to_dict
from engineering_domain import analysis_definition_to_dict
from engineering_quantities import (
    QuantityEvaluation,
    assess_raw_stress_discretization_eligibility,
    asymptotic_consistency_evidence_to_dict,
    build_asymptotic_consistency_evidence,
    build_mesh_convergence_study,
    build_raw_stress_mesh_trend,
    discretization_error_estimate_to_dict,
    estimate_mesh_discretization_error,
    evaluate_regional_displacement,
    mesh_convergence_study_to_dict,
    mesh_refinement_comparison_to_dict,
    quantity_evaluation_to_dict,
    raw_stress_diagnostic_to_dict,
    raw_stress_mesh_trend_to_dict,
)
from engineering_stress import (
    FeatureRelationshipEvidence,
    LocatedStressFeatureEvidence,
    PhysicalCoordinateBoxRegion,
    RegionalStressEvidence,
    SpatialStressBandEvidence,
    build_regional_stress_mesh_study,
    build_spatial_stress_band_studies,
    build_stress_spatial_diagnostic,
    evaluate_regional_stress,
    evaluate_spatial_stress_bands,
    regional_stress_evidence_to_dict,
    regional_stress_mesh_study_to_dict,
    spatial_stress_band_study_to_dict,
    stress_spatial_diagnostic_to_dict,
)
from generate_bracket_mesh import (
    BASE_LENGTH_M,
    BASE_THICKNESS_M,
    BASE_WIDTH_M,
    LOAD_FACE_AREA_M2,
    LOAD_PAD_X_MAX_M,
    LOAD_PAD_Y_MAX_M,
    LOAD_PAD_Y_MIN_M,
    LOAD_PAD_Z_MAX_M,
    LOAD_PAD_Z_MIN_M,
    MOUNTING_HOLE_CENTRES_M,
    MOUNTING_HOLE_RADIUS_M,
    ROOT_FILLET_RADIUS_M,
    UPRIGHT_HEIGHT_M,
    UPRIGHT_X_MIN_M,
)
from numerical_results import IntegrationPointStress, NumericalResult, Vector3
from run_bracket_solve import resolve_mesh_elements
from run_cantilever_solve import as_calculix_c3d10, read_msh
from surface_load_mapping import map_uniform_force_to_c3d10_faces, triangle_area
from stress_spatial_profile import (
    StressPathDefinition,
    StressSpatialProfile,
    evaluate_stress_spatial_profile,
)


LEVELS = (
    ("coarse", COARSE_MESH_SIZE_M),
    ("baseline", BASELINE_MESH_SIZE_M),
    ("fine", FINER_MESH_SIZE_M),
)
GAUSS_LOW = 0.138196601125011
GAUSS_HIGH = 0.585410196624968
GAUSS_NATURAL_COORDINATES = (
    (GAUSS_LOW, GAUSS_LOW, GAUSS_LOW),
    (GAUSS_HIGH, GAUSS_LOW, GAUSS_LOW),
    (GAUSS_LOW, GAUSS_HIGH, GAUSS_LOW),
    (GAUSS_LOW, GAUSS_LOW, GAUSS_HIGH),
)


class BracketVerificationError(RuntimeError):
    pass


def run_json(command: list[str], cwd: Path) -> tuple[dict, float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=300
    )
    if completed.returncode != 0:
        raise BracketVerificationError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout), time.perf_counter() - started
    except json.JSONDecodeError as error:
        raise BracketVerificationError(f"Command did not return JSON: {command}") from error


def c3d10_shape_weights(natural: tuple[float, float, float]) -> tuple[float, ...]:
    xi, eta, zeta = natural
    bary = (1.0 - xi - eta - zeta, xi, eta, zeta)
    a, b, c, d = bary
    return (
        a*(2*a-1), b*(2*b-1), c*(2*c-1), d*(2*d-1),
        4*a*b, 4*b*c, 4*c*a, 4*a*d, 4*b*d, 4*c*d,
    )


def interpolate_coordinates(
    element_nodes: list[int],
    nodes: dict[int, tuple[float, float, float]],
    natural: tuple[float, float, float],
) -> tuple[float, float, float]:
    weights = c3d10_shape_weights(natural)
    return tuple(
        sum(weights[i] * nodes[node][axis] for i, node in enumerate(element_nodes))
        for axis in range(3)
    )


def area_weighted_surface_displacement(
    faces: list[dict],
    nodes: dict[int, tuple[float, float, float]],
    displacements: dict[int, tuple[float, float, float]],
) -> tuple[tuple[float, float, float], float]:
    """Integrate the quadratic surface displacement, then divide by face area."""
    integral = [0.0, 0.0, 0.0]
    area_sum = 0.0
    for face in faces:
        if len(face["nodes"]) != 6:
            raise ValueError("surface displacement QoI requires six-node triangles")
        if any(node not in displacements for node in face["nodes"]):
            raise ValueError("surface displacement QoI is missing nodal data")
        area = triangle_area(*(nodes[node] for node in face["nodes"][:3]))
        area_sum += area
        for node in face["nodes"][3:]:
            for axis in range(3):
                integral[axis] += displacements[node][axis] * area / 3.0
    if area_sum <= 0.0:
        raise ValueError("surface displacement QoI requires positive area")
    return tuple(value / area_sum for value in integral), area_sum


def vector_moment(
    forces: dict[int, tuple[float, float, float]],
    nodes: dict[int, tuple[float, float, float]],
) -> tuple[float, float, float]:
    moment = [0.0, 0.0, 0.0]
    for node, force in forces.items():
        x, y, z = nodes[node]
        fx, fy, fz = force
        moment[0] += y*fz - z*fy
        moment[1] += z*fx - x*fz
        moment[2] += x*fy - y*fx
    return tuple(moment)


def classify_stress_location(location: tuple[float, float, float]) -> str:
    x, y, z = location
    if any(
        math.hypot(x-cx, y-cy) <= MOUNTING_HOLE_RADIUS_M + 0.012 and z <= 0.020
        for cx, cy in MOUNTING_HOLE_CENTRES_M
    ):
        return "mounting-hole vicinity"
    if x >= UPRIGHT_X_MIN_M - ROOT_FILLET_RADIUS_M - 0.006 and z <= 0.040:
        return "upright root / fillet vicinity"
    if x >= 0.138 and LOAD_PAD_Z_MIN_M - 0.006 <= z <= LOAD_PAD_Z_MAX_M + 0.006:
        return "load-pad vicinity"
    return "bracket body away from predeclared feature vicinities"


def fixed_bore_surface_distance_m(location: Vector3) -> float:
    """Exact distance to the finite cylindrical surfaces used by the fixed BC."""
    axial_excess = max(0.0, location.z - BASE_THICKNESS_M, -location.z)
    return min(
        math.hypot(
            abs(math.hypot(location.x - cx, location.y - cy) - MOUNTING_HOLE_RADIUS_M),
            axial_excess,
        )
        for cx, cy in MOUNTING_HOLE_CENTRES_M
    )


def regional_maximum_feature_evidence(
    evidence: RegionalStressEvidence,
) -> LocatedStressFeatureEvidence:
    location = evidence.maximum_location_m
    fixed_distance = fixed_bore_surface_distance_m(location)
    fixed_relationship = (
        "within_controlled_near_distance"
        if fixed_distance <= BRACKET_STRESS_SPATIAL_POLICY.near_feature_distance_m
        else "separated_beyond_controlled_near_distance"
    )
    fillet_top_z = BASE_THICKNESS_M + ROOT_FILLET_RADIUS_M
    return LocatedStressFeatureEvidence(
        evidence.mesh_identity,
        evidence.mesh.characteristic_size_m,
        evidence.maximum_von_mises_pa,
        location,
        (
            FeatureRelationshipEvidence(
                "simplified_fixed_mounting_bores",
                fixed_relationship,
                "exact_distance_to_finite_cylindrical_fixed_surface",
                fixed_distance,
                "minimum Euclidean distance to either finite mounting-hole cylinder",
            ),
            FeatureRelationshipEvidence(
                "root_fillet",
                "above_fillet_top_coordinate",
                "vertical_coordinate_gap_above_fillet_top",
                location.z - fillet_top_z,
                "global-Z coordinate difference; not a shortest-distance calculation",
            ),
            FeatureRelationshipEvidence(
                "upright_web",
                "inside_declared_lower_upright_web_region",
                None,
                None,
                "closed coordinate-box membership",
            ),
            FeatureRelationshipEvidence(
                "load_pad",
                "below_load_pad_bottom_coordinate",
                "vertical_coordinate_gap_below_load_pad_bottom",
                LOAD_PAD_Z_MIN_M - location.z,
                "global-Z coordinate difference; not a shortest-distance calculation",
            ),
        ),
    )


def analyze_level(
    repository: Path,
    root: Path,
    name: str,
    mesh_size: float,
    step_path: Path,
    *,
    root_local_size_m: float | None = None,
    feature_stress_region: PhysicalCoordinateBoxRegion | None = None,
    stress_path: StressPathDefinition | None = None,
) -> tuple[
    dict,
    QuantityEvaluation,
    AnalysisResult,
    RegionalStressEvidence,
    tuple[SpatialStressBandEvidence, ...],
    RegionalStressEvidence | None,
    StressSpatialProfile | None,
]:
    scripts = repository / "scripts"
    output_dir = root / name
    mesh_command = [
        sys.executable,
        str(scripts / "generate_bracket_mesh.py"),
        "--output-dir",
        str(output_dir),
        "--step-path",
        str(step_path),
        "--mesh-size",
        str(mesh_size),
    ]
    if root_local_size_m is not None:
        mesh_command.extend(("--root-local-size", str(root_local_size_m)))
    mesh, mesh_runtime = run_json(mesh_command, repository)
    solve, solve_runtime = run_json(
        [sys.executable, str(scripts / "run_bracket_solve.py"), "--output-dir", str(output_dir)],
        repository,
    )
    if mesh["gmsh_warnings"] or solve["sanity"]["solver_warnings"]:
        raise BracketVerificationError("Unexpected Gmsh or CalculiX warning in bracket execution")
    mesh_path = output_dir / "bracket.msh"
    dat_path = output_dir / "bracket_static.dat"
    nodes, elements = read_msh(mesh_path)
    volumes = {
        element["id"]: as_calculix_c3d10(element)
        for element in elements if element["type"] == 11 and element["physical_tag"] == 1
    }
    load_faces = resolve_mesh_elements(BRACKET_LOAD_FACE, 9, elements)
    numerical = parse_calculix_dat(dat_path)
    displacements = numerical.displacement_tuples_by_node()
    qoi, qoi_area = area_weighted_surface_displacement(load_faces, nodes, displacements)
    nodal_loads, _, _ = map_uniform_force_to_c3d10_faces(
        BRACKET_FORCE, load_faces, nodes, LOAD_FACE_AREA_M2
    )
    reaction_forces = {item.node_id: item.reaction_n.as_tuple() for item in numerical.reactions}
    applied_moment = vector_moment(nodal_loads, nodes)
    reaction_moment = vector_moment(reaction_forces, nodes)
    ip_locations = {}
    for stress in numerical.integration_point_stresses:
        element = volumes.get(stress.element_id)
        if element is None or stress.integration_point not in range(1, 5):
            raise BracketVerificationError("Raw stress cannot be mapped to a C3D10 point")
        ip_locations[(stress.element_id, stress.integration_point)] = Vector3(
            *interpolate_coordinates(
                element["nodes"], nodes, GAUSS_NATURAL_COORDINATES[stress.integration_point-1]
            )
        )
    located_numerical = NumericalResult(
        numerical.displacements,
        numerical.reactions,
        tuple(
            IntegrationPointStress(
                stress.element_id,
                stress.integration_point,
                stress.stress_pa,
                ip_locations[(stress.element_id, stress.integration_point)],
            )
            for stress in numerical.integration_point_stresses
        ),
        numerical.reaction_resultant_n,
    )
    definition = bracket_analysis_definition(mesh["gmsh_version"], solve["ccx_version"], mesh_size)
    result = build_analysis_result(
        definition,
        located_numerical,
        ResolvedAnalysisContext(
            mesh["mesh"]["node_count"],
            mesh["mesh"]["volume_element_count"],
            Vector3(*solve["model"]["integrated_resultant_n"]),
        ),
        node_locations_m={node: Vector3(*point) for node, point in nodes.items()},
        integration_point_locations_m=ip_locations,
    )
    provenance = build_analysis_provenance(
        definition,
        cad_step_path=step_path,
        mesh_path=mesh_path,
        solver_input_path=output_dir / "bracket_static.inp",
        solver_dat_path=dat_path,
        solver_frd_path=output_dir / "bracket_static.frd",
        gmsh_version=mesh["gmsh_version"],
        calculix_version=solve["ccx_version"],
    )
    quantity_evaluation = evaluate_regional_displacement(
        LOAD_PAD_AVERAGE_UX,
        definition,
        result,
        numerical,
        resolved_region_identity="gmsh_physical_surface:load_pad",
        mesh_identity=f"sha256:{provenance.mesh.sha256}",
        faces=load_faces,
        node_coordinates_m=nodes,
    )
    regional_stress = evaluate_regional_stress(
        LOWER_UPRIGHT_WEB_STRESS_REGION,
        located_numerical,
        mesh_identity=f"sha256:{provenance.mesh.sha256}",
        mesh=result.mesh,
    )
    spatial_band_evidence = evaluate_spatial_stress_bands(
        BRACKET_STRESS_SPATIAL_BANDS,
        located_numerical,
        mesh_identity=f"sha256:{provenance.mesh.sha256}",
        mesh=result.mesh,
    )
    feature_stress = (
        None
        if feature_stress_region is None
        else evaluate_regional_stress(
            feature_stress_region,
            located_numerical,
            mesh_identity=f"sha256:{provenance.mesh.sha256}",
            mesh=result.mesh,
        )
    )
    if stress_path is not None and feature_stress is None:
        raise BracketVerificationError(
            "Stress-path evaluation requires feature-region stress evidence"
        )
    stress_profile = (
        None
        if stress_path is None
        else evaluate_stress_spatial_profile(
            stress_path,
            located_numerical,
            mesh_identity=f"sha256:{provenance.mesh.sha256}",
            feature_peak_location_m=feature_stress.maximum_location_m,
        )
    )
    if not math.isclose(quantity_evaluation.value, qoi[0], rel_tol=0.0, abs_tol=1e-15):
        raise BracketVerificationError(
            "reusable QoI evaluation differs from existing integration"
        )
    peak = result.stress.global_raw_max_von_mises
    peak_location = peak.location_m.as_tuple() if peak.location_m else None
    record = {
        "level": name,
        "analysis_definition": analysis_definition_to_dict(definition),
        "analysis_result": analysis_result_to_dict(result),
        "engineering_assessment": engineering_assessment_to_dict(
            build_bracket_assessment(result)
        ),
        "quantity_evaluation": quantity_evaluation_to_dict(quantity_evaluation),
        "regional_stress_evidence": regional_stress_evidence_to_dict(regional_stress),
        "analysis_provenance": analysis_provenance_to_dict(provenance),
        "mesh_quality": mesh["quality"],
        "mesh_sizing": mesh["mesh_sizing"],
        "gmsh_warnings": mesh["gmsh_warnings"],
        "solver_warnings": solve["sanity"]["solver_warnings"],
        "load_pad_area_average_displacement_qoi": {
            "predeclared_quantity": "area-average global displacement over named load_pad face",
            "value_m": list(qoi), "integrated_area_m2": qoi_area,
            "integration": "exact integral of the C3D10 quadratic face interpolation for straight triangles",
        },
        "force_and_moment_equilibrium": {
            "applied_force_n": solve["model"]["integrated_resultant_n"],
            "reaction_force_n": solve["sanity"]["fixed_reaction_n"],
            "applied_moment_about_origin_n_m": list(applied_moment),
            "reaction_moment_about_origin_n_m": list(reaction_moment),
            "moment_imbalance_n_m": [applied_moment[i] + reaction_moment[i] for i in range(3)],
        },
        "peak_stress_feature_classification": classify_stress_location(peak_location),
        "fixed_node_maximum_displacement_m": solve["sanity"]["maximum_fixed_node_displacement_m"],
        "runtimes_seconds": {"mesh": mesh_runtime, "solve": solve_runtime},
    }
    return (
        record,
        quantity_evaluation,
        result,
        regional_stress,
        spatial_band_evidence,
        feature_stress,
        stress_profile,
    )


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    root = repository / "artifacts" / "bracket"
    step_path = root / "bracket.step"
    started = time.perf_counter()
    try:
        step_path.unlink(missing_ok=True)
        analyzed = [
            analyze_level(repository, root, name, size, step_path)
            for name, size in LEVELS
        ]
        levels = [item[0] for item in analyzed]
        evaluations = [item[1] for item in analyzed]
        results = [item[2] for item in analyzed]
        regional_stress_evaluations = [item[3] for item in analyzed]
        spatial_band_evaluations = [item[4] for item in analyzed]
        mesh_study = build_mesh_convergence_study(
            BRACKET_MESH_STUDY_ID,
            BRACKET_MESH_STUDY_VERSION,
            evaluations,
            LOAD_PAD_UX_REFINEMENT_POLICY,
        )
        refinement = mesh_study.adjacent_comparisons[1]
        raw_stress_study = build_raw_stress_mesh_trend(results, evaluations)
        regional_stress_study = build_regional_stress_mesh_study(
            regional_stress_evaluations
        )
        spatial_band_studies = build_spatial_stress_band_studies(
            spatial_band_evaluations
        )
        stress_spatial_diagnostic = build_stress_spatial_diagnostic(
            tuple(
                regional_maximum_feature_evidence(item)
                for item in regional_stress_evaluations
            ),
            spatial_band_studies,
            BRACKET_STRESS_SPATIAL_POLICY,
        )
        critical_stress_assessment = build_critical_stress_assessment(
            raw_stress_study,
            regional_stress_study,
            spatial_band_studies,
            stress_spatial_diagnostic,
            BRACKET_CRITICAL_STRESS_POLICY,
            BRACKET_CRITICAL_STRESS_MODEL_ASSUMPTIONS,
        )
        displacement_error_estimate = estimate_mesh_discretization_error(
            mesh_study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        raw_stress_error_eligibility = assess_raw_stress_discretization_eligibility(
            raw_stress_study, BRACKET_DISCRETIZATION_ESTIMATE_POLICY
        )
        displacement_asymptotic_consistency = build_asymptotic_consistency_evidence(
            displacement_error_estimate, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
        )
        raw_stress_asymptotic_consistency = build_asymptotic_consistency_evidence(
            raw_stress_error_eligibility, BRACKET_ASYMPTOTIC_CONSISTENCY_POLICY
        )
        stress_diagnostic = raw_stress_study.adjacent_changes[1]
        base_qoi = refinement.reference.value
        fine_qoi = refinement.refined.value
        base_stress = stress_diagnostic.reference.von_mises_pa
        fine_stress = stress_diagnostic.refined.von_mises_pa
        sensitivity = {
            "displacement_qoi": {
                "quantity": "area-average UX over load_pad face",
                "baseline_m": base_qoi, "finer_m": fine_qoi,
                "absolute_change_m": refinement.absolute_change,
                "relative_change": refinement.relative_change,
            },
            "global_raw_von_mises": {
                "baseline_pa": base_stress, "finer_pa": fine_stress,
                "absolute_change_pa": stress_diagnostic.absolute_change_pa,
                "relative_change": stress_diagnostic.relative_change,
                "interpretation": "diagnostic sensitivity only; no critical-stress or convergence policy",
            },
            "equilibrium_residual_magnitude_n": [
                level["analysis_result"]["equilibrium_evidence"]["residual_magnitude_n"]
                for level in levels
            ],
        }
        artifact = {
            "status": "realistic bracket integration case completed",
            "case_id": "mounting-bracket-c3d10-v1",
            "case_role": "engineering integration and mesh-sensitivity evidence; not an analytical verification benchmark",
            "unit_system": "SI",
            "geometry_m": {
                "base": {"length": BASE_LENGTH_M, "width": BASE_WIDTH_M, "thickness": BASE_THICKNESS_M},
                "upright": {"x_min": UPRIGHT_X_MIN_M, "height": UPRIGHT_HEIGHT_M, "thickness": BASE_LENGTH_M-UPRIGHT_X_MIN_M},
                "root_fillet_radius": ROOT_FILLET_RADIUS_M,
                "mounting_holes": {"radius": MOUNTING_HOLE_RADIUS_M, "centres_xy": MOUNTING_HOLE_CENTRES_M},
                "load_pad": {"x_max": LOAD_PAD_X_MAX_M, "y_range": [LOAD_PAD_Y_MIN_M, LOAD_PAD_Y_MAX_M], "z_range": [LOAD_PAD_Z_MIN_M, LOAD_PAD_Z_MAX_M], "area": LOAD_FACE_AREA_M2},
            },
            "scenario": {
                "material": "linear isotropic steel, E=200 GPa, nu=0.30",
                "load": "2500 N resultant in global +X, uniformly distributed on load_pad",
                "boundary_condition": "both mounting-hole cylindrical faces fixed in UX/UY/UZ",
                "selection": "named STEP faces re-identified after import by dimensional bounding boxes and exact-count checks",
            },
            "levels": levels,
            "mesh_convergence_study": mesh_convergence_study_to_dict(mesh_study),
            "displacement_discretization_error_estimate": (
                discretization_error_estimate_to_dict(displacement_error_estimate)
            ),
            "displacement_asymptotic_consistency": (
                asymptotic_consistency_evidence_to_dict(
                    displacement_asymptotic_consistency
                )
            ),
            "raw_stress_mesh_trend": raw_stress_mesh_trend_to_dict(
                raw_stress_study
            ),
            "regional_stress_mesh_study": regional_stress_mesh_study_to_dict(
                regional_stress_study
            ),
            "spatial_stress_band_studies": [
                spatial_stress_band_study_to_dict(item)
                for item in spatial_band_studies
            ],
            "stress_spatial_diagnostic": stress_spatial_diagnostic_to_dict(
                stress_spatial_diagnostic
            ),
            "critical_stress_assessment": critical_stress_assessment_to_dict(
                critical_stress_assessment
            ),
            "raw_stress_discretization_eligibility": (
                discretization_error_estimate_to_dict(raw_stress_error_eligibility)
            ),
            "raw_stress_asymptotic_consistency": (
                asymptotic_consistency_evidence_to_dict(
                    raw_stress_asymptotic_consistency
                )
            ),
            "mesh_refinement_comparison": mesh_refinement_comparison_to_dict(refinement),
            "raw_stress_refinement_diagnostic": raw_stress_diagnostic_to_dict(
                stress_diagnostic
            ),
            "mesh_sensitivity": sensitivity,
            "engineering_review": {
                "deformation": "positive load-pad UX and coupled downward bending are mechanically plausible for the eccentric +X load",
                "rigid_body_motion": "absent: constrained-hole nodes report zero displacement and a finite equilibrating reaction",
                "stress": "global raw integration-point peaks are feature diagnostics, not failure or critical-design stresses",
                "small_deformation_indicator": max(level["analysis_result"]["displacement_summary"]["global_maximum_magnitude_m"] for level in levels)/UPRIGHT_HEIGHT_M,
                "limitations": [
                    "fully fixed mounting bores idealize bolts, washer contact, preload, friction, and base compliance",
                    "uniform vector traction on the load-pad face idealizes the attached component",
                    "global raw stress peaks can remain mesh-sensitive near idealized constraints and geometric features",
                    "three meshes are trend evidence, not demonstrated convergence or mesh independence",
                    "no analytical whole-part solution, experimental validation, FoS, or pass/fail claim is made",
                ],
            },
            "runtime_seconds": time.perf_counter()-started,
        }
        artifact_path = root / "bracket_validation.json"
        artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, BracketVerificationError, CalculixResultParseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({**artifact, "validation_artifact": str(artifact_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
