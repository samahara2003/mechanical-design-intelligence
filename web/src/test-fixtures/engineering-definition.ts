import type { EngineeringDefinition } from "../domain/engineering-definition.ts";

export function validEngineeringDefinition(
  modelVersionReference = "11111111-1111-4111-8111-111111111111",
): EngineeringDefinition {
  return {
    unit_system: "SI",
    model_version_reference: modelVersionReference,
    material_snapshot: {
      name: "Steel",
      youngs_modulus_pa: 200e9,
      poissons_ratio: 0.3,
      density_kg_per_m3: 7850,
      yield_strength_pa: null,
      source: { reference: "project material card", revision: "1" },
    },
    loads: [{
      type: "force",
      magnitude_n: 1000,
      unit_direction: [1, 0, 0],
      vector_n: [1000, 0, 0],
      target: { region_name: "load", entity: "face" },
    }],
    boundary_conditions: [{
      target: { region_name: "fixed", entity: "face" },
      constrained_dofs: ["UX", "UY", "UZ"],
    }],
    mesh_config: {
      element_type: "C3D10",
      element_order: 2,
      characteristic_size_m: 0.01,
      mesher_identifier: "Gmsh/OpenCASCADE",
      mesher_version: "4.15.2",
    },
    solver_config: {
      solver_identifier: "CalculiX",
      solver_version: "2.23",
      analysis_type: "linear_static",
      small_deformation: true,
      output_requests: ["displacement", "reaction_force", "integration_point_stress"],
    },
    required_factor_of_safety: null,
  };
}
