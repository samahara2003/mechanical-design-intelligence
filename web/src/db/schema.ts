import { sql } from "drizzle-orm";
import {
  char,
  check,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  primaryKey,
  text,
  timestamp,
  unique,
  uuid,
} from "drizzle-orm/pg-core";

import type { EngineeringDefinition } from "../domain/engineering-definition.ts";
import type { AnalysisStatus } from "../domain/analysis-lifecycle.ts";
import type { AnalysisProvenanceSummary, AnalysisResultSummary } from "../domain/result-contract.ts";

export const analysisStatus = pgEnum("analysis_status", [
  "draft", "queued", "running", "completed", "failed", "canceled",
]);

export const models = pgTable("models", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [check("models_name_nonempty", sql`length(btrim(${table.name})) > 0`)]);

export const modelVersions = pgTable("model_versions", {
  id: uuid("id").defaultRandom().primaryKey(),
  modelId: uuid("model_id").notNull().references(() => models.id, { onDelete: "restrict" }),
  versionNumber: integer("version_number").notNull(),
  originalFilename: text("original_filename").notNull(),
  cadSha256: char("cad_sha256", { length: 64 }).notNull(),
  artifactStorageKey: text("artifact_storage_key"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  unique("model_versions_model_version_unique").on(table.modelId, table.versionNumber),
  index("model_versions_model_id_idx").on(table.modelId),
  check("model_versions_positive_version", sql`${table.versionNumber} > 0`),
  check("model_versions_filename_nonempty", sql`length(btrim(${table.originalFilename})) > 0`),
  check("model_versions_sha256_format", sql`${table.cadSha256} ~ '^[0-9a-f]{64}$'`),
]);

export const analyses = pgTable("analyses", {
  id: uuid("id").defaultRandom().primaryKey(),
  modelVersionId: uuid("model_version_id").notNull().references(() => modelVersions.id, { onDelete: "restrict" }),
  status: analysisStatus("status").$type<AnalysisStatus>().default("draft").notNull(),
  engineeringDefinition: jsonb("engineering_definition").$type<EngineeringDefinition>().notNull(),
  definitionSha256: char("definition_sha256", { length: 64 }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  executionStartedAt: timestamp("execution_started_at", { withTimezone: true }),
}, (table) => [
  index("analyses_model_version_id_idx").on(table.modelVersionId),
  check("analyses_definition_object", sql`jsonb_typeof(${table.engineeringDefinition}) = 'object'`),
  check("analyses_sha256_format", sql`${table.definitionSha256} is null or ${table.definitionSha256} ~ '^[0-9a-f]{64}$'`),
  check("analyses_execution_lifecycle", sql`(${table.status} = 'draft' and ${table.executionStartedAt} is null) or (${table.status} <> 'draft' and ${table.executionStartedAt} is not null and ${table.definitionSha256} is not null)`),
]);

export const analysisResults = pgTable("analysis_results", {
  analysisId: uuid("analysis_id").notNull().references(() => analyses.id, { onDelete: "restrict" }),
  resultSummary: jsonb("result_summary").$type<AnalysisResultSummary>().notNull(),
  provenanceSummary: jsonb("provenance_summary").$type<AnalysisProvenanceSummary>().notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
}, (table) => [
  primaryKey({ name: "analysis_results_one_per_analysis", columns: [table.analysisId] }),
  check("analysis_results_summary_object", sql`jsonb_typeof(${table.resultSummary}) = 'object'`),
  check("analysis_results_provenance_object", sql`jsonb_typeof(${table.provenanceSummary}) = 'object'`),
]);
