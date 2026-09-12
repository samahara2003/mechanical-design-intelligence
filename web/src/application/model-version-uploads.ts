import { randomUUID } from "node:crypto";

import { eq, max, sql } from "drizzle-orm";

import type { MdiDatabase } from "./persistence.ts";
import * as schema from "../db/schema.ts";
import {
  isSha256,
  modelVersionSourceKey,
  STEP_CONTENT_TYPE,
  type StepUploadMetadata,
  UPLOAD_URL_TTL_SECONDS,
  validateStepUploadMetadata,
  verifyUploadForConfirmation,
} from "../domain/step-upload.ts";
import type { PrivateObjectStorage } from "../storage/object-storage.ts";

export class ModelVersionUploadError extends Error {}

export async function requestModelVersionUpload(
  db: MdiDatabase,
  storage: PrivateObjectStorage,
  modelId: string,
  metadata: StepUploadMetadata,
) {
  validateStepUploadMetadata(metadata);
  const model = await db.query.models.findFirst({ where: eq(schema.models.id, modelId) });
  if (model === undefined) throw new ModelVersionUploadError("Model does not exist");

  const uploadId = randomUUID();
  const objectKey = modelVersionSourceKey(modelId, uploadId);
  const expiresAt = new Date(Date.now() + UPLOAD_URL_TTL_SECONDS * 1000);
  await db.insert(schema.modelVersionUploads).values({
    id: uploadId,
    modelId,
    originalFilename: metadata.originalFilename,
    expectedSizeBytes: metadata.sizeBytes,
    expectedSha256: metadata.clientSha256,
    objectKey,
    expiresAt,
  });
  try {
    const signed = await storage.createPresignedPut({
      key: objectKey,
      contentLength: metadata.sizeBytes,
      contentType: STEP_CONTENT_TYPE,
      metadata: {
        sha256: metadata.clientSha256,
        "upload-id": uploadId,
        "model-id": modelId,
      },
      expiresInSeconds: UPLOAD_URL_TTL_SECONDS,
    });
    return {
      uploadId,
      uploadUrl: signed.url,
      requiredHeaders: signed.requiredHeaders,
      expiresAt: expiresAt.toISOString(),
    };
  } catch (error) {
    await db.delete(schema.modelVersionUploads).where(eq(schema.modelVersionUploads.id, uploadId));
    throw error;
  }
}

export async function confirmModelVersionUpload(
  db: MdiDatabase,
  storage: PrivateObjectStorage,
  input: { modelId: string; uploadId: string; clientSha256: string },
) {
  if (!isSha256(input.clientSha256)) throw new ModelVersionUploadError("invalid SHA-256");
  const upload = await db.query.modelVersionUploads.findFirst({
    where: eq(schema.modelVersionUploads.id, input.uploadId),
  });
  if (upload === undefined || upload.modelId !== input.modelId) {
    throw new ModelVersionUploadError("pending upload does not exist for this Model");
  }
  const object = await storage.headObject(upload.objectKey);
  verifyUploadForConfirmation(
    {
      id: upload.id,
      modelId: upload.modelId,
      originalFilename: upload.originalFilename,
      sizeBytes: upload.expectedSizeBytes,
      clientSha256: upload.expectedSha256,
      objectKey: upload.objectKey,
      expiresAt: upload.expiresAt,
    },
    object,
    input.clientSha256,
  );

  return db.transaction(async (tx) => {
    await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${input.modelId}, 0))`);
    const current = await tx.query.modelVersionUploads.findFirst({
      where: eq(schema.modelVersionUploads.id, input.uploadId),
    });
    if (current === undefined || current.modelId !== input.modelId) {
      throw new ModelVersionUploadError("pending upload was already finalized");
    }
    const [version] = await tx.select({ highest: max(schema.modelVersions.versionNumber) })
      .from(schema.modelVersions)
      .where(eq(schema.modelVersions.modelId, input.modelId));
    const [created] = await tx.insert(schema.modelVersions).values({
      id: current.id,
      modelId: current.modelId,
      versionNumber: (version.highest ?? 0) + 1,
      originalFilename: current.originalFilename,
      cadSha256: current.expectedSha256,
      artifactStorageKey: current.objectKey,
      sourceSizeBytes: current.expectedSizeBytes,
    }).returning();
    await tx.delete(schema.modelVersionUploads).where(eq(schema.modelVersionUploads.id, current.id));
    return created;
  });
}
