export const MAX_STEP_UPLOAD_BYTES = 100 * 1024 * 1024;
export const UPLOAD_URL_TTL_SECONDS = 5 * 60;
export const STEP_CONTENT_TYPE = "application/octet-stream";

export interface StepUploadMetadata {
  readonly originalFilename: string;
  readonly sizeBytes: number;
  readonly clientSha256: string;
}

export interface PendingStepUpload extends StepUploadMetadata {
  readonly id: string;
  readonly modelId: string;
  readonly objectKey: string;
  readonly expiresAt: Date;
}

export interface ObservedStepObject {
  readonly contentLength: number;
  readonly metadata: Readonly<Record<string, string>>;
}

export class StepUploadValidationError extends Error {}

export function isSha256(value: string): boolean {
  return /^[0-9a-f]{64}$/.test(value);
}

export function validateStepUploadMetadata(metadata: StepUploadMetadata): void {
  const filename = metadata.originalFilename;
  if (
    typeof filename !== "string"
    || filename.length === 0
    || filename.length > 255
    || filename.includes("\0")
    || filename.includes("/")
    || filename.includes("\\")
  ) {
    throw new StepUploadValidationError("original filename is invalid");
  }
  if (!/\.(step|stp)$/i.test(filename)) {
    throw new StepUploadValidationError("only .step and .stp files are accepted");
  }
  if (!Number.isSafeInteger(metadata.sizeBytes) || metadata.sizeBytes <= 0) {
    throw new StepUploadValidationError("STEP upload size must be a positive integer");
  }
  if (metadata.sizeBytes > MAX_STEP_UPLOAD_BYTES) {
    throw new StepUploadValidationError(
      `STEP upload exceeds the ${MAX_STEP_UPLOAD_BYTES} byte V0 limit`,
    );
  }
  if (!isSha256(metadata.clientSha256)) {
    throw new StepUploadValidationError("client SHA-256 must be 64 lowercase hexadecimal characters");
  }
}

function requireUuid(value: string, label: string): void {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new StepUploadValidationError(`${label} must be a UUID`);
  }
}

/** The key contains server-created IDs only; the original filename is never used. */
export function modelVersionSourceKey(modelId: string, modelVersionId: string): string {
  requireUuid(modelId, "model ID");
  requireUuid(modelVersionId, "model-version ID");
  return `models/${modelId}/versions/${modelVersionId}/source.step`;
}

export function verifyUploadForConfirmation(
  upload: PendingStepUpload,
  object: ObservedStepObject | null,
  confirmationSha256: string,
  now = new Date(),
): void {
  if (!isSha256(confirmationSha256) || confirmationSha256 !== upload.clientSha256) {
    throw new StepUploadValidationError("confirmation SHA-256 does not match upload intent");
  }
  if (now >= upload.expiresAt) {
    throw new StepUploadValidationError("upload intent has expired");
  }
  if (object === null) throw new StepUploadValidationError("uploaded STEP object does not exist");
  if (object.contentLength !== upload.sizeBytes) {
    throw new StepUploadValidationError("uploaded STEP size does not match upload intent");
  }
  if (
    object.metadata.sha256 !== upload.clientSha256
    || object.metadata["upload-id"] !== upload.id
    || object.metadata["model-id"] !== upload.modelId
  ) {
    throw new StepUploadValidationError("uploaded STEP metadata does not match upload intent");
  }
}
