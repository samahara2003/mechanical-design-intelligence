import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { loadEnvFile } from "node:process";

import { eq } from "drizzle-orm";

import {
  confirmModelVersionUpload,
  requestModelVersionUpload,
} from "../src/application/model-version-uploads.ts";
import { createModel, type MdiDatabase } from "../src/application/persistence.ts";
import { closeDatabase, getDatabase } from "../src/db/client.ts";
import * as schema from "../src/db/schema.ts";
import { getPrivateObjectStorage } from "../src/storage/r2.ts";

if (existsSync(".env.local")) loadEnvFile(".env.local");

const fixture = [
  "ISO-10303-21;",
  "HEADER;",
  "FILE_DESCRIPTION(('MDI disposable upload verification'),'2;1');",
  "FILE_NAME('fixture.step','2026-01-01T00:00:00',('MDI'),('MDI'),'','','');",
  "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));",
  "ENDSEC;",
  "DATA;",
  "ENDSEC;",
  "END-ISO-10303-21;",
  "",
].join("\n");
const fixtureBytes = new TextEncoder().encode(fixture);
const fixtureSha256 = createHash("sha256").update(fixtureBytes).digest("hex");
const db: MdiDatabase = getDatabase();
const storage = getPrivateObjectStorage();
let modelId: string | undefined;
let uploadId: string | undefined;
let objectKey: string | undefined;
let modelVersionId: string | undefined;

try {
  const model = await createModel(db, "Disposable R2 upload verification");
  modelId = model.id;
  const requested = await requestModelVersionUpload(db, storage, model.id, {
    originalFilename: "disposable-fixture.step",
    sizeBytes: fixtureBytes.byteLength,
    clientSha256: fixtureSha256,
  });
  uploadId = requested.uploadId;
  const pending = await db.query.modelVersionUploads.findFirst({
    where: eq(schema.modelVersionUploads.id, uploadId),
  });
  assert.ok(pending);
  objectKey = pending.objectKey;
  assert.equal(objectKey, `models/${model.id}/versions/${uploadId}/source.step`);

  const put = await fetch(requested.uploadUrl, {
    method: "PUT",
    headers: requested.requiredHeaders,
    body: fixtureBytes,
  });
  if (!put.ok) {
    const responseBody = await put.text();
    const errorCode = /<Code>([^<]+)<\/Code>/.exec(responseBody)?.[1] ?? "unknown";
    assert.fail(`presigned R2 PUT failed with ${put.status} (${errorCode})`);
  }
  const overwrite = await fetch(requested.uploadUrl, {
    method: "PUT",
    headers: requested.requiredHeaders,
    body: fixtureBytes,
  });
  assert.equal(overwrite.status, 412, "the immutable object key accepted an overwrite");

  const version = await confirmModelVersionUpload(db, storage, {
    modelId: model.id,
    uploadId,
    clientSha256: fixtureSha256,
  });
  modelVersionId = version.id;
  assert.equal(version.cadSha256, fixtureSha256);
  assert.equal(version.sourceSizeBytes, fixtureBytes.byteLength);
  assert.equal(version.artifactStorageKey, objectKey);
  assert.equal(version.originalFilename, "disposable-fixture.step");

  const storedBytes = await storage.getObjectBytesForVerification(objectKey);
  const storedSha256 = createHash("sha256").update(storedBytes).digest("hex");
  assert.equal(storedBytes.byteLength, fixtureBytes.byteLength);
  assert.equal(storedSha256, fixtureSha256);
  assert.deepEqual(storedBytes, fixtureBytes);

  console.log(JSON.stringify({
    status: "ok",
    presignedDirectPut: true,
    objectOverwriteRejected: true,
    objectHeadVerifiedBeforeFinalization: true,
    finalizedModelVersion: true,
    storedSizeMatches: true,
    storedSha256Matches: true,
  }, null, 2));
} finally {
  if (objectKey !== undefined) {
    try { await storage.deleteObject(objectKey); } catch {}
  }
  if (modelVersionId !== undefined) {
    await db.delete(schema.modelVersions).where(eq(schema.modelVersions.id, modelVersionId));
  }
  if (uploadId !== undefined) {
    await db.delete(schema.modelVersionUploads).where(eq(schema.modelVersionUploads.id, uploadId));
  }
  if (modelId !== undefined) {
    await db.delete(schema.models).where(eq(schema.models.id, modelId));
  }
  if (objectKey !== undefined) {
    assert.equal(await storage.headObject(objectKey), null);
  }
  if (modelId !== undefined) {
    const remaining = await db.query.models.findFirst({ where: eq(schema.models.id, modelId) });
    assert.equal(remaining, undefined);
  }
  await closeDatabase();
}
