import { createHash } from "node:crypto";

export type GeometryEntity = "face" | "volume";
export type TranslationalDof = "UX" | "UY" | "UZ";

export interface GeometrySelection {
  readonly region_name: string;
  readonly entity: GeometryEntity;
}

export interface MaterialSource {
  readonly reference: string;
  readonly revision: string | null;
}

export interface MaterialSnapshot {
  readonly name: string;
  readonly youngs_modulus_pa: number;
  readonly poissons_ratio: number;
  readonly density_kg_per_m3: number | null;
  readonly yield_strength_pa: number | null;
  readonly source: MaterialSource | null;
}

export interface ForceLoad {
  readonly type: "force";
  readonly magnitude_n: number;
  readonly unit_direction: readonly [number, number, number];
  readonly vector_n: readonly [number, number, number];
  readonly target: GeometrySelection;
}

export interface PressureLoad {
  readonly type: "pressure";
  readonly magnitude_pa: number;
  readonly normal_direction: "inward" | "outward";
  readonly target: GeometrySelection;
}

export type EngineeringLoad = ForceLoad | PressureLoad;

export interface BoundaryConditionDefinition {
  readonly target: GeometrySelection;
  readonly constrained_dofs: readonly TranslationalDof[];
}

export interface MeshConfigDefinition {
  readonly element_type: "C3D10";
  readonly element_order: 2;
  readonly characteristic_size_m: number;
  readonly mesher_identifier: string;
  readonly mesher_version: string;
}

export interface SolverConfigDefinition {
  readonly solver_identifier: string;
  readonly solver_version: string;
  readonly analysis_type: "linear_static";
  readonly small_deformation: true;
  readonly output_requests: readonly string[];
}

/** Exact field vocabulary emitted by Python analysis_definition_to_dict(). */
export interface EngineeringDefinition {
  readonly unit_system: "SI";
  readonly model_version_reference: string;
  readonly material_snapshot: MaterialSnapshot;
  readonly loads: readonly EngineeringLoad[];
  readonly boundary_conditions: readonly BoundaryConditionDefinition[];
  readonly mesh_config: MeshConfigDefinition;
  readonly solver_config: SolverConfigDefinition;
  readonly required_factor_of_safety: number | null;
}

export class EngineeringDefinitionError extends Error {}

function requireText(value: string, label: string): void {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new EngineeringDefinitionError(`${label} must be nonempty`);
  }
}

function requirePositiveFinite(value: number, label: string): void {
  if (!Number.isFinite(value) || value <= 0) {
    throw new EngineeringDefinitionError(`${label} must be positive and finite`);
  }
}

function validateSelection(selection: GeometrySelection, requiredEntity?: GeometryEntity): void {
  requireText(selection.region_name, "geometry region name");
  if (selection.entity !== "face" && selection.entity !== "volume") {
    throw new EngineeringDefinitionError("geometry entity must be face or volume");
  }
  if (requiredEntity !== undefined && selection.entity !== requiredEntity) {
    throw new EngineeringDefinitionError(`geometry selection must target a ${requiredEntity}`);
  }
}

export function validateEngineeringDefinition(definition: EngineeringDefinition): void {
  if (definition.unit_system !== "SI") {
    throw new EngineeringDefinitionError("only the SI unit system is supported");
  }
  requireText(definition.model_version_reference, "model version reference");
  const material = definition.material_snapshot;
  requireText(material.name, "material name");
  requirePositiveFinite(material.youngs_modulus_pa, "Young's modulus");
  if (!Number.isFinite(material.poissons_ratio) || material.poissons_ratio <= -1 || material.poissons_ratio >= 0.5) {
    throw new EngineeringDefinitionError("Poisson ratio must satisfy -1 < nu < 0.5");
  }
  if (material.density_kg_per_m3 !== null) requirePositiveFinite(material.density_kg_per_m3, "density");
  if (material.yield_strength_pa !== null) requirePositiveFinite(material.yield_strength_pa, "yield strength");
  if (material.source !== null) requireText(material.source.reference, "material source reference");
  if (definition.loads.length === 0 || definition.boundary_conditions.length === 0) {
    throw new EngineeringDefinitionError("at least one load and boundary condition are required");
  }
  for (const load of definition.loads) {
    validateSelection(load.target, "face");
    if (load.type === "force") {
      requirePositiveFinite(load.magnitude_n, "force magnitude");
      const norm = Math.hypot(...load.unit_direction);
      if (!Number.isFinite(norm) || Math.abs(norm - 1) > 1e-12) {
        throw new EngineeringDefinitionError("force unit_direction must be normalized");
      }
      for (let axis = 0; axis < 3; axis += 1) {
        const expected = load.magnitude_n * load.unit_direction[axis];
        if (!Number.isFinite(load.vector_n[axis]) || Math.abs(load.vector_n[axis] - expected) > Math.max(1e-9, Math.abs(expected) * 1e-12)) {
          throw new EngineeringDefinitionError("force vector_n must equal magnitude_n * unit_direction");
        }
      }
    } else if (load.type === "pressure") {
      requirePositiveFinite(load.magnitude_pa, "pressure magnitude");
      if (load.normal_direction !== "inward" && load.normal_direction !== "outward") {
        throw new EngineeringDefinitionError("pressure normal direction is invalid");
      }
    } else {
      throw new EngineeringDefinitionError("unsupported load type");
    }
  }
  for (const boundary of definition.boundary_conditions) {
    validateSelection(boundary.target, "face");
    if (boundary.constrained_dofs.length === 0 || new Set(boundary.constrained_dofs).size !== boundary.constrained_dofs.length) {
      throw new EngineeringDefinitionError("boundary DOFs must be nonempty and unique");
    }
    if (boundary.constrained_dofs.some((dof) => !["UX", "UY", "UZ"].includes(dof))) {
      throw new EngineeringDefinitionError("boundary contains an unsupported DOF");
    }
  }
  const mesh = definition.mesh_config;
  if (mesh.element_type !== "C3D10" || mesh.element_order !== 2) {
    throw new EngineeringDefinitionError("current V0 supports only C3D10 order 2");
  }
  requirePositiveFinite(mesh.characteristic_size_m, "characteristic mesh size");
  requireText(mesh.mesher_identifier, "mesher identifier");
  requireText(mesh.mesher_version, "mesher version");
  const solver = definition.solver_config;
  requireText(solver.solver_identifier, "solver identifier");
  requireText(solver.solver_version, "solver version");
  if (solver.analysis_type !== "linear_static" || solver.small_deformation !== true) {
    throw new EngineeringDefinitionError("current V0 supports only small-deformation linear static analysis");
  }
  if (new Set(solver.output_requests).size !== solver.output_requests.length) {
    throw new EngineeringDefinitionError("solver output requests must be unique");
  }
  if (definition.required_factor_of_safety !== null) {
    requirePositiveFinite(definition.required_factor_of_safety, "required factor of safety");
  }
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, nested]) => [key, canonicalize(nested)]),
    );
  }
  return value;
}

/**
 * Stable V0 web fingerprint. The authoritative execution fingerprint remains
 * the Python Engineering Core value because Python/JavaScript number rendering
 * is not assumed byte-identical.
 */
export function webDefinitionFingerprint(definition: EngineeringDefinition): string {
  validateEngineeringDefinition(definition);
  return createHash("sha256").update(JSON.stringify(canonicalize(definition)), "utf8").digest("hex");
}
