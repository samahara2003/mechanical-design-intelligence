# Mechanical Design Intelligence — Repository Instructions

Mechanical Design Intelligence is an evidence-based mechanical engineering review platform. Deterministic engineering calculations are the source of technical truth. AI may explain deterministic evidence, but it must never determine or override pass/fail status.

## Current milestone

Work only toward the Engineering Spike unless the user explicitly changes the milestone. Its purpose is to prove this pipeline on a known engineering case:

`STEP -> Gmsh/OpenCASCADE -> tetrahedral mesh -> CalculiX -> solver output -> Python parser -> structured engineering result`

The spike must eventually report at least maximum relevant stress and maximum displacement. Use the simplest deterministic, testable design that meets this goal. Do not build the web application, database, authentication, cloud deployment, queues, SSE, AI review, distributed/production infrastructure, or DFM functionality during this milestone. Do not implement a custom finite element solver.

## Engineering guardrails

- Keep V1 limited to the scope in [docs/requirements.md](docs/requirements.md); never silently expand the physics.
- Treat mesh and solver configuration, tool versions, material data, CAD checksum, loads, boundary conditions, assumptions, and result provenance as engineering evidence.
- Keep verification (solving the model correctly) distinct from validation (the model representing the physical system adequately).
- Do not treat maximum nodal stress as universally meaningful when stress singularities may be present.
- For the initial ductile criterion, calculate `FoS = yield strength / relevant von Mises stress`. Passing the configured requirement means `actual FoS >= required FoS`, not merely `FoS > 1`.
- Preserve executed analyses as immutable, reproducible historical records when that domain functionality is introduced.
- Prefer correctness before performance and evidence before AI.

## Architecture guardrails

- Use Python for the engineering pipeline, Gmsh/OpenCASCADE for geometry and meshing, and CalculiX for FEA.
- Future technology choices already decided are documented in [docs/architecture.md](docs/architecture.md). Do not implement them before their milestone.
- Do not select queue, cloud worker, object-storage, Redis, or deployment vendors without an explicit decision.
- Do not introduce Redis, Kafka, Kubernetes, microservices, distributed infrastructure, or unnecessary abstractions without a demonstrated requirement.

## Working practices

- Make important assumptions and limitations explicit; see [docs/engineering-assumptions.md](docs/engineering-assumptions.md).
- Add deterministic tests and benchmark evidence appropriate to engineering risk; see [docs/validation.md](docs/validation.md).
- Never create branches, commit, or push. The user owns all Git operations.
- Do not install dependencies unless explicitly requested for the active milestone.

## Design references

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Engineering assumptions](docs/engineering-assumptions.md)
- [Verification and validation](docs/validation.md)
