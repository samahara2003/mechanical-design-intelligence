# Architecture

This document separates the architecture of the current Engineering Spike from the planned future platform. Future components are directional constraints, not authorization to implement them now.

## 1. Engineering Spike architecture

The spike is a local, deterministic engineering pipeline:

```text
STEP model
    |
    v
Gmsh with OpenCASCADE
(geometry import and tetrahedral meshing)
    |
    v
CalculiX input preparation
    |
    v
CalculiX solve
    |
    v
Solver output
    |
    v
Python parser and post-processing
    |
    v
Structured engineering result
```

The structured result must eventually contain, at minimum:

- maximum relevant stress; and
- maximum displacement.

The spike should be demonstrated using a known engineering case with recorded validation evidence. Python modules should be deterministic and testable. Inputs, intermediate artifacts, configuration, tool versions, and outputs must remain traceable enough to reproduce and interpret the result.

The initial CAD-to-mesh implementation uses Python standard-library orchestration to invoke the installed Gmsh command-line executable through `subprocess`. Gmsh is discovered through `PATH`; no machine-specific executable path is part of the workflow. The Gmsh Python API is deferred to avoid introducing Python-binding compatibility and packaging concerns before they provide a demonstrated benefit.

STEP remains the actual CAD input boundary. The cantilever benchmark STEP file may be created deterministically as a test fixture, but meshing occurs in a separate operation that imports that STEP through Gmsh/OpenCASCADE. Named physical groups (`beam`, `fixed`, and `load`) carry geometry semantics into mesh sets intended for downstream FEA input.

### Spike constraints

The spike does not include a web UI, API product surface, database, authentication, cloud deployment, durable queue, SSE, AI review, distributed execution, production infrastructure, or DFM functionality. It must not introduce a custom finite element solver or speculative infrastructure.

No internal package layout, command-line interface, result schema, artifact format, or process-orchestration design has yet been selected. Those choices should be made only when implementing the spike and should remain as simple as its demonstrated requirements allow.

## 2. Planned future system architecture

The intended conceptual flow is:

```text
Browser
    |
    v
Application API
    |---------------------------> PostgreSQL
    |                             metadata, state, queryable result summaries
    v
Durable job queue
    |
    v
Engineering workers
    |
    +--> Gmsh / OpenCASCADE
    +--> CalculiX
    +--> Python post-processing
    |
    +---------------------------> PostgreSQL
    +---------------------------> Private object storage
                                      CAD and simulation artifacts
    |
    v
Deterministic engineering evidence
    |
    v
AI review
```

The future browser application will use Next.js/TypeScript, with Three.js/React Three Fiber for 3D visualization. PostgreSQL will hold relational metadata, workflow state, and queryable result summaries. Large CAD and simulation artifacts will reside in private object storage.

Large uploads and artifact downloads should use direct transfers to private object storage through short-lived signed URLs, subject to application authorization. CAD models, inputs, and results must be isolated by user or organization.

Long-running work must not block the UI. The future execution flow will account for geometry preparation, mesh generation, load application, solving, post-processing, and review generation. If jobs use at-least-once delivery, workers must be idempotent and persisted state transitions must prevent duplicate finalization. Cancellation must take precedence over a late successful solver result.

AI review is strictly downstream of deterministic results and provenance. It explains evidence; it does not infer technical truth from rendered images and does not decide pass/fail.

## Decisions intentionally deferred

No vendor or product has been selected for:

- the durable queue;
- cloud workers or compute;
- private object storage;
- Redis or any caching layer; or
- deployment infrastructure.

The project has not committed to microservices or other distributed patterns beyond the conceptual future need for non-blocking engineering work. Such decisions require a demonstrated milestone need.
