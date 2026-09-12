import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { loadEnvFile } from "node:process";

import pg from "pg";

if (existsSync(".env.local")) loadEnvFile(".env.local");
if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is unavailable");

const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
const client = await pool.connect();
const outcomes = [];
let savepointCounter = 0;

async function succeeds(label, operation) {
  await operation();
  outcomes.push({ invariant: label, outcome: "passed" });
}

async function fails(label, operation) {
  savepointCounter += 1;
  const savepoint = `expected_failure_${savepointCounter}`;
  await client.query(`SAVEPOINT ${savepoint}`);
  let rejected = false;
  try {
    await operation();
  } catch {
    rejected = true;
  }
  await client.query(`ROLLBACK TO SAVEPOINT ${savepoint}`);
  await client.query(`RELEASE SAVEPOINT ${savepoint}`);
  assert.equal(rejected, true, `${label} unexpectedly succeeded`);
  outcomes.push({ invariant: label, outcome: "rejected as required" });
}

const modelId = randomUUID();
const versionId = randomUUID();
const secondVersionId = randomUUID();
const analysisId = randomUUID();
const fingerprint = "c".repeat(64);
const definition = {
  unit_system: "SI",
  model_version_reference: versionId,
  material_snapshot: {
    name: "Disposable verification steel",
    youngs_modulus_pa: 200e9,
    poissons_ratio: 0.3,
    density_kg_per_m3: 7850,
    yield_strength_pa: null,
    source: null,
  },
  loads: [{
    type: "force",
    magnitude_n: 1000,
    unit_direction: [1, 0, 0],
    vector_n: [1000, 0, 0],
    target: { region_name: "load", entity: "face" },
  }],
  boundary_conditions: [{
    target: { region_name: "fixed", entity: "face" },
    constrained_dofs: ["UX", "UY", "UZ"],
  }],
  mesh_config: {
    element_type: "C3D10",
    element_order: 2,
    characteristic_size_m: 0.01,
    mesher_identifier: "Gmsh/OpenCASCADE",
    mesher_version: "verification",
  },
  solver_config: {
    solver_identifier: "CalculiX",
    solver_version: "verification",
    analysis_type: "linear_static",
    small_deformation: true,
    output_requests: ["displacement", "reaction_force", "integration_point_stress"],
  },
  required_factor_of_safety: null,
};

try {
  await client.query("BEGIN");
  await succeeds("Model insertion", () => client.query(
    `INSERT INTO models (id, name) VALUES ($1, $2)`,
    [modelId, "Disposable database verification model"],
  ));
  await succeeds("ModelVersion insertion", () => client.query(
    `INSERT INTO model_versions
      (id, model_id, version_number, original_filename, cad_sha256)
     VALUES ($1, $2, 1, $3, $4)`,
    [versionId, modelId, "verification.step", "a".repeat(64)],
  ));
  await client.query(
    `INSERT INTO model_versions
      (id, model_id, version_number, original_filename, cad_sha256)
     VALUES ($1, $2, 2, $3, $4)`,
    [secondVersionId, modelId, "verification-v2.step", "b".repeat(64)],
  );
  await client.query(
    `INSERT INTO analyses (id, model_version_id, engineering_definition)
     VALUES ($1, $2, $3::jsonb)`,
    [analysisId, versionId, JSON.stringify(definition)],
  );
  const editedDefinition = structuredClone(definition);
  editedDefinition.loads[0].magnitude_n = 1200;
  editedDefinition.loads[0].vector_n[0] = 1200;
  await succeeds("draft Analysis edit", () => client.query(
    `UPDATE analyses SET engineering_definition = $2::jsonb, updated_at = now()
     WHERE id = $1`,
    [analysisId, JSON.stringify(editedDefinition)],
  ));
  await fails("AnalysisResult before completed", () => client.query(
    `INSERT INTO analysis_results (analysis_id, result_summary, provenance_summary)
     VALUES ($1, '{}'::jsonb, '{}'::jsonb)`,
    [analysisId],
  ));
  await fails("invalid draft to running transition", () => client.query(
    `UPDATE analyses SET status = 'running', execution_started_at = now() WHERE id = $1`,
    [analysisId],
  ));
  await client.query(
    `UPDATE analyses SET definition_sha256 = $2 WHERE id = $1`,
    [analysisId, fingerprint],
  );
  await succeeds("draft to queued transition", () => client.query(
    `UPDATE analyses SET status = 'queued', execution_started_at = now(), updated_at = now()
     WHERE id = $1`,
    [analysisId],
  ));
  await fails("engineering definition mutation after draft", () => client.query(
    `UPDATE analyses SET engineering_definition = '{}'::jsonb WHERE id = $1`,
    [analysisId],
  ));
  await fails("model_version_id mutation after draft", () => client.query(
    `UPDATE analyses SET model_version_id = $2 WHERE id = $1`,
    [analysisId, secondVersionId],
  ));
  await fails("invalid queued to completed transition", () => client.query(
    `UPDATE analyses SET status = 'completed' WHERE id = $1`,
    [analysisId],
  ));
  await succeeds("queued to running transition", () => client.query(
    `UPDATE analyses SET status = 'running', updated_at = now() WHERE id = $1`,
    [analysisId],
  ));
  await fails("invalid running to queued transition", () => client.query(
    `UPDATE analyses SET status = 'queued' WHERE id = $1`,
    [analysisId],
  ));
  await succeeds("running to completed transition", () => client.query(
    `UPDATE analyses SET status = 'completed', updated_at = now() WHERE id = $1`,
    [analysisId],
  ));
  await fails("terminal Analysis transition", () => client.query(
    `UPDATE analyses SET status = 'failed' WHERE id = $1`,
    [analysisId],
  ));
  await succeeds("AnalysisResult insertion for completed Analysis", () => client.query(
    `INSERT INTO analysis_results (analysis_id, result_summary, provenance_summary)
     VALUES ($1, $2::jsonb, $3::jsonb)`,
    [
      analysisId,
      JSON.stringify({ unit_system: "SI", model_version_reference: versionId }),
      JSON.stringify({ analysis_definition_sha256: fingerprint }),
    ],
  ));
  await fails("second AnalysisResult for same Analysis", () => client.query(
    `INSERT INTO analysis_results (analysis_id, result_summary, provenance_summary)
     VALUES ($1, '{}'::jsonb, '{}'::jsonb)`,
    [analysisId],
  ));
  await fails("AnalysisResult update", () => client.query(
    `UPDATE analysis_results SET result_summary = '{}'::jsonb WHERE analysis_id = $1`,
    [analysisId],
  ));
  await fails("ModelVersion geometry identity mutation", () => client.query(
    `UPDATE model_versions SET cad_sha256 = $2 WHERE id = $1`,
    [versionId, "d".repeat(64)],
  ));
  await client.query("ROLLBACK");

  const cleanup = await pool.query(
    `SELECT
       (SELECT count(*) FROM models WHERE id = $1) AS models,
       (SELECT count(*) FROM model_versions WHERE id IN ($2, $3)) AS versions,
       (SELECT count(*) FROM analyses WHERE id = $4) AS analyses,
       (SELECT count(*) FROM analysis_results WHERE analysis_id = $4) AS results`,
    [modelId, versionId, secondVersionId, analysisId],
  );
  assert.deepEqual(cleanup.rows[0], { models: "0", versions: "0", analyses: "0", results: "0" });
  outcomes.push({ invariant: "disposable record cleanup", outcome: "rolled back" });
  console.log(JSON.stringify({ status: "ok", outcomes }, null, 2));
} catch (error) {
  try { await client.query("ROLLBACK"); } catch {}
  throw error;
} finally {
  client.release();
  await pool.end();
}
