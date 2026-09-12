import {
  type EngineeringDefinition,
  validateEngineeringDefinition,
} from "./engineering-definition.ts";

export const analysisStatuses = [
  "draft",
  "queued",
  "running",
  "completed",
  "failed",
  "canceled",
] as const;

export type AnalysisStatus = (typeof analysisStatuses)[number];
export type TerminalAnalysisStatus = "completed" | "failed" | "canceled";

export interface AnalysisRecord {
  readonly id: string;
  readonly modelVersionId: string;
  readonly status: AnalysisStatus;
  readonly engineeringDefinition: EngineeringDefinition;
  readonly definitionSha256: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly executionStartedAt: string | null;
}

export class AnalysisLifecycleError extends Error {}

const transitions: Readonly<Record<AnalysisStatus, readonly AnalysisStatus[]>> = {
  draft: ["queued"],
  queued: ["running", "canceled"],
  running: ["completed", "failed", "canceled"],
  completed: [],
  failed: [],
  canceled: [],
};

function requireText(value: string, label: string): void {
  if (value.trim().length === 0) throw new AnalysisLifecycleError(`${label} must be nonempty`);
}

function requireTimestamp(value: string): void {
  if (!Number.isFinite(Date.parse(value))) throw new AnalysisLifecycleError("timestamp must be ISO-compatible");
}

function requireSha256(value: string): void {
  if (!/^[0-9a-f]{64}$/.test(value)) {
    throw new AnalysisLifecycleError("definition fingerprint must be lowercase SHA-256");
  }
}

function immutableDefinition(definition: EngineeringDefinition): EngineeringDefinition {
  return deepFreeze(structuredClone(definition));
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const nested of Object.values(value)) deepFreeze(nested);
  }
  return value;
}

function immutableAnalysis(value: AnalysisRecord): AnalysisRecord {
  return Object.freeze(value);
}

export function createDraftAnalysis(input: {
  id: string;
  modelVersionId: string;
  engineeringDefinition: EngineeringDefinition;
  now: string;
}): AnalysisRecord {
  requireText(input.id, "analysis ID");
  requireText(input.modelVersionId, "model-version ID");
  requireTimestamp(input.now);
  validateEngineeringDefinition(input.engineeringDefinition);
  if (input.engineeringDefinition.model_version_reference !== input.modelVersionId) {
    throw new AnalysisLifecycleError("definition must reference the Analysis model version");
  }
  return immutableAnalysis({
    id: input.id,
    modelVersionId: input.modelVersionId,
    status: "draft",
    engineeringDefinition: immutableDefinition(input.engineeringDefinition),
    definitionSha256: null,
    createdAt: input.now,
    updatedAt: input.now,
    executionStartedAt: null,
  });
}

export function editDraftAnalysis(
  analysis: AnalysisRecord,
  engineeringDefinition: EngineeringDefinition,
  now: string,
): AnalysisRecord {
  if (analysis.status !== "draft") {
    throw new AnalysisLifecycleError("engineering definition is immutable after draft");
  }
  requireTimestamp(now);
  validateEngineeringDefinition(engineeringDefinition);
  if (engineeringDefinition.model_version_reference !== analysis.modelVersionId) {
    throw new AnalysisLifecycleError("edited definition cannot change the model version");
  }
  return immutableAnalysis({
    ...analysis,
    engineeringDefinition: immutableDefinition(engineeringDefinition),
    definitionSha256: null,
    updatedAt: now,
  });
}

/** Record the Engineering Core fingerprint while the row is still a draft. */
export function fingerprintDraftAnalysis(
  analysis: AnalysisRecord,
  authoritativeDefinitionSha256: string,
  now: string,
): AnalysisRecord {
  if (analysis.status !== "draft") {
    throw new AnalysisLifecycleError("only a draft definition can be fingerprinted");
  }
  requireSha256(authoritativeDefinitionSha256);
  requireTimestamp(now);
  return immutableAnalysis({
    ...analysis,
    definitionSha256: authoritativeDefinitionSha256,
    updatedAt: now,
  });
}

export function canTransition(from: AnalysisStatus, to: AnalysisStatus): boolean {
  return transitions[from].includes(to);
}

export function transitionAnalysis(
  analysis: AnalysisRecord,
  to: AnalysisStatus,
  now: string,
): AnalysisRecord {
  requireTimestamp(now);
  if (!canTransition(analysis.status, to)) {
    throw new AnalysisLifecycleError(`invalid Analysis transition: ${analysis.status} -> ${to}`);
  }
  if (analysis.status === "draft") {
    if (analysis.definitionSha256 === null) {
      throw new AnalysisLifecycleError("draft must have an authoritative fingerprint before queueing");
    }
    requireSha256(analysis.definitionSha256);
  }
  return immutableAnalysis({
    ...analysis,
    status: to,
    updatedAt: now,
    executionStartedAt: analysis.status === "draft" ? now : analysis.executionStartedAt,
  });
}

export function createReplacementDraftAnalysis(input: {
  source: AnalysisRecord;
  newId: string;
  changedDefinition: EngineeringDefinition;
  now: string;
}): AnalysisRecord {
  if (input.source.status === "draft") {
    throw new AnalysisLifecycleError("edit the existing draft instead of replacing it");
  }
  if (input.newId === input.source.id) {
    throw new AnalysisLifecycleError("changed executed configuration requires a new Analysis ID");
  }
  return createDraftAnalysis({
    id: input.newId,
    modelVersionId: input.source.modelVersionId,
    engineeringDefinition: input.changedDefinition,
    now: input.now,
  });
}

export function isTerminalStatus(status: AnalysisStatus): status is TerminalAnalysisStatus {
  return status === "completed" || status === "failed" || status === "canceled";
}
