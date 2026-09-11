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

The post-solve inspection is deliberately narrow: CalculiX must report job completion, displacement must be nonzero and directed consistently with the load, maximum displacement must occur at the free end, stress output must exist, and fixed-support Z reaction must balance the applied Z load. These are pipeline and basic structural-sanity checks, not formal verification. The first quantitative analytical comparison primarily uses tip displacement. Fixed-boundary local stresses are not treated as proof of correctness; their concentration or singularity behavior remains future verification work.

## Cantilever analytical displacement benchmark

The first quantitative verification benchmark uses the frozen 1.0 m by 0.05 m by 0.05 m cantilever, `E = 200e9 Pa`, `nu = 0.30`, the fully fixed `x = 0` face, C3D10 elements, and the existing uniform end-face traction with a 1000 N resultant in global negative Z. The Euler-Bernoulli reference is:

`I = b h^3 / 12`

`delta_tip = F L^3 / (3 E I)`

For the frozen inputs, `I = 5.208333333333335e-7 m^4` and the analytical tip-displacement magnitude is `0.0032 m`.

The numerical quantity of interest is not the maximum displacement magnitude in the mesh. It is signed global `UZ` at the centroid of the free-end cross-section, `(1.0, 0.025, 0.025) m`, corresponding to the Euler-Bernoulli centroidal axis. The current mesh has no node at that coordinate. The value is therefore evaluated using the quadratic shape functions of the six-node load-face triangle containing the point. For the current mesh, surface element 86 uniquely contains the centroid and gives `UZ = -0.0031934983433094745 m`.

The observed magnitude disagreement with the Euler-Bernoulli reference is:

- absolute difference: `6.501656690524778e-6 m`;
- relative difference: `0.0020317677157889935`; and
- percent difference: `0.20317677157889935%`.

The negative FEA sign is consistent with the applied negative-Z load. This result establishes a reproducible comparison of the selected displacement quantity through the current STEP-to-CalculiX pipeline. No formal acceptance threshold has been set. One mesh result alone is not a convergence study and cannot establish discretization independence. Euler-Bernoulli theory is itself an idealized beam model, while the FEA model is a three-dimensional solid with fixed-end local effects. This comparison does not verify stress results.

## C3D10 displacement mesh-convergence study

The displacement convergence experiment changes only the uniform Gmsh characteristic mesh size. STEP geometry, material, 1000 N negative-Z resultant, consistent uniform-traction load mapping, fully fixed end, C3D10 formulation, CalculiX linear-static settings, and the free-end centroid `UZ` quantity remain fixed. Mesh sizes follow a preselected geometric refinement factor of `sqrt(2)`:

- coarse: `0.025 m`;
- medium: `0.017677669529663688 m`;
- fine: `0.0125 m`, preserving the original successful mesh; and
- finer: `0.008838834764831844 m`.

This progression uses a fixed geometric characteristic-length refinement ratio of `sqrt(2)`. It was selected around the existing four-elements-across-section target before evaluating convergence results, not tuned to improve agreement.

| Level | Nodes | C3D10 elements | Centroid UZ (m) | Euler-Bernoulli disagreement | Change from previous |
| --- | ---: | ---: | ---: | ---: | ---: |
| coarse | 1,851 | 804 | -0.003191318918668 | 0.271283792% | — |
| medium | 5,866 | 2,883 | -0.003192322994337 | 0.239906427% | 0.031462718% |
| fine | 13,218 | 7,242 | -0.003193498343309 | 0.203176772% | 0.036817984% |
| finer | 31,742 | 18,925 | -0.003194353096108 | 0.176465747% | 0.026765406% |

Successive relative change is the absolute change in signed centroid `UZ` divided by the previous mesh's displacement magnitude. The medium centroid lay on a shared face edge, producing two interpolation candidates; their quadratic interpolations agreed within the recorded numerical tolerance. All other levels had one containing face candidate. For every level, the integrated applied load was approximately `-1000 N` in Z, the fixed reaction was `+1000 N` in Z, displacement had the expected negative sign, and no CalculiX warning was reported.

The displacement sequence appears to be stabilizing: changes between successive meshes are roughly three hundredths of one percent, and analytical difference decreases across these four levels. The successive-change magnitude is not strictly monotonic, however, and no formal convergence or acceptance threshold has been defined. Euler-Bernoulli disagreement and successive-refinement change answer different questions: the former compares two non-identical mathematical models, while the latter measures sensitivity to discretization within the 3D model.

A limiting three-dimensional elasticity solution under further mesh refinement would not be required to equal exactly `3.200000 mm`, because it and Euler-Bernoulli theory are not mathematically identical models. This study addresses displacement only. It does not establish stress convergence, resolve fixed-boundary stress effects, or generally verify the solver or project.

## C3D4 versus C3D10 displacement study

This bending-dominated experiment examines element formulation/order behavior while leaving the physical benchmark unchanged. Both C3D4 and C3D10 use the same four characteristic sizes (`0.025`, `0.017677669529663688`, `0.0125`, and `0.008838834764831844 m`), STEP geometry, material, consistent 1000 N negative-Z traction resultant, fixed boundary, linear-static solver settings, analytical reference, and free-end centroid `UZ` quantity.

C3D4 uses three-node triangular boundary faces, equal one-third-area consistent nodal traction contributions, and linear face interpolation. C3D10 retains six-node faces, its established consistent nodal traction integration, and quadratic face interpolation. Gmsh's C3D4 CalculiX export is checked against MSH connectivity; the established C3D10 edge-node permutation remains unchanged.

| Formulation | Level | Nodes | Elements | Centroid UZ (m) | Euler-Bernoulli disagreement | Change from previous | Total runtime (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| C3D4 | coarse | 351 | 804 | -0.001587897076867 | 50.378216% | — | 0.914 |
| C3D4 | medium | 1,021 | 2,883 | -0.002315851268687 | 27.629648% | 45.843915% | 1.019 |
| C3D4 | fine | 2,131 | 7,242 | -0.002664575690394 | 16.732010% | 15.058153% | 1.436 |
| C3D4 | finer | 4,801 | 18,925 | -0.002905532394451 | 9.202113% | 9.042967% | 2.512 |
| C3D10 | coarse | 1,851 | 804 | -0.003191318918668 | 0.271284% | — | 1.061 |
| C3D10 | medium | 5,866 | 2,883 | -0.003192322994337 | 0.239906% | 0.031463% | 1.836 |
| C3D10 | fine | 13,218 | 7,242 | -0.003193498343309 | 0.203177% | 0.036818% | 3.573 |
| C3D10 | finer | 31,742 | 18,925 | -0.003194353096108 | 0.176466% | 0.026765% | 9.303 |

Within each formulation, reducing characteristic size is h-refinement. Increasing approximation order from C3D4 to C3D10 at a given characteristic size is an element-order or p-refinement comparison. This controlled study is not an hp-adaptive FEM implementation. Identical characteristic size produces the same number of tetrahedra here but not the same node count, degrees of freedom, or computational cost: C3D10 adds midside nodes and its solve cost grows more rapidly.

For this bending-dominated cantilever benchmark and the tested characteristic mesh sizes, C3D4 produced a substantially stiffer displacement response and changed substantially under h-refinement. Its disagreement with the Euler-Bernoulli reference decreases from about 50.38% to 9.20% across the chosen levels but has not stabilized to the degree seen for C3D10. C3D10 results exactly reproduce the established convergence values and show much smaller successive changes and closer agreement with the Euler-Bernoulli reference at greater node, degree-of-freedom, and runtime cost. This behavior does not prove that C3D10 is universally superior: it applies to this geometry, loading, quantity, mesh family, and bending-dominated case.

All eight runs preserve negative displacement sign, approximately `-1000 N` applied Z resultant, `+1000 N` fixed-support Z reaction, and no CalculiX warnings. No acceptance threshold is defined. The study compares displacement only and does not establish stress convergence or general solver accuracy.

## C3D10 longitudinal bending-stress study

The stress verification quantity is longitudinal normal stress `sigma_xx`, not von Mises stress or a global maximum. The section is frozen at `x = 0.2 m`, four section depths from the fixed face, and was selected before stress results were observed. This avoids result-driven sampling but does not assert that `x/h = 4` is a universal Saint-Venant boundary.

Euler-Bernoulli theory gives `sigma_xx = -M_y z/I`, where `z` is measured from the section centroid. With the beam along global `+X` and the force in global `-Z`, the internal section moment is `M_y = -F(L-x) = -800 N*m`. Using `I = b h^3/12 = 5.208333333333335e-7 m^4`, the upper fiber is tensile `+38.4 MPa`, the lower fiber is compressive `-38.4 MPa`, and the neutral-axis value is zero. The 3D elasticity and Euler-Bernoulli models are not identical, so reported percentages are Euler-Bernoulli disagreement, not pure FEA error.

CalculiX `*EL FILE, S` stores Cauchy stress in FRD after extrapolation from integration points to nodes and averaging contributions from adjacent elements. Those convenient visualization values are not raw nodal stresses. This study adds `*EL PRINT, ELSET=BEAM` with `S`, which writes the six global Cauchy-stress components to DAT at each of the four C3D10 integration points. The integration-point representation is used because it is the least post-processed stress output available from the current solve.

For each mesh, the deterministic section patch contains all four integration points from every tetrahedron whose corner-node X range intersects `x = 0.2 m`. Physical integration-point coordinates are evaluated using the C3D10 quadratic shape functions and CalculiX's four-point tetrahedral rule. The implemented Gmsh-to-CalculiX permutation matches every element in Gmsh's independent CalculiX export. CalculiX integration-point record numbers map in order to `(low,low,low)`, `(high,low,low)`, `(low,high,low)`, and `(low,low,high)`, where `low = 0.138196601125011` and `high = 0.585410196624968`. Ordering matters for section reconstruction and diagnostic locations because it pairs each stress tensor with its physical point; it does not affect the unordered set of global peak values.

A volume-weighted least-squares field is fit using the basis `1`, `x-0.2`, `y-0.025`, `z-0.025`, and `(x-0.2)(z-0.025)`. The interaction term represents the axial bending-moment gradient without shifting the frozen evaluation section. At `x = 0.2 m` and `y = 0.025 m`, the fit reduces to a line through the depth. Its intercept, depth slope, neutral-axis crossing, and extrapolated values at the exact outer fibers are reported. The four-point rule has equal reference weights. All midside nodes in these meshes lie at their edge midpoints within `1.68e-16 m`, making the geometry straight-sided with constant Jacobian to numerical precision; physical quadrature weight is therefore one quarter of the actual corner-defined tetrahedron volume. This weighting prevents smaller tetrahedra from receiving disproportionate influence merely because each element contributes four samples. It is benchmark-specific and is not valid without pointwise Jacobian evaluation for curved C3D10 geometry.

| Level | Samples | Upper sigma_xx (MPa) | Lower sigma_xx (MPa) | Mean outer magnitude (MPa) | Euler-Bernoulli disagreement | Change from previous |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| coarse | 88 | 38.400935 | -38.400343 | 38.400639 | 0.001664% | — |
| medium | 200 | 38.406709 | -38.407534 | 38.407122 | 0.018546% | 0.016882% |
| fine | 396 | 38.400706 | -38.401085 | 38.400896 | 0.002333% | 0.016211% |
| finer | 704 | 38.399872 | -38.399821 | 38.399846 | 0.000400% | 0.002733% |

| Level | Depth slope (GPa/m) | Neutral-axis sigma_xx (kPa) | Reconstructed neutral-axis Z (m) | Weighted RMSE (kPa) | Weighted R-squared |
| --- | ---: | ---: | ---: | ---: | ---: |
| coarse | 1.536025563 | 0.296067 | 0.024999807 | 53.985770 | 0.999994677 |
| medium | 1.536284872 | -0.412405 | 0.025000268 | 32.832995 | 0.999997841 |
| fine | 1.536035832 | -0.189536 | 0.025000123 | 18.105346 | 0.999999345 |
| finer | 1.535993850 | 0.025264 | 0.024999984 | 10.745042 | 0.999999760 |

All four reconstructed section lines have the expected upper-tension/lower-compression sign reversal. Their fitted neutral-axis values are near zero, reconstructed zero crossings are near `z = 0.025 m`, and residual metrics against the raw CalculiX integration-point `sigma_xx` values decrease under refinement. The basis assumes linear variation with Z at the frozen section, so high R-squared does not independently prove linearity; R-squared and RMSE quantify compatibility of the raw samples with that selected local model. Outer-fiber magnitude is stable but not strictly monotonic; no acceptance or convergence threshold is defined.

Whole-element inclusion creates an irregular finite axial patch rather than sampling exactly on the section. The axial and axial-depth terms reduce bias from that patch, but the result remains a local model-based reconstruction. The raw integration-point X bands shrink across the tested refinements:

| Level | Raw sample X minimum (m) | Raw sample X maximum (m) | Span (m) | Span / mesh size |
| --- | ---: | ---: | ---: | ---: |
| coarse | 0.181910353 | 0.218100582 | 0.036190229 | 1.447609 |
| medium | 0.186605237 | 0.205887637 | 0.019282400 | 1.090777 |
| fine | 0.190230604 | 0.209045085 | 0.018814481 | 1.505158 |
| finer | 0.191662471 | 0.207462878 | 0.015800408 | 1.787612 |

As an independently specified sensitivity cross-check, the same raw section-patch samples are also fit by equal-weight ordinary least squares of `sigma_xx` versus Z only. This deliberately omits X, Y, axial-depth interaction, and volume weighting. It still assumes linear variation with Z, so it is not independent proof of linearity, but it tests dependence of the reported magnitude on the full reconstruction basis.

| Level | Z-only outer magnitude (MPa) | Euler-Bernoulli disagreement | Raw-fit RMSE (kPa) | Raw-fit R-squared |
| --- | ---: | ---: | ---: | ---: |
| coarse | 38.400140 | 0.000365% | 316.013 | 0.999841547 |
| medium | 38.598182 | 0.516100% | 160.948 | 0.999953253 |
| fine | 38.432061 | 0.083492% | 128.060 | 0.999969956 |
| finer | 38.403739 | 0.009738% | 87.965 | 0.999985636 |

Direct, unfitted depth-band summaries provide a second view of the raw evidence. Mean `sigma_xx` in the fixed lower quarter is `-30.98`, `-30.20`, `-29.80`, and `-29.80 MPa`; the fixed upper-quarter means are `+31.10`, `+30.11`, `+29.68`, and `+29.26 MPa`. The central quarter-depth-band means are `-0.287`, `-0.053`, `+0.090`, and `+0.495 MPa`. These interior raw values exhibit the expected lower-compression/upper-tension trend without using the fitted outer-fiber result. They are not outer-fiber evaluations and are not compared directly with `38.4 MPa`.

The exact fiber values are extrapolations, not raw FEA evaluations. Closest raw integration-point distances are:

| Level | Upper distance (m) | Upper / mesh size | Lower distance (m) | Lower / mesh size |
| --- | ---: | ---: | ---: | ---: |
| coarse | 0.003290229 | 0.131609 | 0.003433010 | 0.137320 |
| medium | 0.002099680 | 0.118776 | 0.002099680 | 0.118776 |
| fine | 0.001056856 | 0.084549 | 0.001496022 | 0.119682 |
| finer | 0.000638786 | 0.072270 | 0.001083939 | 0.122634 |

Global integration-point peaks are retained separately as diagnostics:

| Level | Minimum sigma_xx (MPa) at `(x,y,z)` m | Maximum sigma_xx (MPa) at `(x,y,z)` m | Maximum von Mises (MPa) at `(x,y,z)` m |
| --- | --- | --- | --- |
| coarse | -46.173340 at `(0.002271, 0.007796, 0.001806)` | 46.061000 at `(0.002003, 0.031424, 0.048057)` | 43.641400 at `(0.011625, 0.024072, 0.048057)` |
| medium | -47.462810 at `(0.005822, 0.001664, 0.001664)` | 47.465280 at `(0.005822, 0.001664, 0.048336)` | 44.494973 at `(0.005822, 0.001664, 0.001664)` |
| fine | -50.713440 at `(0.004223, 0.001150, 0.001265)` | 50.730540 at `(0.004223, 0.001150, 0.048735)` | 47.696018 at `(0.004223, 0.001150, 0.001265)` |
| finer | -57.051670 at `(0.000818, 0.000843, 0.002816)` | 57.312420 at `(0.000804, 0.049157, 0.047200)` | 52.188009 at `(0.002834, 0.049179, 0.049195)` |

These peaks are selected directly from the same raw DAT integration-point tensors; their six components, element and integration-point identifiers, and consistently mapped physical coordinates are retained in the artifact. Von Mises stress is calculated from all three normal and all three engineering shear stress components using the standard invariant expression. The peaks occur close to the fully fixed face and increase with refinement over the tested meshes. That behavior is consistent with strong mesh sensitivity in the boundary region, but it does not by itself distinguish a boundary effect from a stress concentration or establish a mathematical singularity. The peaks are not compared with the `38.4 MPa` section reference and are not used for pass/fail.

Every run retained an approximately `-1000 N` applied Z resultant and `+1000 N` support reaction, reproduced the established C3D10 centroid-displacement value within `5e-10 m`, and reported no CalculiX warnings. This experiment provides evidence that the selected C3D10 models reproduce the expected longitudinal bending distribution at the frozen section with low mesh sensitivity for this reconstructed quantity. It does not establish general stress convergence, resolve the fixed-boundary peak behavior, verify other stress components or formulations, or physically validate the model.

## C3D10 axial-bar benchmark

The axial-bar benchmark complements the bending-focused cantilever evidence by exercising axial load transfer, reaction equilibrium, longitudinal displacement and stress, and Poisson response through an independent analytical case. Passing either benchmark does not establish correctness of the other quantities or general solver verification.

The frozen bar is `L = 1.0 m`, `b = h = 0.05 m`, `A = 0.0025 m^2`, `E = 200 GPa`, and `nu = 0.30`. A consistent quadratic-face nodal load represents uniform `+X` traction of `400000 Pa` on the `x = 1 m` face, integrating to `+1000 N`. The entire `x = 0` face is fixed in UX, UY, and UZ. The geometry is exported as STEP in millimetres, re-imported into Gmsh/OpenCASCADE in metres, grouped as `axial_bar`, `fixed`, and `axial_load`, meshed with C3D10 at the preselected `0.0125 m` characteristic size, converted with verified Gmsh-to-CalculiX connectivity, and solved by CalculiX as a small-deformation linear-static isotropic model.

The ideal uniaxial references are:

`sigma_xx = F/A = 400000 Pa = 0.4 MPa`

`epsilon_xx = sigma_xx/E = 2e-6`

`epsilon_yy = epsilon_zz = -nu*epsilon_xx = -6e-7`

`delta_x = F L/(A E) = 2e-6 m = 0.002 mm`

The displacement QoI is the global displacement vector at the free-end face centroid `(1.0, 0.025, 0.025) m`, evaluated using six-node quadratic-triangle interpolation. The mesh contains 13,218 nodes and 7,242 C3D10 elements. The interpolated result is `(UX, UY, UZ) = (1.996734046e-6, -1.070376e-10, 1.933251e-10) m`. UX is `3.265954e-9 m` below the ideal reference, a `0.163298%` disagreement. The transverse centroid components are negligible relative to UX.

Stress verification uses raw Cauchy stress from CalculiX DAT output requested with `*EL PRINT, ELSET=AXIAL_BAR` and `S`. FRD nodally extrapolated and averaged stresses are not used. The interior region was fixed before results were observed: all four integration points of every C3D10 tetrahedron whose corner-node X range intersects `x = 0.5 m`. It contains 94 elements and 376 raw samples spanning `x = 0.489668851` to `0.509045085 m`. Each point uses its equal-rule physical weight of one quarter of the straight-sided parent tetrahedron volume.

| Component | Volume-weighted mean (Pa) | Raw minimum (Pa) | Raw maximum (Pa) | Standard deviation (Pa) |
| --- | ---: | ---: | ---: | ---: |
| sigma_xx | 400000.0 | 400000.0 | 400000.0 | 0.0 |
| sigma_yy | -6.22e-10 | -2.51e-8 | 3.96e-8 | 1.00e-8 |
| sigma_zz | -2.22e-9 | -3.59e-8 | 3.60e-8 | 9.67e-9 |
| sigma_xy | -2.14e-8 | -6.88e-8 | 1.46e-8 | 1.54e-8 |
| sigma_xz | 9.91e-9 | -2.04e-8 | 4.29e-8 | 1.02e-8 |
| sigma_yz | -4.41e-10 | -1.17e-8 | 1.07e-8 | 3.71e-9 |

The volume-weighted mean `sigma_xx` differs from `0.4 MPa` only at floating-point summation level (`1.46e-14%`). The transverse and shear components are numerically negligible in this interior patch, and the zero observed `sigma_xx` spread is limited by the precision written to DAT; it must not be generalized into a mesh-convergence claim.

Normal strains are reconstructed independently for each raw stress tensor using isotropic elastic compliance:

`epsilon_xx = (sigma_xx - nu*(sigma_yy + sigma_zz))/E`

`epsilon_yy = (sigma_yy - nu*(sigma_xx + sigma_zz))/E`

`epsilon_zz = (sigma_zz - nu*(sigma_xx + sigma_yy))/E`

The volume-weighted interior means are `epsilon_xx = 2.000000000000004e-6`, `epsilon_yy = -5.999999999999998e-7`, and `epsilon_zz = -6.000000000000101e-7`. These agree with the ideal uniaxial and Poisson references at numerical precision. Because the same isotropic constitutive law was supplied to CalculiX and used for reconstruction, this is a deterministic strain interpretation check, not an independent verification of the constitutive implementation.

Equilibrium is preserved in all axes: applied resultant `(1000.0000000000001, 0, 0) N` and fixed-support reaction `(-1000.0, 7.07e-11, -9.23e-12) N`. No CalculiX warning was reported.

The predeclared one-section-depth support band (`x <= 0.05 m`) confirms the expected fixed-face perturbation. Its mean `sigma_xx` is `0.399989 MPa`, with a `0.342246` to `0.532868 MPa` range; mean `sigma_yy` and `sigma_zz` are about `0.022 MPa`, individual transverse stresses reach about `0.202 MPa`, and shear magnitudes reach about `0.120 MPa`. Its combined non-axial-stress RMS is `0.072119 MPa`, versus approximately `3.34e-14 MPa` in the interior. This is evidence of local three-dimensional constraint effects, not a singularity claim. The predeclared loaded-end band (`x >= 0.95 m`) remains uniaxial to DAT precision under the consistent uniform traction and shows no comparable loading-boundary perturbation.

The `0.163298%` elongation disagreement is consistent with comparing the fully constrained three-dimensional support model against the ideal one-dimensional free-Poisson bar reference; it is not labeled pure FEA error. This single-mesh analytical benchmark does not establish mesh independence, general stress convergence, general CalculiX verification, physical validation, nonlinear behavior, yielding, or C3D4 axial behavior.

## C3D10 square-bar torsion benchmark

The torsion benchmark adds independent evidence for pure-moment loading, moment equilibrium, rotational response, longitudinal shear, and non-circular-section warping. It complements but is not proved by the bending and axial benchmarks. The frozen model is a `1.0 m` long square bar with side `a = 0.05 m`, `E = 200 GPa`, `nu = 0.30`, and torque `Tx = +100 N*m`. It follows the same real ingestion boundary: a millimetre STEP fixture is re-imported by Gmsh/OpenCASCADE in metres, assigned the physical groups `torsion_bar`, `fixed`, and `torque_load`, meshed at the preselected `0.0125 m` size with C3D10 elements, converted with the audited connectivity permutation, and solved by CalculiX. The mesh has 13,218 nodes and 7,242 C3D10 elements.

The loaded-face traction is derived as

`t(y,z) = (0, -k(z-zc), +k(y-yc))`, with `(yc,zc) = (0.025,0.025) m`.

For each quadratic surface triangle, the product of this linear traction and the quadratic shape functions is integrated using a four-point degree-three-exact triangle rule. A unit-`k` load is assembled first; its actual nodal moment determines `k = 95,999,999.99999997 Pa/m` for the requested torque. The final assembled loads give force `(0, -2.36e-13, +3.87e-13) N` and moment about the loaded-face centroid `(100.00000000000001, 0, 0) N*m`. This is neither a single nodal moment nor an unverified force couple.

For isotropic elasticity, `G = E/[2(1+nu)] = 76.923076923 GPa`. The analytical reference uses the documented approximate square Saint-Venant constant `Jt = 0.1406 a^4 = 8.7875e-7 m^4`, giving

`theta = T L/(G Jt) = 0.001479374110953058 rad`.

The polar second moment is `Iy+Iz = 1.041666666666667e-6 m^4`; it is recorded for contrast and is not substituted for `Jt`. These quantities are not interchangeable for a non-circular section.

The primary numerical QoI is an equal-node least-squares fit over the 105 unique free-end surface nodes. It simultaneously fits rigid transverse translations and the small-angle field `UY = ty - theta(z-zc)`, `UZ = tz + theta(y-yc)`, so a small section translation does not bias the rotation. The result is `theta = 0.001474008751862423 rad`, an absolute difference of `5.365359091e-6 rad` and `0.362678%` disagreement from the Saint-Venant reference. The transverse fit residual is `5.76009e-8 m` RMS and `1.22036e-7 m` maximum absolute, exposing the non-rigid part of the end-face response rather than hiding it.

The free-end value is retained as a system-level total rotation, but the cleaner Saint-Venant QoI is the interior twist rate. Exact geometric sections were predeclared at `x = 0.25`, `0.50`, and `0.75 m`. Because these are not mesh node planes, every straight-sided tetrahedron is intersected with each requested X plane. The resulting convex polygons are triangulated, and displacement is evaluated from the C3D10 shape functions at a positive seven-point, degree-five-exact triangle rule. The same translation-plus-rotation equations are then fit with physical area weights. Thus the reported locations are the requested planes themselves, rather than undocumented nearest-node planes. Each integrated cross-section recovers the expected `0.0025 m^2` area.

| Requested / actual X (m) | Intersected elements | Quadrature points | Fitted theta (rad) | Fitted `(ty,tz)` (m) | RMS residual (m) | Maximum residual (m) |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| 0.25 / 0.2499999999999999 | 78 | 700 | 0.000366971972123 | `(2.256e-10, 7.074e-10)` | 4.133e-10 | 2.056e-9 |
| 0.50 / 0.4999999999999999 | 73 | 616 | 0.000735335748856 | `(1.012e-10, 1.238e-9)` | 4.926e-10 | 2.071e-9 |
| 0.75 / 0.7499999999999997 | 83 | 700 | 0.001103735449712 | `(6.285e-10, 2.158e-9)` | 3.614e-10 | 1.828e-9 |

Ordinary least squares over these three area-fitted section rotations gives

`theta(x) = -1.415754025e-6 + 0.001473526955179*x`.

The angular fit residual is `8.46740e-9 rad` RMS and `1.19747e-8 rad` maximum, with `R^2 = 0.9999999992075`. The analytical Saint-Venant rate is `T/(GJt) = 0.001479374110953 rad/m`; the FEA interior rate differs by `5.847156e-6 rad/m`, or `0.395245%`. No acceptance threshold is attached to this comparison.

Extrapolating the interior line to `x = 1 m` gives `0.001472111201153 rad`, while the independently retained free-face fit is `0.001474008751862 rad`, higher by `1.897551e-6 rad` or `0.128900%` of the extrapolated value. Together with the very linear interior field, this supports a small end-region influence on total free-end rotation. It does not quantitatively separate the effects of the fixed support, the resultant-equivalent loaded-face traction, finite length, or discretization. The interior-rate disagreement is slightly larger than the free-end total-rotation disagreement, so “cleaner” here means less directly dependent on either boundary traction distribution, not numerically closer by construction.

Free-end axial displacement is summarized over the same nodes: minimum `UX = -2.067025e-7 m`, maximum `+2.058629e-7 m`, mean `2.72021e-10 m`, and RMS variation about the mean `9.12781e-8 m`. The signed, nonuniform field is consistent with square-section warping, subject to the finite-length model and the applied end traction. No analytical warping-function verification is claimed.

Stress evidence comes only from raw CalculiX DAT Cauchy tensors requested by `*EL PRINT, ELSET=TORSION_BAR` with `S`, not FRD extrapolated or nodally averaged stresses. The predeclared interior patch includes all four integration points of the 94 C3D10 elements whose corner-node X range intersects `x = 0.5 m`: 376 samples spanning `x = 0.4896688511` to `0.5090450850 m`. Straight-element volume-quarter weights give the following summary.

| Component | Volume-weighted mean (MPa) | Raw minimum (MPa) | Raw maximum (MPa) | RMS (MPa) |
| --- | ---: | ---: | ---: | ---: |
| sigma_xx | -0.000885 | -0.531980 | 0.549252 | 0.131588 |
| sigma_yy | -0.000806 | -0.228974 | 0.241329 | 0.048729 |
| sigma_zz | -0.002650 | -0.229062 | 0.230156 | 0.048711 |
| sigma_xy | -0.059550 | -3.554830 | 3.489491 | 1.458400 |
| sigma_xz | -0.043488 | -3.470823 | 3.472026 | 1.491968 |
| sigma_yz | -0.000105 | -0.038378 | 0.042596 | 0.011253 |

Raw-point von Mises ranges from `0.220048` to `6.157698 MPa`, with a volume-weighted mean of `3.360461 MPa`. The combined longitudinal-shear RMS is `2.086360 MPa`, about 14.0 times the combined RMS of the normal and transverse-shear components. Both `sigma_xy` and `sigma_xz` change sign. Centroid-tied quadrants also show the expected organized sign pattern: mean `sigma_xy` switches with Z, while mean `sigma_xz` switches with Y. These are qualitative square-torsion checks; no circular-shaft `tau=Tr/J` comparison or stress acceptance threshold is used.

Reaction forces reconstructed from the individual support rows are `(4.20e-10, -1.69e-9, +1.55e-9) N`. Their moment about the same loaded-face centroid is `(-99.99999815, +1.97e-5, -4.09e-5) N*m`. The small transverse moment residues reflect the finite decimal precision of individual DAT rows. CalculiX 2.23 reported no warning.

The predeclared one-width support band (`x <= 0.05 m`) retains a similar longitudinal-shear RMS (`2.098479 MPa`) but its combined normal/transverse-shear RMS is `0.416170 MPa`, 2.79 times the interior value; longitudinal-shear dominance drops from 14.0 in the interior to 5.04. This documents local perturbation from fully suppressing end warping without declaring a singularity. The distributed linear end traction is resultant-equivalent but is not the exact Saint-Venant traction distribution for a square, so load-end and finite-length effects can also contribute to the free-end fit and analytical disagreement.

This is one mesh and one torque case. It does not establish mesh independence, a full analytical square-section stress or warping solution, general stress convergence, physical validation, nonlinear behavior, yielding, C3D4 behavior, or general CalculiX verification. No pass/fail threshold is defined.

## Engineering-domain axial regression

The axial bar is the first and only benchmark minimally integrated with the reusable engineering domain definitions. Its named volume and faces, snapshotted elastic material, `+1000 N` force intent, fixed UX/UY/UZ condition, `0.0125 m` C3D10 mesh configuration, and CalculiX linear-static configuration now construct one immutable `AnalysisDefinition`. Benchmark-specific STEP generation, Gmsh physical-tag resolution, consistent nodal-force integration, CalculiX deck construction, execution, and result parsing remain in their existing scripts.

The benchmark was rerun after integration. Its solver-input SHA-256 remained exactly `667ee2d057709f187516107ee1e14e3534a47a9d799d0e43e6cfda98be07d5a9`. Node and element counts remained 13,218 and 7,242. The free-end centroid UX remained `1.99673404618334e-6 m`; interior mean `sigma_xx` remained `400000 Pa`; reconstructed mean `epsilon_xx` remained `2e-6`; applied FX remained `1000.0000000000001 N`; and support reaction FX remained `-1000 N`. No CalculiX warning was reported. This exact regression shows that introducing the domain representation did not change the axial numerical model or selected results.

Domain unit tests cover material bounds, named geometry selection, boundary-condition DOFs, force normalization and zero-direction rejection, explicit pressure-normal convention, mesh and solver constraints, nested immutability, required factor of safety, and reproduction of the verified axial engineering inputs. These tests validate construction rules only; they do not establish that a named CAD region exists or that a solver mapping is physically correct.
