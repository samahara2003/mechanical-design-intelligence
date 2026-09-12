# MDI Web Foundation

This directory contains the minimal Next.js App Router and PostgreSQL persistence foundation. It does not execute engineering analyses.

## Local commands

```text
npm install
npm test
npm run typecheck
npm run build
npm run db:migrate
npm run verify:upload
```

`db:migrate` requires `DATABASE_URL`; copy `.env.example` for local configuration. The checked-in SQL migrations include lifecycle and immutability constraints that are part of the persistence contract. `verify:upload` uses disposable records and requires the configured private R2 bucket as well as PostgreSQL.

## Engineering contract boundary

`src/domain/engineering-definition.ts` intentionally uses the snake-case JSON field vocabulary produced by Python `analysis_definition_to_dict`. Geometry selections remain named faces/volumes and never contain mesh IDs. Python remains authoritative for engineering validation and the execution fingerprint. The TypeScript validator protects application inputs and its local fingerprint supports draft change detection only.

## Private STEP upload V0

The browser accepts only `.step` and `.stp` files from 1 byte through 100 MiB, computes SHA-256, asks the server for a five-minute presigned PUT, and uploads directly to the private R2 bucket. The server owns the immutable key `models/{modelId}/versions/{modelVersionId}/source.step`; the original filename is metadata only. The signed PUT includes `If-None-Match: *`, so the key cannot be overwritten.

An incomplete request exists only in `model_version_uploads`. Confirmation uses R2 `HeadObject` to verify existence, byte size, and signed upload metadata before atomically creating the immutable `model_versions` row and removing the staging row. The browser hash is application/upload integrity metadata, not authoritative execution provenance. A future Engineering Worker must hash the exact bytes it downloads and compare them before execution.

For browser use, configure the private bucket's CORS policy for the application's origin, `PUT`, and the `Content-Type`, `If-None-Match`, and `x-amz-meta-*` request headers. V0 intentionally omits authentication, automatic abandoned-upload cleanup, multipart upload, and all FEA execution.
