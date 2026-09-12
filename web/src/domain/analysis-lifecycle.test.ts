import assert from "node:assert/strict";
import test from "node:test";

import {
  AnalysisLifecycleError,
  createDraftAnalysis,
  createReplacementDraftAnalysis,
  editDraftAnalysis,
  fingerprintDraftAnalysis,
  isTerminalStatus,
  transitionAnalysis,
} from "./analysis-lifecycle.ts";
import { validEngineeringDefinition } from "../test-fixtures/engineering-definition.ts";

const modelVersionId = "11111111-1111-4111-8111-111111111111";
const initialTime = "2026-01-01T00:00:00.000Z";

function draft() {
  return createDraftAnalysis({
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    modelVersionId,
    engineeringDefinition: validEngineeringDefinition(modelVersionId),
    now: initialTime,
  });
}

test("draft definition edits replace immutable state and clear its fingerprint", () => {
  const original = fingerprintDraftAnalysis(draft(), "a".repeat(64), initialTime);
  const changed = structuredClone(validEngineeringDefinition(modelVersionId)) as any;
  changed.loads[0].magnitude_n = 2000;
  changed.loads[0].vector_n[0] = 2000;
  const edited = editDraftAnalysis(original, changed, "2026-01-02T00:00:00.000Z");
  const editedLoad = edited.engineeringDefinition.loads[0];
  const originalLoad = original.engineeringDefinition.loads[0];
  if (editedLoad.type !== "force" || originalLoad.type !== "force") assert.fail("expected forces");
  assert.equal(editedLoad.magnitude_n, 2000);
  assert.equal(edited.definitionSha256, null);
  assert.equal(originalLoad.magnitude_n, 1000);
  assert.ok(Object.isFrozen(edited.engineeringDefinition.loads));
});

test("only declared lifecycle transitions are accepted and terminal states remain terminal", () => {
  const frozen = fingerprintDraftAnalysis(draft(), "b".repeat(64), initialTime);
  const queued = transitionAnalysis(frozen, "queued", "2026-01-02T00:00:00.000Z");
  const running = transitionAnalysis(queued, "running", "2026-01-03T00:00:00.000Z");
  const completed = transitionAnalysis(running, "completed", "2026-01-04T00:00:00.000Z");
  assert.ok(isTerminalStatus(completed.status));
  assert.equal(queued.executionStartedAt, "2026-01-02T00:00:00.000Z");
  assert.throws(() => transitionAnalysis(completed, "running", initialTime), AnalysisLifecycleError);
  assert.throws(() => transitionAnalysis(draft(), "running", initialTime), AnalysisLifecycleError);

  const canceledQueued = transitionAnalysis(frozen, "queued", "2026-01-02T00:00:00.000Z");
  assert.equal(transitionAnalysis(canceledQueued, "canceled", initialTime).status, "canceled");
  assert.equal(transitionAnalysis(running, "failed", initialTime).status, "failed");
  assert.equal(transitionAnalysis(running, "canceled", initialTime).status, "canceled");
});

test("execution cannot start without authoritative Engineering Core fingerprint", () => {
  assert.throws(() => transitionAnalysis(draft(), "queued", initialTime), AnalysisLifecycleError);
});

test("engineering definition cannot be edited after draft", () => {
  const queued = transitionAnalysis(
    fingerprintDraftAnalysis(draft(), "c".repeat(64), initialTime),
    "queued",
    initialTime,
  );
  assert.throws(
    () => editDraftAnalysis(queued, validEngineeringDefinition(modelVersionId), initialTime),
    AnalysisLifecycleError,
  );
});

test("changed executed configuration produces a distinct draft Analysis", () => {
  const running = transitionAnalysis(
    transitionAnalysis(fingerprintDraftAnalysis(draft(), "d".repeat(64), initialTime), "queued", initialTime),
    "running",
    initialTime,
  );
  const changed = structuredClone(validEngineeringDefinition(modelVersionId)) as any;
  changed.mesh_config.characteristic_size_m = 0.005;
  const replacement = createReplacementDraftAnalysis({
    source: running,
    newId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    changedDefinition: changed,
    now: "2026-01-02T00:00:00.000Z",
  });
  assert.equal(replacement.status, "draft");
  assert.notEqual(replacement.id, running.id);
  assert.equal(replacement.modelVersionId, running.modelVersionId);
});
