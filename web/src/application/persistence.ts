import { and, eq } from "drizzle-orm";
import type { NodePgDatabase } from "drizzle-orm/node-postgres";
import { isDeepStrictEqual } from "node:util";

import {
  AnalysisLifecycleError,
  canTransition,
  type AnalysisStatus,
} from "../domain/analysis-lifecycle.ts";
import {
  type EngineeringDefinition,
  validateEngineeringDefinition,
} from "../domain/engineering-definition.ts";
import type {
  AnalysisProvenanceSummary,
  AnalysisResultSummary,
} from "../domain/result-contract.ts";
import * as schema from "../db/schema.ts";

export type MdiDatabase = NodePgDatabase<typeof schema>;

export async function createModel(db: MdiDatabase, name: string) {
  if (name.trim().length === 0) throw new Error("model name must be nonempty");
  const [created] = await db.insert(schema.models).values({ name: name.trim() }).returning();
  return created;
}

export async function createModelVersionMetadata(
  db: MdiDatabase,
  input: {
    modelId: string;
    versionNumber: number;
    originalFilename: string;
    cadSha256: string;
    artifactStorageKey?: string | null;
    sourceSizeBytes: number;
  },
) {
  if (!Number.isInteger(input.versionNumber) || input.versionNumber <= 0) {
    throw new Error("version number must be a positive integer");
  }
  if (!/^[0-9a-f]{64}$/.test(input.cadSha256)) throw new Error("invalid CAD SHA-256");
  const [created] = await db.insert(schema.modelVersions).values(input).returning();
  return created;
}

export async function createDraftAnalysis(
  db: MdiDatabase,
  modelVersionId: string,
  engineeringDefinition: EngineeringDefinition,
) {
  validateEngineeringDefinition(engineeringDefinition);
  if (engineeringDefinition.model_version_reference !== modelVersionId) {
    throw new Error("engineering definition must reference the selected ModelVersion");
  }
  const [created] = await db.insert(schema.analyses).values({
    modelVersionId,
    engineeringDefinition,
    status: "draft",
  }).returning();
  return created;
}

export async function editDraftAnalysis(
  db: MdiDatabase,
  analysisId: string,
  engineeringDefinition: EngineeringDefinition,
) {
  validateEngineeringDefinition(engineeringDefinition);
  const [updated] = await db.update(schema.analyses).set({
    engineeringDefinition,
    definitionSha256: null,
    updatedAt: new Date(),
  }).where(and(
    eq(schema.analyses.id, analysisId),
    eq(schema.analyses.status, "draft"),
    eq(schema.analyses.modelVersionId, engineeringDefinition.model_version_reference),
  )).returning();
  if (updated === undefined) {
    throw new AnalysisLifecycleError("draft edit rejected because Analysis is missing or frozen");
  }
  return updated;
}

/**
 * Atomically records the Engineering Core fingerprint and leaves draft. The
 * conditional updates make a concurrent edit/queue attempt fail cleanly.
 */
export async function queueDraftAnalysis(
  db: MdiDatabase,
  analysisId: string,
  authoritativeDefinitionSha256: string,
  expectedDefinition: EngineeringDefinition,
) {
  if (!/^[0-9a-f]{64}$/.test(authoritativeDefinitionSha256)) {
    throw new Error("invalid authoritative AnalysisDefinition SHA-256");
  }
  if (!canTransition("draft", "queued")) throw new AnalysisLifecycleError("queue transition unavailable");
  validateEngineeringDefinition(expectedDefinition);
  return db.transaction(async (tx) => {
    const current = await tx.query.analyses.findFirst({
      where: eq(schema.analyses.id, analysisId),
    });
    if (current === undefined || current.status !== "draft") {
      throw new AnalysisLifecycleError("queue rejected because Analysis is missing or no longer draft");
    }
    validateEngineeringDefinition(current.engineeringDefinition);
    if (!isDeepStrictEqual(current.engineeringDefinition, expectedDefinition)) {
      throw new AnalysisLifecycleError("draft changed after authoritative fingerprinting");
    }
    if (current.engineeringDefinition.model_version_reference !== current.modelVersionId) {
      throw new AnalysisLifecycleError("Analysis definition does not reference its ModelVersion");
    }
    const [fingerprinted] = await tx.update(schema.analyses).set({
      definitionSha256: authoritativeDefinitionSha256,
      updatedAt: new Date(),
    }).where(and(eq(schema.analyses.id, analysisId), eq(schema.analyses.status, "draft"))).returning();
    if (fingerprinted === undefined) {
      throw new AnalysisLifecycleError("queue rejected because Analysis is missing or no longer draft");
    }
    await tx.insert(schema.analysisJobs).values({ analysisId });
    const startedAt = new Date();
    const [queued] = await tx.update(schema.analyses).set({
      status: "queued",
      executionStartedAt: startedAt,
      updatedAt: startedAt,
    }).where(and(
      eq(schema.analyses.id, analysisId),
      eq(schema.analyses.status, "draft"),
      eq(schema.analyses.definitionSha256, authoritativeDefinitionSha256),
    )).returning();
    if (queued === undefined) throw new AnalysisLifecycleError("conditional queue transition failed");
    return queued;
  });
}

export async function transitionAnalysis(
  db: MdiDatabase,
  analysisId: string,
  from: AnalysisStatus,
  to: AnalysisStatus,
) {
  if (!canTransition(from, to)) {
    throw new AnalysisLifecycleError(`invalid Analysis transition: ${from} -> ${to}`);
  }
  const [updated] = await db.update(schema.analyses).set({ status: to, updatedAt: new Date() })
    .where(and(eq(schema.analyses.id, analysisId), eq(schema.analyses.status, from))).returning();
  if (updated === undefined) throw new AnalysisLifecycleError("conditional transition failed");
  return updated;
}

export async function createFinalAnalysisResult(
  db: MdiDatabase,
  analysisId: string,
  resultSummary: AnalysisResultSummary,
  provenanceSummary: AnalysisProvenanceSummary,
) {
  const [created] = await db.insert(schema.analysisResults).values({
    analysisId, resultSummary, provenanceSummary,
  }).returning();
  return created;
}
