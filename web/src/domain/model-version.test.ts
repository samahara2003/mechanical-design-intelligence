import assert from "node:assert/strict";
import test from "node:test";

import {
  assertModelVersionUnchanged,
  createModelVersion,
  ModelVersionImmutabilityError,
} from "./model-version.ts";

test("ModelVersion is an immutable exact CAD revision", () => {
  const version = createModelVersion({
    id: "version-1",
    modelId: "model-1",
    versionNumber: 1,
    originalFilename: "bracket.step",
    cadSha256: "a".repeat(64),
    artifactStorageKey: null,
    sourceSizeBytes: 1024,
    createdAt: "2026-01-01T00:00:00.000Z",
  });
  assert.ok(Object.isFrozen(version));
  assert.doesNotThrow(() => assertModelVersionUnchanged(version, version));
  assert.throws(
    () => assertModelVersionUnchanged(version, { ...version, cadSha256: "b".repeat(64) }),
    ModelVersionImmutabilityError,
  );
});
