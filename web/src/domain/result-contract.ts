export type Vector3Tuple = readonly [number, number, number];

export interface AnalysisResultSummary {
  readonly unit_system: "SI";
  readonly model_version_reference: string;
  readonly mesh_summary: {
    readonly node_count: number;
    readonly element_count: number;
    readonly element_type: string;
    readonly characteristic_size_m: number;
  };
  readonly displacement_summary: {
    readonly global_maximum_magnitude_m: number;
    readonly node_id: number;
    readonly vector_m: Vector3Tuple;
    readonly location_m: Vector3Tuple | null;
  };
  readonly equilibrium_evidence: {
    readonly applied_resultant_n: Vector3Tuple;
    readonly reaction_resultant_n: Vector3Tuple;
    readonly imbalance_n: Vector3Tuple;
    readonly residual_magnitude_n: number;
    readonly relative_imbalance: number | null;
  };
  readonly stress_summary: Readonly<Record<string, unknown>>;
  readonly warnings: readonly Readonly<{ code: string; message: string }>[];
}

export interface AnalysisProvenanceSummary {
  readonly model_version_reference: string;
  readonly analysis_definition_sha256: string;
  readonly artifacts: Readonly<Record<string, unknown>>;
  readonly tools: Readonly<Record<string, unknown>>;
  readonly postprocessor: Readonly<{ identifier: string; version: string }>;
}

export interface EngineeringAssessmentSummary {
  readonly assessment_version: "engineering-assessment/1";
  readonly unit_system: "SI";
  readonly equilibrium_assessment: {
    readonly scope: "numerical_consistency_only";
    readonly applied_force_n: Vector3Tuple;
    readonly reaction_force_n: Vector3Tuple;
    readonly residual_force_n: Vector3Tuple;
    readonly residual_magnitude_n: number;
    readonly relative_residual: number | null;
    readonly reference_force_magnitude_n: number;
    readonly allowable_residual_magnitude_n: number;
    readonly governing_tolerance: "absolute" | "relative";
    readonly status: "within_tolerance" | "outside_tolerance";
    readonly policy: Readonly<Record<string, unknown>>;
  };
  readonly displacement_evidence: Readonly<Record<string, unknown>>;
  readonly stress_evidence: Readonly<{
    interpretation: "diagnostic_only";
    [key: string]: unknown;
  }>;
  readonly assumptions: readonly Readonly<{ code: string; message: string }>[];
  readonly warnings: readonly Readonly<{ code: string; message: string }>[];
}
