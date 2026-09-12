import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { basename, join, resolve, sep } from "node:path";
import { loadEnvFile } from "node:process";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";

import { eq } from "drizzle-orm";

import {
  confirmModelVersionUpload,
  requestModelVersionUpload,
} from "../src/application/model-version-uploads.ts";
import {
  createDraftAnalysis,
  createModel,
  queueDraftAnalysis,
  transitionAnalysis,
  type MdiDatabase,
} from "../src/application/persistence.ts";
import { closeDatabase, getDatabase } from "../src/db/client.ts";
import * as schema from "../src/db/schema.ts";
import {
  validateEngineeringDefinition,
  type EngineeringDefinition,
} from "../src/domain/engineering-definition.ts";
import { getPrivateObjectStorage } from "../src/storage/r2.ts";

if (existsSync(".env.local")) loadEnvFile(".env.local");

const repository = resolve("..");
const python = "python";
const fixturePath = resolve(repository, "artifacts", "bracket", "bracket.step");
const fixtureBytes = await readFile(fixturePath);
const fixtureSha256 = createHash("sha256").update(fixtureBytes).digest("hex");
const db: MdiDatabase = getDatabase();
const storage = getPrivateObjectStorage();
const workRoot = await mkdtemp(join(tmpdir(), "mdi-worker-e2e-"));
const cleanup = {
  modelIds: [] as string[],
  versionIds: [] as string[],
  analysisIds: [] as string[],
  objectKeys: [] as string[],
};

function toolVersion(command: string, args: string[], pattern: RegExp): string {
  const result = spawnSync(command, args, { encoding: "utf8" });
  assert.equal(result.error, undefined, `${command} version query failed to start`);
  const match = pattern.exec(`${result.stdout}\n${result.stderr}`);
  assert.ok(match, `${command} version was not recognized`);
  return match[1];
}

function authoritativeBracketDefinition(modelVersionId: string): {
  definition: EngineeringDefinition;
  fingerprint: string;
} {
  const gmshVersion = toolVersion("gmsh", ["--version"], /(\d+\.\d+\.\d+)/);
  const calculixVersion = toolVersion("ccx", ["-v"], /Version\s+(\d+(?:\.\d+)+)/i);
  const result = spawnSync(python, [
    resolve(repository, "scripts", "engineering_definition_cli.py"),
    "--model-version", modelVersionId,
    "--gmsh-version", gmshVersion,
    "--calculix-version", calculixVersion,
  ], { cwd: repository, encoding: "utf8" });
  assert.equal(result.status, 0, "Engineering Core definition construction failed");
  const value = JSON.parse(result.stdout);
  validateEngineeringDefinition(value.definition);
  assert.match(value.fingerprint, /^[0-9a-f]{64}$/);
  return value;
}

async function uploadModelVersion(name: string, declaredSha256: string) {
  const model = await createModel(db, name);
  cleanup.modelIds.push(model.id);
  const requested = await requestModelVersionUpload(db, storage, model.id, {
    originalFilename: "known-bracket.step",
    sizeBytes: fixtureBytes.byteLength,
    clientSha256: declaredSha256,
  });
  const put = await fetch(requested.uploadUrl, {
    method: "PUT",
    headers: requested.requiredHeaders,
    body: fixtureBytes,
  });
  assert.equal(put.ok, true, `fixture upload failed with ${put.status}`);
  const version = await confirmModelVersionUpload(db, storage, {
    modelId: model.id,
    uploadId: requested.uploadId,
    clientSha256: declaredSha256,
  });
  cleanup.versionIds.push(version.id);
  cleanup.objectKeys.push(version.artifactStorageKey!);
  return version;
}

function runWorkerOnce() {
  const result = spawnSync(python, [
    resolve(repository, "scripts", "engineering_worker.py"),
    "--once",
    "--work-root", workRoot,
  ], { cwd: repository, encoding: "utf8", env: process.env, timeout: 300_000 });
  assert.equal(result.status, 0, `worker process failed: ${result.stderr.trim().slice(0, 300)}`);
  return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)!);
}

async function createAndQueue(versionId: string) {
  const authoritative = authoritativeBracketDefinition(versionId);
  const analysis = await createDraftAnalysis(db, versionId, authoritative.definition);
  cleanup.analysisIds.push(analysis.id);
  await queueDraftAnalysis(db, analysis.id, authoritative.fingerprint, authoritative.definition);
  return { analysis, authoritative };
}

try {
  const version = await uploadModelVersion("Disposable worker bracket", fixtureSha256);
  const { analysis, authoritative } = await createAndQueue(version.id);

  // Seed an expired claim to prove the real worker can reclaim it. The token is
  // retained to demonstrate that the new claim fences stale completion.
  const staleToken = randomUUID();
  await db.update(schema.analysisJobs).set({
    status: "claimed",
    attemptCount: 1,
    claimToken: staleToken,
    claimedBy: "disposable-crashed-worker",
    claimedAt: new Date(Date.now() - 120_000),
    leaseExpiresAt: new Date(Date.now() - 60_000),
  }).where(eq(schema.analysisJobs.analysisId, analysis.id));
  await transitionAnalysis(db, analysis.id, "queued", "running");

  const successfulWorker = runWorkerOnce();
  assert.equal(successfulWorker.status, "completed", JSON.stringify(successfulWorker));
  assert.equal(successfulWorker.downloaded_step_sha256_verified, true);
  const completed = await db.query.analyses.findFirst({ where: eq(schema.analyses.id, analysis.id) });
  assert.equal(completed?.status, "completed");
  assert.equal(completed?.definitionSha256, authoritative.fingerprint);
  const results = await db.query.analysisResults.findMany({
    where: eq(schema.analysisResults.analysisId, analysis.id),
  });
  assert.equal(results.length, 1);
  const result = results[0];
  assert.equal(result.resultSummary.unit_system, "SI");
  assert.ok(result.resultSummary.mesh_summary.node_count > 0);
  assert.ok(result.resultSummary.displacement_summary.global_maximum_magnitude_m > 0);
  assert.ok((result.resultSummary.stress_summary.global_raw_max_von_mises as { von_mises_pa: number }).von_mises_pa > 0);
  assert.equal(result.provenanceSummary.analysis_definition_sha256, authoritative.fingerprint);
  const provenanceArtifacts = result.provenanceSummary.artifacts as Record<string, {
    sha256: string; byte_size: number; storage_key: string;
  }>;
  const finishedJob = await db.query.analysisJobs.findFirst({
    where: eq(schema.analysisJobs.analysisId, analysis.id),
  });
  assert.equal(finishedJob?.status, "finished");
  assert.equal(finishedJob?.attemptCount, 2);
  assert.notEqual(finishedJob?.claimToken, staleToken);
  assert.equal(provenanceArtifacts.cad_step.sha256, fixtureSha256);
  assert.equal(provenanceArtifacts.cad_step.storage_key, version.artifactStorageKey);
  for (const role of ["mesh", "solver_input", "solver_dat", "solver_frd"]) {
    const artifact = provenanceArtifacts[role];
    assert.ok(artifact.storage_key.includes(`/attempts/${finishedJob?.claimToken}/`));
    assert.ok(!artifact.storage_key.includes(`/attempts/${staleToken}/`));
    cleanup.objectKeys.push(artifact.storage_key);
    const head = await storage.headObject(artifact.storage_key);
    assert.equal(head?.contentLength, artifact.byte_size);
    assert.equal(head?.metadata.sha256, artifact.sha256);
    assert.equal(head?.metadata["claim-token"], finishedJob?.claimToken);
  }
  assert.equal(runWorkerOnce().status, "idle");
  assert.equal((await db.query.analysisResults.findMany({
    where: eq(schema.analysisResults.analysisId, analysis.id),
  })).length, 1);

  const canceled = await createAndQueue(version.id);
  await transitionAnalysis(db, canceled.analysis.id, "queued", "canceled");
  assert.equal(runWorkerOnce().status, "idle");
  assert.equal((await db.query.analysisResults.findMany({
    where: eq(schema.analysisResults.analysisId, canceled.analysis.id),
  })).length, 0);

  // Upload metadata can be self-consistent while its claimed browser hash is
  // wrong. The worker must detect that from exact downloaded bytes before FEA.
  const badVersion = await uploadModelVersion("Disposable bad-hash bracket", "0".repeat(64));
  const bad = await createAndQueue(badVersion.id);
  const failedWorker = runWorkerOnce();
  assert.equal(failedWorker.status, "failed");
  assert.match(failedWorker.reason, /^step_integrity failed/);
  const failedAnalysis = await db.query.analyses.findFirst({
    where: eq(schema.analyses.id, bad.analysis.id),
  });
  assert.equal(failedAnalysis?.status, "failed");
  assert.equal((await db.query.analysisResults.findMany({
    where: eq(schema.analysisResults.analysisId, bad.analysis.id),
  })).length, 0);
  const failedJob = await db.query.analysisJobs.findFirst({
    where: eq(schema.analysisJobs.analysisId, bad.analysis.id),
  });
  assert.equal(failedJob?.status, "finished");
  assert.match(failedJob?.failureReason ?? "", /^step_integrity failed/);
  for (const role of ["mesh.msh", "solver_input.inp", "solver_dat.dat", "solver_frd.frd"]) {
    assert.equal(
      await storage.headObject(
        `analyses/${bad.analysis.id}/attempts/${failedJob?.claimToken}/${role}`,
      ),
      null,
    );
  }

  console.log(JSON.stringify({
    status: "ok",
    analysisCompleted: true,
    exactStepHashVerifiedByWorker: true,
    compactResultPersisted: true,
    provenanceReferencesConsumedStep: true,
    generatedPrivateArtifactsVerified: 4,
    expiredClaimReclaimed: true,
    staleClaimFenced: true,
    duplicateDeliveryIdle: true,
    canceledAnalysisNotClaimed: true,
    badHashFailedBeforeFea: true,
    meshNodes: result.resultSummary.mesh_summary.node_count,
    meshElements: result.resultSummary.mesh_summary.element_count,
    maximumDisplacementM: result.resultSummary.displacement_summary.global_maximum_magnitude_m,
    globalRawMaximumVonMisesPa: (
      result.resultSummary.stress_summary.global_raw_max_von_mises as { von_mises_pa: number }
    ).von_mises_pa,
    equilibriumResidualN: result.resultSummary.equilibrium_evidence.residual_magnitude_n,
  }, null, 2));
} finally {
  for (const key of [...new Set(cleanup.objectKeys)].reverse()) {
    await storage.deleteObject(key);
  }
  for (const key of [...new Set(cleanup.objectKeys)]) {
    assert.equal(await storage.headObject(key), null);
  }
  for (const id of cleanup.analysisIds.reverse()) {
    await db.delete(schema.analysisResults).where(eq(schema.analysisResults.analysisId, id));
    await db.delete(schema.analysisJobs).where(eq(schema.analysisJobs.analysisId, id));
    await db.delete(schema.analyses).where(eq(schema.analyses.id, id));
  }
  for (const id of cleanup.versionIds.reverse()) {
    await db.delete(schema.modelVersions).where(eq(schema.modelVersions.id, id));
  }
  for (const id of cleanup.modelIds.reverse()) {
    await db.delete(schema.models).where(eq(schema.models.id, id));
  }
  const tempRoot = resolve(tmpdir()) + sep;
  const resolvedWorkRoot = resolve(workRoot);
  assert.ok(resolvedWorkRoot.startsWith(tempRoot) && basename(resolvedWorkRoot).startsWith("mdi-worker-e2e-"));
  await rm(resolvedWorkRoot, { recursive: true, force: true });
  await closeDatabase();
}
