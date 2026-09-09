# Verification and Validation Strategy

## Distinct questions

Verification and validation are related but separate activities:

- **Verification:** Are the mathematical/model equations being solved correctly? This includes checking geometry transfer, meshing, load and boundary-condition translation, solver execution, result parsing, calculations, and numerical convergence.
- **Validation:** Does the engineering model adequately represent the physical system for its intended use? This includes assessing whether the selected physics, material idealization, boundary conditions, loads, and result interpretation are appropriate.

A solver run that completes is not proof of either. Passing verification does not by itself validate the physical model.

## Strategy for the Engineering Spike

The spike must demonstrate the complete deterministic path from STEP input to a structured result on a known engineering case. Evidence should be retained at each boundary:

1. confirm that the intended STEP geometry is imported with expected dimensions and topology;
2. record mesh settings and mesh statistics;
3. confirm that loads, boundary conditions, material properties, units, and coordinate conventions are translated as intended;
4. retain the generated CalculiX input and relevant solver logs/output;
5. independently check parser extraction and derived calculations such as FoS;
6. compare FEA displacement and stress quantities with analytical or other authoritative reference values; and
7. repeat at multiple mesh resolutions to assess convergence and identify mesh-sensitive quantities.

Testing should isolate deterministic pipeline modules where practical and include an end-to-end benchmark. Failures in geometry preparation, meshing, solver execution, parsing, or required result availability must be distinguishable from an engineering requirement result that does not pass.

## Initial benchmark candidates

The planned initial suite contains:

- a cantilever beam;
- an axially loaded bar; and
- a simple bending or torsion benchmark.

The first spike needs to demonstrate a known engineering case; the exact first benchmark has not yet been selected.

For every analytical benchmark, record:

- benchmark geometry and units;
- material properties;
- loads and boundary conditions;
- model assumptions and applicable analytical theory;
- analytical or reference result and its source/derivation;
- FEA result;
- percent error;
- mesh configuration and resolution;
- mesher and solver versions;
- convergence behavior across mesh refinements; and
- any discrepancy, singularity, or applicability notes.

Unless a benchmark defines otherwise, percent error should be explicitly calculated and labeled relative to its reference value. Acceptance tolerances have not yet been selected and must be justified per measured quantity before a benchmark is treated as passing.

## Mesh convergence and stress interpretation

Mesh configuration is part of analysis provenance, not an incidental implementation detail. Convergence studies should compare like-for-like quantities across systematic refinements and record degrees of freedom or other useful mesh-size measures.

Displacement and stress may converge differently. Stress near a point load, sharp re-entrant corner, or perfectly fixed boundary can be singular and may continue increasing as the mesh is refined. Maximum nodal stress must not automatically be treated as a meaningful validation target in such regions. Where singularities may exist, use appropriately located or averaged comparison quantities supported by the benchmark theory, document the choice, and report the limitation.

A sophisticated automated singularity detector is not required for V1.

## Evidence and repeatability

Benchmark outputs must be traceable to immutable inputs and provenance described in [engineering-assumptions.md](engineering-assumptions.md). A repeat run with identical recorded inputs and compatible tools should produce materially consistent structured results. Any allowed numerical tolerance, platform variability, or version sensitivity must be measured and documented rather than silently accepted.

Validation evidence should precede performance optimization, scaling work, or AI-generated review features.

## Current CAD-to-mesh check

The first executable check generates a deterministic cantilever STEP fixture and then re-imports it in a separate Gmsh/OpenCASCADE operation. It verifies one solid volume, exactly one surface at each expected end location, named physical groups for the beam and both end faces, a nonempty tetrahedral mesh, and exported mesh artifacts. This verifies the CAD-to-mesh translation mechanics only; it does not yet verify a CalculiX solve, result parsing, analytical agreement, or mesh convergence.

## First CalculiX solve sanity check

The next spike check requires Gmsh type-11 volume connectivity, converts and verifies it as CalculiX `C3D10`, and performs a linear-static solve. Before execution it confirms that the fixed/load mesh groups lie on their expected X coordinates, every six-node load triangle maps to one C3D10 face, the surface area integrates to 0.0025 m², and the consistent negative-Z nodal loads sum to -1000 N.

The post-solve inspection is deliberately narrow: CalculiX must report job completion, displacement must be nonzero and directed consistently with the load, maximum displacement must occur at the free end, stress output must exist, and fixed-support Z reaction must balance the applied Z load. These are pipeline and basic structural-sanity checks, not formal verification. The first quantitative analytical comparison will primarily use tip displacement. Fixed-boundary local stresses are not treated as proof of correctness; their concentration or singularity behavior, C3D4-versus-C3D10 behavior, and mesh convergence remain future verification work.

## Cantilever analytical displacement benchmark

The first quantitative verification benchmark uses the frozen 1.0 m by 0.05 m by 0.05 m cantilever, `E = 200e9 Pa`, `nu = 0.30`, the fully fixed `x = 0` face, C3D10 elements, and the existing uniform end-face traction with a 1000 N resultant in global negative Z. The Euler-Bernoulli reference is:

`I = b h^3 / 12`

`delta_tip = F L^3 / (3 E I)`

For the frozen inputs, `I = 5.208333333333335e-7 m^4` and the analytical tip-displacement magnitude is `0.0032 m`.

The numerical quantity of interest is not the maximum displacement magnitude in the mesh. It is signed global `UZ` at the centroid of the free-end cross-section, `(1.0, 0.025, 0.025) m`, corresponding to the Euler-Bernoulli centroidal axis. The current mesh has no node at that coordinate. The value is therefore evaluated using the quadratic shape functions of the six-node load-face triangle containing the point. For the current mesh, surface element 86 uniquely contains the centroid and gives `UZ = -0.0031934983433094745 m`.

The observed magnitude comparison is:

- absolute error: `6.501656690524778e-6 m`;
- relative error: `0.0020317677157889935`; and
- percent error: `0.20317677157889935%`.

The negative FEA sign is consistent with the applied negative-Z load. This result establishes a reproducible comparison of the selected displacement quantity through the current STEP-to-CalculiX pipeline. No formal acceptance threshold has been set. One mesh result alone is not a convergence study and cannot establish discretization independence. Euler-Bernoulli theory is itself an idealized beam model, while the FEA model is a three-dimensional solid with fixed-end local effects. This comparison does not verify stress results. C3D4-versus-C3D10 comparison remains later work.

## C3D10 displacement mesh-convergence study

The displacement convergence experiment changes only the uniform Gmsh characteristic mesh size. STEP geometry, material, 1000 N negative-Z resultant, consistent uniform-traction load mapping, fully fixed end, C3D10 formulation, CalculiX linear-static settings, and the free-end centroid `UZ` quantity remain fixed. Mesh sizes follow a preselected geometric refinement factor of `sqrt(2)`:

- coarse: `0.025 m`;
- medium: `0.017677669529663688 m`;
- fine: `0.0125 m`, preserving the original successful mesh; and
- finer: `0.008838834764831844 m`.

This progression uses a fixed geometric characteristic-length refinement ratio of `sqrt(2)`. It was selected around the existing four-elements-across-section target before evaluating convergence results, not tuned to improve agreement.

| Level | Nodes | C3D10 elements | Centroid UZ (m) | Analytical error | Change from previous |
| --- | ---: | ---: | ---: | ---: | ---: |
| coarse | 1,851 | 804 | -0.003191318918668 | 0.271283792% | — |
| medium | 5,866 | 2,883 | -0.003192322994337 | 0.239906427% | 0.031462718% |
| fine | 13,218 | 7,242 | -0.003193498343309 | 0.203176772% | 0.036817984% |
| finer | 31,742 | 18,925 | -0.003194353096108 | 0.176465747% | 0.026765406% |

Successive relative change is the absolute change in signed centroid `UZ` divided by the previous mesh's displacement magnitude. The medium centroid lay on a shared face edge, producing two interpolation candidates; their quadratic interpolations agreed within the recorded numerical tolerance. All other levels had one containing face candidate. For every level, the integrated applied load was approximately `-1000 N` in Z, the fixed reaction was `+1000 N` in Z, displacement had the expected negative sign, and no CalculiX warning was reported.

The displacement sequence appears to be stabilizing: changes between successive meshes are roughly three hundredths of one percent, and analytical difference decreases across these four levels. The successive-change magnitude is not strictly monotonic, however, and no formal convergence or acceptance threshold has been defined. Analytical error and successive-refinement change answer different questions: the former compares the 3D model with Euler-Bernoulli beam theory, while the latter measures sensitivity to discretization within the 3D model.

The converged three-dimensional elasticity solution is not required to equal exactly `3.200000 mm`, because it and Euler-Bernoulli theory are not mathematically identical models. This study addresses displacement only. It does not establish stress convergence, resolve fixed-boundary stress effects, verify C3D4 behavior, or generally verify the solver or project.
