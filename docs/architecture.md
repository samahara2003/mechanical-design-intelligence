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

The first solve milestone extends this local flow with a second standard-library Python process that prepares a minimal CalculiX deck and invokes `ccx` from `PATH` through `subprocess`. This is spike orchestration, not a production result parser or job-execution architecture. The deck is derived from the Gmsh physical groups rather than arbitrary geometry entity identifiers.

### Engineering domain boundary

The first Engineering Core slice introduces small immutable Python value objects that separate four concerns:

1. engineering intent: model-version reference, material snapshot, geometry-targeted loads, and geometry-targeted boundary conditions;
2. resolved numerical configuration: the actual mesh size, element formulation, mesher/version, solver/version, linear-static setting, and result requests;
3. solver representation: physical tags, mesh node and element sets, CalculiX DOF numbers, equivalent nodal loads, input keywords, and output-file syntax; and
4. deterministic results and runtime provenance.

Only the first concern and the minimum reproducible part of the second are modeled here. This is not a general solver execution framework, persistence model, draft workflow, or state machine.

An `AnalysisDefinition` is a frozen executed-analysis value containing a `ModelVersionReference`, `MaterialSnapshot`, force or pressure intent, boundary conditions, `MeshConfig`, `SolverConfig`, and optional required factor of safety. Its nested collections are tuples and its nested values are themselves frozen. Mutable draft state is an application-layer concern intentionally deferred. Once execution begins, an engineering change requires a new definition and, when persistence exists, a new Analysis.

Material properties are copied into `MaterialSnapshot`; an executed analysis therefore does not depend only on a mutable library key. Current fields are name, Young's modulus, Poisson ratio, optional density, optional yield strength, and an optional immutable source reference. Values use SI units: Pa, N, m, and kg/m^3 as indicated by field names. A full units library is not introduced.

`GeometrySelection` currently identifies a named face or volume region such as `fixed`, `load`, `axial_load`, or `axial_bar`. Loads and constraints refer to this geometry-level intent, never mesh node IDs. Gmsh/CalculiX adapters remain responsible for resolving names into physical tags, faces, nodes, elements, and solver DOFs. Named regions are not robust CAD topological naming: upstream fixture/import logic must still create and validate them, and topology tracking across arbitrary CAD revisions remains deferred.

A force is represented as a positive magnitude in newtons plus a normalized three-component direction and a target face. This keeps engineering intent distinct from the benchmark-specific consistent nodal-force integration. Pressure is a positive magnitude in pascals plus an explicit inward/outward selected-face normal convention. Torque is not yet a production load type: the square-bar benchmark's distributed resultant-equivalent traction remains benchmark-specific evidence until a production torque requirement and mapping semantics are defined.

Resolved `MeshConfig` currently supports the verified C3D10 path and records its derived quadratic order, characteristic size, and Gmsh/OpenCASCADE version. `SolverConfig` records CalculiX version, linear-static analysis, small-deformation behavior, and requested result quantities. CAD, generated mesh, solver input, result and artifact checksums remain execution provenance rather than engineering definition fields. Runtime duration and broader worker/environment details are still deferred.

The axial bar was the initial consumer because its uniform force, fully fixed face, material, and C3D10 configuration had the least ambiguous mapping. The C3D10 cantilever is now the second consumer, using the same domain, mapping, adapter, parser, numerical-result, and evidence boundaries for a transverse force and bending-dominated response. Geometry generation and analytical verification remain benchmark-specific. Torsion migration is intentionally deferred.

### First CalculiX adapter boundary

The axial and C3D10 cantilever paths implement the dependency direction `engineering domain -> geometry/mesh resolution -> CalculiX adapter -> solver deck`. Their solve scripts remain orchestration and benchmark-specific resolution layers: named geometry selections are mapped to each generated mesh's physical-tag contract, geometric locations are verified, and volume elements, surface faces, and node IDs are resolved. These IDs are never written back into domain objects and are not treated as engineering identity.

`calculix_adapter.py` owns CalculiX DOF numbers and the proven C3D10 linear-static deck syntax. It maps UX/UY/UZ to DOFs 1/2/3, emits the linear-isotropic `*MATERIAL`/`*ELASTIC` representation, combines contiguous constrained DOFs into `*BOUNDARY` rows, translates resolved nodal force vectors into `*CLOAD` rows, and renders the established output cards. Its volume/set names and heading are explicit resolved inputs because axial and cantilever use different physical-group contracts; the axial compatibility wrapper preserves its established deck byte-for-byte. The adapter still rejects solver identifiers, analysis modes, formulations, load shapes, and output requests outside the two proven C3D10 force cases. It is not an abstract solver interface or universal CalculiX syntax tree. The C3D4 comparison retains its established benchmark-local deck path.

The C3D10 consistent surface-force integration lives separately in `surface_load_mapping.py`. Architecturally it is geometry/mesh numerical mapping: it converts uniform traction over quadratic boundary faces into equivalent nodal force vectors using element shape functions. The same unchanged function now maps both axial `+X` and cantilever transverse `-Z` force intent, demonstrating that it is not tied to face-normal pressure or one load axis. The mapping depends on finite-element interpolation and the resolved face, not CalculiX keywords or DOF numbering. Each benchmark still chooses its traction interpretation and expected face area. The CalculiX adapter begins only when it converts physical nodal vectors to solver DOFs and deck rows.

### First CalculiX output boundary

The axial and C3D10 cantilever paths complete the reverse boundary without collapsing raw numerical data into engineering evidence:

```text
Engineering Definition
        |
        v
CalculiX Input Adapter
        |
        v
     CalculiX
        |
        v
CalculiX Result Parser
        |
        v
Solver-Neutral Numerical Result
        |
        v
Engineering Post-Processing
        |
        v
Benchmark Evidence / future AnalysisResult
```

`calculix_results.py` alone owns the supported DAT headers, row formats, component ordering, and fixed-set result markers. The same unchanged parsing functions now parse axial and cantilever nodal displacements, individual reactions, printed reaction resultants, and raw integration-point Cauchy stresses. Missing or truncated required records are errors; components are never inferred or replaced. DAT is authoritative for these quantities. FRD remains a retained solver artifact, but its extrapolated/averaged stress representation is not used by either reusable result path and no FRD parser is introduced.

`numerical_results.py` contains frozen, solver-neutral `Vector3`, `NodalDisplacement`, `NodalReaction`, `StressTensor`, `IntegrationPointStress`, and `NumericalResult` values. Tuple collections are copied, deterministically sorted, and checked for duplicate numerical identities. Field names make SI units explicit: displacement and optional location in metres, reaction in newtons, and stress in pascals. Node, element, and integration-point IDs are valid coordinates of a completed numerical solution; this does not permit engineering loads or constraints to use mesh IDs as identity.

`engineering_postprocessing.py` begins the downstream deterministic layer with vector magnitude and the three-dimensional stress-tensor-to-von-Mises invariant. Von Mises is derived rather than stored as raw solver output. It is not automatically reduced to a global peak, a relevant design stress, factor of safety, or pass/fail decision. Those require an explicit critical-region and stress-selection policy, particularly around constrained supports and other mesh-sensitive regions.

Mesh-dependent reconstruction remains outside text parsing. The axial free-end centroid interpolation uses the existing C3D10 quadratic surface representation, and integration-point coordinates are reconstructed from the verified mesh connectivity and quadrature identity. These operations are numerical/mesh post-processing that could apply to another solver using the same representation, but they remain narrowly located in benchmark infrastructure rather than becoming a generic FEM library. Selection of the `x = 0.5 m` patch, comparison with `F/A`, strain reconstruction and Poisson references, equilibrium interpretation, and support-band diagnostics remain axial-benchmark verification.

Execution provenance remains separate from the numerical snapshot. `AnalysisProvenance` retains CAD, mesh, input, DAT/FRD content identities and actual Gmsh/CalculiX versions; `NumericalResult` contains physical result values and numerical identities, not checksums, worker identity, timestamps, or analytical references. This is not a general multi-solver result framework. Torsion continues using its established parsing path.

### Analysis execution provenance

The axial and fine C3D10 cantilever executions now preserve this relationship:

```text
AnalysisDefinition -> AnalysisProvenance + NumericalResult -> AnalysisResult
```

`analysis_provenance.py` fingerprints the complete existing `AnalysisDefinition` serializer as canonical JSON: keys are sorted, separators contain no insignificant whitespace, text is UTF-8, and SHA-256 supplies the stable identity. It does not use Python `hash()` or object representations. A material, load, boundary-condition, mesh, solver, or model-version change therefore changes the definition fingerprint.

`ArtifactProvenance` supports only the five artifacts proven here: STEP, mesh, CalculiX input, DAT, and FRD. Each record carries its artifact role, streaming SHA-256, optional byte size, and optional local path. The checksum identifies exact bytes. A local path is debug/execution metadata, is excluded from value equality, and can be omitted from the serialized content-identity view; it is not a durable storage identity. This is intentionally not a generic artifact framework.

`AnalysisProvenance` links the model-version reference and definition fingerprint to those artifacts, actual Gmsh and CalculiX versions, and the stable `engineering-core-analysis-result/1` deterministic post-processing contract. OpenCASCADE version is not separately asserted because the current execution path does not capture it as an independent reliable tool fact. Git state, commit identity, timestamps, storage IDs, and worker details are not runtime requirements.

Execution history is append-only in concept even though persistence is not implemented. A rerun under a changed solver/mesher version or configuration creates new execution provenance and does not rewrite the earlier engineering history. Byte reproducibility is stricter than numerical reproducibility: timestamp-bearing STEP or FRD files may have different checksums while producing the same mesh and parsed numerical evidence. Such differences are recorded, not normalized away.

### First engineering-evidence and AnalysisResult boundary

The axial and cantilever benchmarks now prove the complete first Engineering Core dependency chain:

```text
AnalysisDefinition
        |
        v
Solver Input Adapter
        |
        v
     CalculiX
        |
        v
Solver Output Parser
        |
        v
 NumericalResult
        |
        v
Engineering Evidence Builder
        |
        v
  AnalysisResult
        |
        v
future UI / reports / AI review
```

`NumericalResult` remains the detailed numerical snapshot: thousands of nodal vectors and raw integration-point tensors with numerical identities. `AnalysisResult` is a compact, immutable, machine-readable summary and does not duplicate those fields. It records the model-version reference, resolved mesh counts/configuration, the global maximum-displacement diagnostic, force-equilibrium evidence, the global raw integration-point von Mises diagnostic, and objective warnings. The builder consumes `AnalysisDefinition`, `NumericalResult`, and a small solver-neutral `ResolvedAnalysisContext`; it has no dependency on CalculiX syntax or parser classes.

The resolved context records actual node/element counts and the integrated applied resultant produced by the surface-load mapping. This permits equilibrium to compare the force actually transferred to the numerical model with the parsed support resultant, rather than assuming the requested load was mapped exactly. Evidence exposes `applied + reaction`, its magnitude, and a relative imbalance normalized by the larger resultant magnitude. It embeds no equilibrium tolerance or boolean decision.

Maximum displacement is the exact largest parsed nodal-vector magnitude, with the original vector, node identity, and physical node location when supplied by mesh reconstruction. Exact magnitude ties select the lowest node ID. This global statistic is useful for search and reporting but does not replace a benchmark-specific geometric QoI such as the axial free-face centroid interpolation.

The stress summary selects the exact largest von Mises value calculated from raw, unaveraged integration-point Cauchy tensors. It retains element/IP identity, original tensor, and reconstructed physical location when available. Exact scalar ties select the lowest element ID and then lowest integration-point identity. Its deliberately explicit name, `global_raw_max_von_mises`, identifies it as a numerical diagnostic—not `critical_stress`, relevant design stress, factor of safety, or acceptance result. No smoothing, nodal extrapolation, or averaging is introduced.

The result serializer emits stable JSON-compatible fields with explicit SI-unit names and no solver-file syntax. `AnalysisProvenance` links the compact result's execution to exact artifacts without embedding raw numerical fields or inventing storage IDs. Local paths may aid debugging but are not durable engineering identity.

Production factor of safety and pass/fail remain deferred because the relevant-stress selection policy is unresolved. In both current consumers, the global peak lies in the perturbed support region. It is not substituted for the axial interior analytical comparison or the cantilever's predeclared `x = 0.2 m` reconstructed beam-stress QoI. Axial `F/A`/strain references and cantilever Euler-Bernoulli displacement/section reconstruction remain benchmark-specific. Future UI, reports, persistence, regression tools, and AI review may consume `AnalysisResult`; AI remains downstream and cannot alter deterministic evidence or determine acceptance.

### Spike constraints

The spike does not include a web UI, API product surface, database, authentication, cloud deployment, durable queue, SSE, AI review, distributed execution, production infrastructure, or DFM functionality. It must not introduce a custom finite element solver or speculative infrastructure.

No production package layout, command-line interface, persistence schema, artifact-storage format, or process-orchestration design has yet been selected. Those choices should be made only when implementing the relevant milestone and should remain as simple as its demonstrated requirements allow.

## 2. Web application V0 persistence foundation

The Web App V0 is contained in `web/` and uses the Next.js App Router, React, TypeScript, Drizzle ORM, and PostgreSQL. It provides application/domain persistence for `Model`, immutable `ModelVersion`, lifecycle-controlled `Analysis`, and one immutable final `AnalysisResult` per analysis. It also supports the first private STEP-ingestion boundary. The later local Worker V0 slice below connects that persistence to the controlled bracket Engineering Core path; SSE, authentication, AI, and visualization remain absent.

`ModelVersion` records one exact geometry revision through a parent model, positive version number, original filename, upload SHA-256, byte size, and its private object key. A database trigger rejects every update to a model-version row; changed geometry therefore requires a new row. The upload hash supports application integrity only. A future Engineering Worker must recompute SHA-256 from the exact downloaded bytes before treating it as execution provenance.

STEP ingestion accepts only nonempty `.step`/`.stp` files up to 100 MiB. The browser hashes the file, obtains a five-minute presigned PUT, and sends bytes directly to a private Cloudflare R2 bucket; Next.js never proxies the STEP body. Object-storage configuration is isolated behind a small S3-compatible adapter. Keys contain only server-created IDs (`models/{modelId}/versions/{modelVersionId}/source.step`), never the original filename, and signed conditional PUTs reject overwrites.

Upload intent is staged in `model_version_uploads`, not represented as a ready geometry revision. Confirmation checks R2 object existence, exact size, and signed metadata, then creates the immutable `ModelVersion` and removes the staging row in one PostgreSQL transaction. Expired or abandoned intents remain incomplete and require a later cleanup policy. This flow does not inspect STEP syntax, process CAD, authorize users, or execute analyses.

`Analysis.engineering_definition` is PostgreSQL `jsonb` with an explicit TypeScript contract matching the keys and meaning emitted by Python `analysis_definition_to_dict`: SI unit system, model-version reference, snapshotted isotropic material, geometry-selected force/pressure loads, geometry-selected translational constraints, resolved C3D10 mesh configuration, CalculiX configuration, and optional required FoS. It never contains mesh node or element IDs. TypeScript validation mirrors the obvious Python domain constraints, but Python remains the engineering authority. The web application can calculate a deterministic draft fingerprint for change detection; because Python and JavaScript number-to-JSON formatting is not assumed byte-identical, only the Python Engineering Core fingerprint is accepted as the authoritative execution fingerprint.

The implemented lifecycle is `draft -> queued`, `queued -> running|canceled`, and `running -> completed|failed|canceled`. Completed, failed, and canceled states are terminal. Application functions validate transitions and use expected-status predicates so concurrent stale mutations fail. A draft edit clears its prior fingerprint. Queueing atomically records the authoritative fingerprint and then performs a conditional transition; leaving draft records `execution_started_at`. PostgreSQL checks and an update trigger independently enforce the same transition graph and prevent model-version, definition, or fingerprint changes while leaving or after leaving draft.

`analysis_results` contains only the compact reusable result summary and provenance summary as typed `jsonb`; full nodal and integration-point fields remain outside PostgreSQL. `analysis_id` is its primary key, enforcing one final result per analysis. Insert is permitted only for a completed analysis, and an update trigger makes the final row immutable. Creation of a changed configuration after execution is represented by a new draft `Analysis`, never an edit to execution history.

The checked-in SQL migrations contain constraints and triggers that Drizzle's table declaration cannot express alone. A configured PostgreSQL instance is still required before applying them. User ownership and deployment topology remain deferred.

### Engineering Worker V0

PostgreSQL is the only V0 durable queue. Each Analysis has at most one `analysis_jobs` row. Application enqueue validates that the persisted draft still matches the definition fingerprinted by the Python Engineering Core, then stores that fingerprint, creates the job, and transitions `draft -> queued` in one database transaction. There is no Redis, Celery, broker, or duplicated product lifecycle.

The Python worker claims one queued job, or one expired claim, using `FOR UPDATE SKIP LOCKED`. Each claim increments an attempt counter and receives a new UUID fencing token plus a 30-minute lease. Initial claim changes the Analysis from `queued` to `running` in the same transaction. A reclaimed Analysis is already running. Finalization requires the current claim token and a running Analysis; a stale worker or canceled/terminal Analysis cannot publish. The final transaction changes `running -> completed`, inserts the one immutable `AnalysisResult`, and marks the job finished atomically. Controlled failure stores only a bounded phase and exception class, changes a still-running Analysis to `failed`, and finishes its job.

Worker V0 is deliberately restricted to the proven baseline mounting bracket: named `bracket`, `mounting_holes`, and `load_pad` regions, its fixed material/load/boundary definition, C3D10 at `0.006 m`, and the configured Gmsh/CalculiX versions. Named regions are still recovered after STEP import with the existing dimensional checks. Arbitrary uploaded CAD and persistent topology identity remain unsupported.

The worker streams the private ModelVersion STEP from R2 and recomputes SHA-256 and byte size before Gmsh can run. This worker hash is compared with the upload integrity metadata and becomes provenance for the exact consumed STEP. Generated mesh, CalculiX input, DAT, and FRD are uploaded directly under immutable server-owned keys `analyses/{analysisId}/attempts/{claimToken}/{role}.{suffix}`. A reclaimed attempt therefore never overwrites or conflicts with earlier run-varying bytes. Only the successfully fenced attempt's exact keys and hashes enter persisted provenance. A stale attempt may leave private orphan objects; automated cleanup is deferred. The compact solver-neutral `AnalysisResult` and path-free `AnalysisProvenance` summary are stored in PostgreSQL, while full artifacts remain private in R2.

## 3. Planned future system architecture

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
