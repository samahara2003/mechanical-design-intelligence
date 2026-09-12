CREATE TABLE "model_version_uploads" (
	"id" uuid PRIMARY KEY NOT NULL,
	"model_id" uuid NOT NULL,
	"original_filename" text NOT NULL,
	"expected_size_bytes" bigint NOT NULL,
	"expected_sha256" char(64) NOT NULL,
	"object_key" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	CONSTRAINT "model_version_uploads_object_key_unique" UNIQUE("object_key"),
	CONSTRAINT "model_version_uploads_filename_nonempty" CHECK (length(btrim("model_version_uploads"."original_filename")) > 0),
	CONSTRAINT "model_version_uploads_size_positive" CHECK ("model_version_uploads"."expected_size_bytes" > 0),
	CONSTRAINT "model_version_uploads_sha256_format" CHECK ("model_version_uploads"."expected_sha256" ~ '^[0-9a-f]{64}$'),
	CONSTRAINT "model_version_uploads_expiry_after_creation" CHECK ("model_version_uploads"."expires_at" > "model_version_uploads"."created_at")
);
--> statement-breakpoint
ALTER TABLE "model_versions" ADD COLUMN "source_size_bytes" bigint NOT NULL;--> statement-breakpoint
ALTER TABLE "model_version_uploads" ADD CONSTRAINT "model_version_uploads_model_id_models_id_fk" FOREIGN KEY ("model_id") REFERENCES "public"."models"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "model_version_uploads_model_id_idx" ON "model_version_uploads" USING btree ("model_id");--> statement-breakpoint
ALTER TABLE "model_versions" ADD CONSTRAINT "model_versions_source_size_positive" CHECK ("model_versions"."source_size_bytes" > 0);