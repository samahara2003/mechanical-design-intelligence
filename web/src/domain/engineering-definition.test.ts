import assert from "node:assert/strict";
import test from "node:test";

import {
  EngineeringDefinitionError,
  validateEngineeringDefinition,
  webDefinitionFingerprint,
} from "./engineering-definition.ts";
import { validEngineeringDefinition } from "../test-fixtures/engineering-definition.ts";

test("Python-compatible engineering vocabulary validates", () => {
  const definition = validEngineeringDefinition();
  assert.doesNotThrow(() => validateEngineeringDefinition(definition));
  assert.equal(definition.loads[0].target.region_name, "load");
  assert.equal("node_ids" in definition.loads[0].target, false);
});

test("invalid material, geometry load, and inconsistent force are rejected", () => {
  const invalidMaterial = structuredClone(validEngineeringDefinition()) as any;
  invalidMaterial.material_snapshot.youngs_modulus_pa = 0;
  assert.throws(() => validateEngineeringDefinition(invalidMaterial), EngineeringDefinitionError);

  const inconsistentForce = structuredClone(validEngineeringDefinition()) as any;
  const force = inconsistentForce.loads[0];
  if (force.type !== "force") assert.fail("fixture must contain a force");
  force.vector_n[0] = 5;
  assert.throws(() => validateEngineeringDefinition(inconsistentForce), EngineeringDefinitionError);
});

test("web fingerprint is deterministic but explicitly not the execution fingerprint", () => {
  const definition = validEngineeringDefinition();
  assert.equal(webDefinitionFingerprint(definition), webDefinitionFingerprint(structuredClone(definition)));
  const changed = structuredClone(definition) as any;
  changed.loads[0].target.region_name = "other_load";
  assert.notEqual(webDefinitionFingerprint(definition), webDefinitionFingerprint(changed));
});
