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

Resolved `MeshConfig` currently supports the verified C3D10 path and records its derived quadratic order, characteristic size, and Gmsh/OpenCASCADE version. `SolverConfig` records CalculiX version, linear-static analysis, small-deformation behavior, and requested result quantities. CAD, generated mesh, solver input, result and artifact checksums, runtime, and worker/environment details remain runtime/result provenance rather than engineering definition fields.

The axial bar is the sole initial consumer because its uniform force, fully fixed face, material, and C3D10 configuration have the least ambiguous mapping. Its geometry generation, consistent surface-load mapping, CalculiX execution, parsing, and analytical verification remain benchmark-specific. Cantilever and torsion migration is intentionally deferred.

### First CalculiX adapter boundary

The axial path now implements the dependency direction `engineering domain -> geometry/mesh resolution -> CalculiX adapter -> solver deck`. `run_axial_bar_solve.py` remains the orchestration and benchmark-specific resolution layer: it maps the axial definition's named `axial_bar`, `fixed`, and `axial_load` selections to the physical-tag contract in the generated mesh, verifies their geometric locations, and obtains volume elements, surface faces, and node IDs. These resolved IDs are never written back into domain objects and are not treated as engineering identity.

`calculix_adapter.py` is the sole new owner of CalculiX DOF numbers and the axial deck syntax. It maps UX/UY/UZ to DOFs 1/2/3, emits the linear-isotropic `*MATERIAL`/`*ELASTIC` representation, combines contiguous constrained DOFs into `*BOUNDARY` rows, translates resolved nodal force vectors into `*CLOAD` rows, and renders the established axial C3D10 linear-static deck and output cards. It rejects solver identifiers, analysis modes, element formulations, load shapes, and output-request sets outside the one proven path. It is a concrete adapter, not an abstract solver interface or universal CalculiX syntax tree.

The C3D10 consistent surface-force integration lives separately in `surface_load_mapping.py`. Architecturally it is geometry/mesh numerical mapping: it converts uniform traction over quadratic boundary faces into equivalent nodal force vectors using element shape functions. That mapping depends on the finite-element interpolation and resolved face, but not on CalculiX keywords or DOF numbering; a future solver that accepts equivalent nodal loads could reuse it. The axial benchmark still chooses the uniform-traction interpretation and expected face area, so those choices remain benchmark-specific. The CalculiX adapter begins only when it converts the resulting physical nodal vectors to solver DOFs and deck rows.

### First CalculiX output boundary

The axial path now completes the reverse boundary without collapsing raw numerical data into engineering evidence:

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

`calculix_results.py` alone owns the supported DAT headers, row formats, component ordering, and fixed-set result markers. It parses nodal displacements, individual nodal reactions, the printed reaction resultant, and raw integration-point Cauchy stresses. Missing or truncated required records are errors; components are never inferred or replaced. The current axial deck writes all authoritative verification quantities to DAT. FRD remains a retained solver artifact, but its extrapolated/averaged stress representation is not used by the axial verification and no FRD parser is introduced in this slice.

`numerical_results.py` contains frozen, solver-neutral `Vector3`, `NodalDisplacement`, `NodalReaction`, `StressTensor`, `IntegrationPointStress`, and `NumericalResult` values. Tuple collections are copied, deterministically sorted, and checked for duplicate numerical identities. Field names make SI units explicit: displacement and optional location in metres, reaction in newtons, and stress in pascals. Node, element, and integration-point IDs are valid coordinates of a completed numerical solution; this does not permit engineering loads or constraints to use mesh IDs as identity.

`engineering_postprocessing.py` begins the downstream deterministic layer with vector magnitude and the three-dimensional stress-tensor-to-von-Mises invariant. Von Mises is derived rather than stored as raw solver output. It is not automatically reduced to a global peak, a relevant design stress, factor of safety, or pass/fail decision. Those require an explicit critical-region and stress-selection policy, particularly around constrained supports and other mesh-sensitive regions.

Mesh-dependent reconstruction remains outside text parsing. The axial free-end centroid interpolation uses the existing C3D10 quadratic surface representation, and integration-point coordinates are reconstructed from the verified mesh connectivity and quadrature identity. These operations are numerical/mesh post-processing that could apply to another solver using the same representation, but they remain narrowly located in benchmark infrastructure rather than becoming a generic FEM library. Selection of the `x = 0.5 m` patch, comparison with `F/A`, strain reconstruction and Poisson references, equilibrium interpretation, and support-band diagnostics remain axial-benchmark verification.

Full runtime provenance also remains separate. Existing artifacts retain CAD, mesh, input, DAT/FRD and software evidence; the numerical snapshot itself contains physical result values and numerical identities, not checksums, worker identity, timestamps, or analytical references. This is not a general multi-solver result framework. Cantilever and torsion continue using their established parsing paths.

### First engineering-evidence and AnalysisResult boundary

The axial benchmark now proves the complete first Engineering Core dependency chain:

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

The serializer emits stable JSON-compatible fields with explicit SI-unit names and no solver-file syntax. Local artifact references are deferred: paths are not durable engineering identity, while checksums and broader execution provenance already remain in the benchmark artifact. A future artifact model can link the compact result to detailed numerical fields without embedding those fields or inventing storage IDs now.

Production factor of safety and pass/fail remain deferred because the relevant-stress selection policy is unresolved. In particular, the axial global peak lies in the perturbed support region and is not substituted for the predeclared interior analytical comparison. Selection of that interior region, `F/A` and strain references, free-end centroid interpolation, support diagnostics, and analytical interpretation remain benchmark-specific. Future UI, reports, persistence, regression tools, and AI review may consume `AnalysisResult`; AI remains downstream and cannot alter deterministic evidence or determine acceptance.

### Spike constraints

The spike does not include a web UI, API product surface, database, authentication, cloud deployment, durable queue, SSE, AI review, distributed execution, production infrastructure, or DFM functionality. It must not introduce a custom finite element solver or speculative infrastructure.

No production package layout, command-line interface, persistence schema, artifact-storage format, or process-orchestration design has yet been selected. Those choices should be made only when implementing the relevant milestone and should remain as simple as its demonstrated requirements allow.

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
