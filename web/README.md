# MDI Web Foundation

This directory contains the minimal Next.js App Router and PostgreSQL persistence foundation. It does not execute engineering analyses.

## Local commands

```text
npm install
npm test
npm run typecheck
npm run build
npm run db:migrate
```

`db:migrate` requires `DATABASE_URL`; copy `.env.example` for local configuration. The initial SQL migration is checked in because lifecycle and immutability triggers are part of the persistence contract.

## Engineering contract boundary

`src/domain/engineering-definition.ts` intentionally uses the snake-case JSON field vocabulary produced by Python `analysis_definition_to_dict`. Geometry selections remain named faces/volumes and never contain mesh IDs. Python remains authoritative for engineering validation and the execution fingerprint. The TypeScript validator protects application inputs and its local fingerprint supports draft change detection only.
