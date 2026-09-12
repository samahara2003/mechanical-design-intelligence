CREATE TYPE "public"."analysis_status" AS ENUM('draft', 'queued', 'running', 'completed', 'failed', 'canceled');--> statement-breakpoint
CREATE TABLE "analyses" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"model_version_id" uuid NOT NULL,
	"status" "analysis_status" DEFAULT 'draft' NOT NULL,
	"engineering_definition" jsonb NOT NULL,
	"definition_sha256" char(64),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"execution_started_at" timestamp with time zone,
	CONSTRAINT "analyses_definition_object" CHECK (jsonb_typeof("analyses"."engineering_definition") = 'object'),
	CONSTRAINT "analyses_sha256_format" CHECK ("analyses"."definition_sha256" is null or "analyses"."definition_sha256" ~ '^[0-9a-f]{64}$'),
	CONSTRAINT "analyses_execution_lifecycle" CHECK (("analyses"."status" = 'draft' and "analyses"."execution_started_at" is null) or ("analyses"."status" <> 'draft' and "analyses"."execution_started_at" is not null and "analyses"."definition_sha256" is not null))
);
--> statement-breakpoint
CREATE TABLE "analysis_results" (
	"analysis_id" uuid NOT NULL,
	"result_summary" jsonb NOT NULL,
	"provenance_summary" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "analysis_results_one_per_analysis" PRIMARY KEY("analysis_id"),
	CONSTRAINT "analysis_results_summary_object" CHECK (jsonb_typeof("analysis_results"."result_summary") = 'object'),
	CONSTRAINT "analysis_results_provenance_object" CHECK (jsonb_typeof("analysis_results"."provenance_summary") = 'object')
);
--> statement-breakpoint
CREATE TABLE "model_versions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"model_id" uuid NOT NULL,
	"version_number" integer NOT NULL,
	"original_filename" text NOT NULL,
	"cad_sha256" char(64) NOT NULL,
	"artifact_storage_key" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "model_versions_model_version_unique" UNIQUE("model_id","version_number"),
	CONSTRAINT "model_versions_positive_version" CHECK ("model_versions"."version_number" > 0),
	CONSTRAINT "model_versions_filename_nonempty" CHECK (length(btrim("model_versions"."original_filename")) > 0),
	CONSTRAINT "model_versions_sha256_format" CHECK ("model_versions"."cad_sha256" ~ '^[0-9a-f]{64}$')
);
--> statement-breakpoint
CREATE TABLE "models" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "models_name_nonempty" CHECK (length(btrim("models"."name")) > 0)
);
--> statement-breakpoint
ALTER TABLE "analyses" ADD CONSTRAINT "analyses_model_version_id_model_versions_id_fk" FOREIGN KEY ("model_version_id") REFERENCES "public"."model_versions"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "analysis_results" ADD CONSTRAINT "analysis_results_analysis_id_analyses_id_fk" FOREIGN KEY ("analysis_id") REFERENCES "public"."analyses"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "model_versions" ADD CONSTRAINT "model_versions_model_id_models_id_fk" FOREIGN KEY ("model_id") REFERENCES "public"."models"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "analyses_model_version_id_idx" ON "analyses" USING btree ("model_version_id");--> statement-breakpoint
CREATE INDEX "model_versions_model_id_idx" ON "model_versions" USING btree ("model_id");--> statement-breakpoint
CREATE FUNCTION mdi_reject_model_version_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ModelVersion is immutable; create a new version instead';
END;
$$;--> statement-breakpoint
CREATE TRIGGER model_versions_immutable
BEFORE UPDATE ON "model_versions"
FOR EACH ROW EXECUTE FUNCTION mdi_reject_model_version_update();--> statement-breakpoint
CREATE FUNCTION mdi_enforce_analysis_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  valid_transition boolean;
BEGIN
  IF (OLD.status <> 'draft' OR NEW.status IS DISTINCT FROM OLD.status)
     AND (
       NEW.model_version_id IS DISTINCT FROM OLD.model_version_id
       OR NEW.engineering_definition IS DISTINCT FROM OLD.engineering_definition
       OR NEW.definition_sha256 IS DISTINCT FROM OLD.definition_sha256
     ) THEN
    RAISE EXCEPTION 'executing Analysis engineering definition is immutable';
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    valid_transition :=
      (OLD.status = 'draft' AND NEW.status = 'queued')
      OR (OLD.status = 'queued' AND NEW.status IN ('running', 'canceled'))
      OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed', 'canceled'));
    IF NOT valid_transition THEN
      RAISE EXCEPTION 'invalid Analysis status transition: % -> %', OLD.status, NEW.status;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;--> statement-breakpoint
CREATE TRIGGER analyses_lifecycle_and_immutability
BEFORE UPDATE ON "analyses"
FOR EACH ROW EXECUTE FUNCTION mdi_enforce_analysis_update();--> statement-breakpoint
CREATE FUNCTION mdi_require_completed_analysis_result() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM "analyses"
    WHERE "id" = NEW.analysis_id AND "status" = 'completed'
  ) THEN
    RAISE EXCEPTION 'AnalysisResult requires a completed Analysis';
  END IF;
  RETURN NEW;
END;
$$;--> statement-breakpoint
CREATE TRIGGER analysis_results_require_completed_analysis
BEFORE INSERT ON "analysis_results"
FOR EACH ROW EXECUTE FUNCTION mdi_require_completed_analysis_result();--> statement-breakpoint
CREATE FUNCTION mdi_reject_analysis_result_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'final AnalysisResult is immutable; create a new Analysis instead';
END;
$$;--> statement-breakpoint
CREATE TRIGGER analysis_results_immutable
BEFORE UPDATE ON "analysis_results"
FOR EACH ROW EXECUTE FUNCTION mdi_reject_analysis_result_update();
