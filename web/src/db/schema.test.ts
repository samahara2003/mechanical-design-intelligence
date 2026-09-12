import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const migrationUrl = new URL("../../drizzle/0000_web_foundation.sql", import.meta.url);
const uploadMigrationUrl = new URL("../../drizzle/0001_model_version_uploads.sql", import.meta.url);
const workerMigrationUrl = new URL("../../drizzle/0002_analysis_jobs.sql", import.meta.url);

test("migration enforces one immutable final result per Analysis", async () => {
  const sql = await readFile(migrationUrl, "utf8");
  assert.match(sql, /analysis_results_one_per_analysis" PRIMARY KEY\s*\("analysis_id"\)/);
  assert.match(sql, /analysis_results_require_completed_analysis/);
  assert.match(sql, /analysis_results_immutable/);
});

test("migration enforces ModelVersion and Analysis immutability", async () => {
  const sql = await readFile(migrationUrl, "utf8");
  assert.match(sql, /model_versions_immutable/);
  assert.match(sql, /analyses_lifecycle_and_immutability/);
  assert.match(sql, /OLD\.status <> 'draft'/);
});

test("migration lists only the supported Analysis transitions", async () => {
  const sql = await readFile(migrationUrl, "utf8");
  for (const transition of [
    "OLD.status = 'draft' AND NEW.status = 'queued'",
    "OLD.status = 'queued' AND NEW.status IN ('running', 'canceled')",
    "OLD.status = 'running' AND NEW.status IN ('completed', 'failed', 'canceled')",
  ]) {
    assert.ok(sql.includes(transition));
  }
});

test("upload migration separates incomplete uploads from immutable ModelVersions", async () => {
  const sql = await readFile(uploadMigrationUrl, "utf8");
  assert.match(sql, /CREATE TABLE "model_version_uploads"/);
  assert.match(sql, /ALTER TABLE "model_versions" ADD COLUMN "source_size_bytes" bigint NOT NULL/);
  assert.match(sql, /model_version_uploads_sha256_format/);
  assert.match(sql, /model_version_uploads_expiry_after_creation/);
});

test("worker migration provides one lease-fenced job per Analysis", async () => {
  const sql = await readFile(workerMigrationUrl, "utf8");
  assert.match(sql, /CREATE TABLE "analysis_jobs"/);
  assert.match(sql, /analysis_id" uuid PRIMARY KEY/);
  assert.match(sql, /analysis_jobs_state_consistency/);
  assert.match(sql, /claim_token/);
  assert.match(sql, /lease_expires_at/);
});
