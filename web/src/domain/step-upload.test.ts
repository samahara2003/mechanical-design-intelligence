import assert from "node:assert/strict";
import test from "node:test";

import {
  isSha256,
  MAX_STEP_UPLOAD_BYTES,
  modelVersionSourceKey,
  StepUploadValidationError,
  validateStepUploadMetadata,
  verifyUploadForConfirmation,
} from "./step-upload.ts";

const sha = "a".repeat(64);
const upload = {
  id: "22222222-2222-4222-8222-222222222222",
  modelId: "11111111-1111-4111-8111-111111111111",
  originalFilename: "bracket.step",
  sizeBytes: 128,
  clientSha256: sha,
  objectKey: "models/11111111-1111-4111-8111-111111111111/versions/22222222-2222-4222-8222-222222222222/source.step",
  expiresAt: new Date("2026-01-02T00:00:00.000Z"),
};

test("STEP and STP metadata are accepted case-insensitively", () => {
  for (const originalFilename of ["part.step", "part.stp", "PART.STEP"]) {
    assert.doesNotThrow(() => validateStepUploadMetadata({
      originalFilename,
      sizeBytes: 1,
      clientSha256: sha,
    }));
  }
});

test("unsupported, empty, unsafe, zero-byte, and oversized uploads are rejected", () => {
  for (const originalFilename of ["part.iges", "part.step.exe", "../part.step", "folder\\part.stp"]) {
    assert.throws(
      () => validateStepUploadMetadata({ originalFilename, sizeBytes: 1, clientSha256: sha }),
      StepUploadValidationError,
    );
  }
  for (const sizeBytes of [0, -1, MAX_STEP_UPLOAD_BYTES + 1]) {
    assert.throws(
      () => validateStepUploadMetadata({ originalFilename: "part.step", sizeBytes, clientSha256: sha }),
      StepUploadValidationError,
    );
  }
});

test("SHA-256 uses strict lowercase hexadecimal format", () => {
  assert.equal(isSha256(sha), true);
  assert.equal(isSha256("A".repeat(64)), false);
  assert.equal(isSha256("a".repeat(63)), false);
});

test("storage key is generated only from server-controlled UUIDs", () => {
  assert.equal(modelVersionSourceKey(upload.modelId, upload.id), upload.objectKey);
  assert.equal(upload.objectKey.includes(upload.originalFilename), false);
  assert.throws(() => modelVersionSourceKey("../../unsafe", upload.id), StepUploadValidationError);
});

test("missing and mismatched uploads cannot finalize", () => {
  const now = new Date("2026-01-01T00:00:00.000Z");
  assert.throws(() => verifyUploadForConfirmation(upload, null, sha, now), StepUploadValidationError);
  assert.throws(() => verifyUploadForConfirmation(
    upload,
    { contentLength: 127, metadata: { sha256: sha, "upload-id": upload.id, "model-id": upload.modelId } },
    sha,
    now,
  ), StepUploadValidationError);
  assert.throws(() => verifyUploadForConfirmation(
    upload,
    { contentLength: 128, metadata: { sha256: "b".repeat(64), "upload-id": upload.id, "model-id": upload.modelId } },
    sha,
    now,
  ), StepUploadValidationError);
  assert.throws(() => verifyUploadForConfirmation(
    upload,
    { contentLength: 128, metadata: { sha256: sha, "upload-id": upload.id, "model-id": upload.modelId } },
    sha,
    upload.expiresAt,
  ), StepUploadValidationError);
});

test("matching object evidence permits confirmation", () => {
  assert.doesNotThrow(() => verifyUploadForConfirmation(
    upload,
    { contentLength: 128, metadata: { sha256: sha, "upload-id": upload.id, "model-id": upload.modelId } },
    sha,
    new Date("2026-01-01T00:00:00.000Z"),
  ));
});
