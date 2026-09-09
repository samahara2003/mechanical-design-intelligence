# Requirements

## Purpose and governing principle

Mechanical Design Intelligence is an evidence-based mechanical engineering review platform. It will allow a mechanical engineer to upload a 3D CAD model, configure and run a static structural analysis, inspect deterministic results, and receive an AI-assisted explanation.

Engineering calculations are the source of technical truth. AI may explain deterministic engineering evidence, but must never determine whether a design passes or fails.

## V1 scope

V1 is limited to:

- STEP input
- a single solid part
- linear static structural analysis
- linear elastic, isotropic materials
- small deformation
- simplified fixed boundary conditions
- force and pressure loads
- a user-defined required minimum factor of safety (FoS)
- Gmsh/OpenCASCADE for geometry processing and tetrahedral meshing
- CalculiX for finite element analysis
- Python for the engineering pipeline
- Next.js and TypeScript for the future web application
- PostgreSQL for future relational metadata
- private object storage for future CAD and simulation artifacts
- Three.js and React Three Fiber for future 3D visualization

The physics scope must not be expanded silently.

### Explicitly out of scope for V1

- plasticity
- nonlinear materials
- large deformation
- contact
- friction
- bolt preload
- fatigue
- transient analysis
- thermal analysis
- assemblies

## Functional requirements

### FR1 — Model upload and revision

Users must be able to upload a 3D CAD model for engineering review. A geometry revision creates a new `ModelVersion`.

### FR2 — Analysis configuration

Users must be able to configure a static structural analysis by:

- selecting a material;
- defining loads and boundary conditions on the model; and
- specifying a required minimum factor of safety.

### FR3 — Deterministic results and requirement evaluation

Users must be able to run an analysis and inspect:

- von Mises stress;
- displacement;
- actual factor of safety; and
- whether the calculated factor of safety satisfies the user-required factor of safety.

For the initial ductile-material criterion:

`actual FoS = yield strength / relevant von Mises stress`

A design must not be labeled as passing merely because `actual FoS > 1`. It satisfies the configured requirement only when:

`actual FoS >= required FoS`

### FR4 — Evidence-based review

Users should receive an evidence-based summary of critical findings, likely engineering causes, and possible design improvements. AI must consume deterministic engineering evidence; it must not infer technical truth from rendered simulation images.

## Non-functional requirements

### NFR1 — Correctness and reproducibility

Analysis results must be physically credible, reproducible, and validated against known benchmarks before performance or scale is prioritized. Correctness comes before performance; evidence comes before AI.

### NFR2 — Non-blocking execution

Long-running analyses must not block the future UI. Planned analysis stages are:

1. preparing geometry;
2. generating mesh;
3. applying loads;
4. solving;
5. post-processing; and
6. generating review.

This asynchronous infrastructure is not part of the Engineering Spike.

### NFR3 — Data isolation

CAD models, analysis inputs, and results must be isolated per user or organization and inaccessible to unauthorized users. Future artifact storage must be private and accessed through authorization controls.

## Core domain model

`User -> Model -> ModelVersion -> Analysis -> Load(s) / BoundaryCondition(s) / Material snapshot -> AnalysisResult -> AIReview`

The loads, boundary conditions, and material snapshot belong to the analysis configuration. `AnalysisResult` records deterministic output; `AIReview` is downstream of that evidence.

## Domain invariants

- Changed geometry creates a new `ModelVersion`.
- Analysis configuration may be changed while an analysis has `draft` status.
- Once execution begins, the engineering configuration is immutable.
- Changing an analysis configuration after execution has begun requires a new `Analysis`.
- Completed analyses are immutable historical records.
- An executed `Analysis` preserves an immutable snapshot of every engineering input necessary to interpret and reproduce its result, as detailed in [engineering-assumptions.md](engineering-assumptions.md).

## Planned analysis lifecycle

`draft -> queued -> running -> completed | failed | canceled`

- `failed` means a system, solver, or process failure.
- `canceled` means intentional user cancellation.
- Cancellation wins over late solver completion.
- At-least-once job delivery may be used in the future. Workers must therefore be idempotent, and persisted state transitions must prevent duplicate finalization.

The lifecycle infrastructure is a future requirement and must not be implemented during the current milestone.

## Current milestone: Engineering Spike

The current milestone is only to prove this deterministic pipeline:

`STEP -> Gmsh -> tetrahedral mesh -> CalculiX -> solver output -> Python parser -> structured engineering result`

The completed spike should produce at least maximum relevant stress and maximum displacement and demonstrate the pipeline on a known engineering case.

The current milestone excludes:

- Next.js application development
- database work
- authentication
- cloud deployment
- queues
- server-sent events (SSE)
- AI review
- distributed architecture
- production infrastructure
- design-for-manufacturability (DFM) functionality
