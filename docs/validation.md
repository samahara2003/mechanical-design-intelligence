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
