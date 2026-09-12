CREATE TYPE "public"."analysis_job_status" AS ENUM('queued', 'claimed', 'finished');--> statement-breakpoint
CREATE TABLE "analysis_jobs" (
	"analysis_id" uuid PRIMARY KEY NOT NULL,
	"status" "analysis_job_status" DEFAULT 'queued' NOT NULL,
	"attempt_count" integer DEFAULT 0 NOT NULL,
	"available_at" timestamp with time zone DEFAULT now() NOT NULL,
	"claim_token" uuid,
	"claimed_by" text,
	"claimed_at" timestamp with time zone,
	"lease_expires_at" timestamp with time zone,
	"completed_at" timestamp with time zone,
	"failure_reason" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "analysis_jobs_attempt_nonnegative" CHECK ("analysis_jobs"."attempt_count" >= 0),
	CONSTRAINT "analysis_jobs_failure_reason_bounded" CHECK ("analysis_jobs"."failure_reason" is null or length("analysis_jobs"."failure_reason") between 1 and 1000),
	CONSTRAINT "analysis_jobs_state_consistency" CHECK (
    ("analysis_jobs"."status" = 'queued' and "analysis_jobs"."attempt_count" = 0 and "analysis_jobs"."claim_token" is null and "analysis_jobs"."claimed_by" is null and "analysis_jobs"."claimed_at" is null and "analysis_jobs"."lease_expires_at" is null and "analysis_jobs"."completed_at" is null and "analysis_jobs"."failure_reason" is null)
    or ("analysis_jobs"."status" = 'claimed' and "analysis_jobs"."attempt_count" > 0 and "analysis_jobs"."claim_token" is not null and "analysis_jobs"."claimed_by" is not null and "analysis_jobs"."claimed_at" is not null and "analysis_jobs"."lease_expires_at" is not null and "analysis_jobs"."completed_at" is null and "analysis_jobs"."failure_reason" is null)
    or ("analysis_jobs"."status" = 'finished' and "analysis_jobs"."attempt_count" > 0 and "analysis_jobs"."claim_token" is not null and "analysis_jobs"."claimed_by" is not null and "analysis_jobs"."claimed_at" is not null and "analysis_jobs"."lease_expires_at" is not null and "analysis_jobs"."completed_at" is not null)
  )
);
--> statement-breakpoint
ALTER TABLE "analysis_jobs" ADD CONSTRAINT "analysis_jobs_analysis_id_analyses_id_fk" FOREIGN KEY ("analysis_id") REFERENCES "public"."analyses"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "analysis_jobs_claim_idx" ON "analysis_jobs" USING btree ("status","available_at","lease_expires_at");