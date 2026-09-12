export interface ModelVersionRecord {
  readonly id: string;
  readonly modelId: string;
  readonly versionNumber: number;
  readonly originalFilename: string;
  readonly cadSha256: string;
  readonly artifactStorageKey: string | null;
  readonly sourceSizeBytes: number;
  readonly createdAt: string;
}

export class ModelVersionImmutabilityError extends Error {}
export class ModelVersionValidationError extends Error {}

export function createModelVersion(input: ModelVersionRecord): ModelVersionRecord {
  if (!Number.isInteger(input.versionNumber) || input.versionNumber <= 0) {
    throw new ModelVersionValidationError("version number must be a positive integer");
  }
  if (!/^[0-9a-f]{64}$/.test(input.cadSha256)) {
    throw new ModelVersionValidationError("CAD checksum must be lowercase SHA-256");
  }
  if (!Number.isSafeInteger(input.sourceSizeBytes) || input.sourceSizeBytes <= 0) {
    throw new ModelVersionValidationError("source size must be a positive integer");
  }
  for (const [label, value] of [["ID", input.id], ["model ID", input.modelId], ["filename", input.originalFilename]] as const) {
    if (value.trim().length === 0) throw new ModelVersionValidationError(`${label} must be nonempty`);
  }
  if (!Number.isFinite(Date.parse(input.createdAt))) throw new ModelVersionValidationError("createdAt is invalid");
  return Object.freeze({ ...input });
}

export function assertModelVersionUnchanged(
  before: ModelVersionRecord,
  after: ModelVersionRecord,
): void {
  if (JSON.stringify(before) !== JSON.stringify(after)) {
    throw new ModelVersionImmutabilityError(
      "ModelVersion is immutable; changed geometry metadata requires a new version",
    );
  }
}
