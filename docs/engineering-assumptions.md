# Engineering Assumptions and Limitations

## Governing principle

Deterministic engineering calculations are the technical source of truth. AI may explain recorded inputs, results, assumptions, limitations, and provenance, but must not calculate or override engineering pass/fail status and must not infer technical truth from rendered result images.

## V1 model assumptions

Every V1 analysis is constrained to:

- one solid part imported from STEP;
- linear static structural behavior;
- a linear elastic, isotropic material model;
- small deformations;
- simplified fixed boundary conditions;
- force and/or pressure loads;
- tetrahedral finite elements generated with Gmsh/OpenCASCADE; and
- solution with CalculiX.

These assumptions imply that stiffness is constant, material response remains within the linear elastic regime used by the model, and deformation is sufficiently small for geometric nonlinearity to be neglected. The user is responsible for selecting inputs and idealizations that are appropriate to the physical case; the system must expose the assumptions needed to judge that appropriateness.

## V1 limitations

V1 does not model:

- plasticity or nonlinear material behavior;
- large deformation or geometric nonlinearity;
- contact or friction;
- bolt preload;
- fatigue;
- transient or dynamic response;
- thermal behavior or thermal loading; or
- assemblies.

Results must not be presented as covering these excluded phenomena. Physics scope may expand only through an explicit later decision.

## Factor of safety interpretation

The initial failure criterion is intended for ductile materials and uses:

`actual FoS = yield strength / relevant von Mises stress`

The yield strength must be the snapshotted value actually used by the executed analysis. The relevant von Mises stress must come from deterministic post-processing and must retain enough context to identify how and where it was obtained.

`actual FoS > 1` is not, by itself, a passing result. Pass/fail is evaluated against the user's configured requirement:

- pass when `actual FoS >= required FoS`;
- fail to meet the configured requirement when `actual FoS < required FoS`.

This comparison is deterministic and must not be delegated to AI. The present requirements do not define special handling for zero stress, invalid or missing yield strength, numerical failure, or rounding at the threshold; those behaviors must be specified before production implementation.

## Stress interpretation and singularities

Mesh-dependent stress concentrations and mathematical singularities can make a maximum nodal stress non-convergent or physically misleading. Therefore, the global maximum nodal stress must not automatically be presented as universally meaningful engineering truth. Reports and validation evidence should retain its location, extraction method, mesh context, and known limitations.

V1 is not required to implement a sophisticated singularity detector unless explicitly requested. This limitation does not remove the obligation to acknowledge suspected singular behavior and avoid unsupported conclusions.

## Reproducibility and immutable provenance

Once analysis execution begins, its engineering configuration becomes immutable. Each executed analysis must preserve an immutable snapshot sufficient to interpret and reproduce the result, including:

- CAD/model version and file checksum;
- material properties actually used, including yield strength and units;
- loads, their units, directions, magnitudes, and application definitions;
- boundary conditions and their application definitions;
- user-required minimum FoS;
- mesh configuration;
- mesher name and version;
- solver configuration;
- solver name and version;
- applicable engineering assumptions and limitations; and
- result provenance, linking reported values to source solver output and post-processing.

Mesh configuration is an engineering input because it affects calculated results. Intermediate and final artifacts needed to audit a result should be traceable to the executed analysis.

Reproducibility also requires explicit unit conventions, coordinate-system interpretation, element and result extraction choices, and software environment details. The exact conventions and structured result schema have not yet been selected; they must be made explicit as part of the Engineering Spike rather than assumed silently.

## Cantilever CAD-to-mesh spike

The deterministic benchmark fixture is a rectangular solid in SI units: 1.0 m long in X and 0.05 m by 0.05 m in cross-section. Its fixed face is located geometrically at `x = 0`; its load face is at `x = 1.0 m`. End faces are selected using bounding-box evidence and exact-count validation, not assumed CAD entity identifiers.

The initial uniform tetrahedral target size is 0.0125 m, configurable at execution time. This gives four nominal element lengths across each cross-section dimension. It is a provisional spike setting chosen for tractable inspection, not a validated production meshing strategy. Mesh convergence and refinement criteria remain future validation work.

For the initial bending solve, the primary volume element is the 10-node quadratic tetrahedron (`C3D10`). Quadratic interpolation is selected because bending response is the focus of this benchmark and linear tetrahedra can be overly stiff in bending. This selection is not a general accuracy claim; its behavior for this benchmark is examined in the displacement studies documented in [validation.md](validation.md).

The frozen load is a 1000 N resultant in global negative Z, represented by a uniform traction of 400000 Pa over the 0.0025 m² end face. CalculiX `*DLOAD` labels `P1` through `P4` apply scalar pressure normal to a tetrahedral element face; positive pressure acts inward. Because the free-end face has outward normal positive X, normal pressure cannot represent the required negative-Z tangential traction. The spike therefore integrates the constant traction using the six-node quadratic triangular face shape functions and writes the resulting consistent nodal forces in global degree of freedom 3. Corner-node contributions integrate to zero, while each triangle contributes one third of its area to each midside node. The implementation checks the triangulated area and summed force before solving.

The Gmsh `fixed` surface becomes a CalculiX node set constrained in degrees of freedom 1 through 3. The Gmsh `load` surface is matched to parent C3D10 faces and retained as a CalculiX element-face surface using `S1` through `S4` labels; these surface labels identify topology, while the applied traction direction remains global negative Z.

The bending-stress verification quantity is longitudinal Cauchy stress `sigma_xx` at the section `x = 0.2 m`, selected before stress results were observed. This is four section depths from the fixed face, but `x/h = 4` is not assumed to be a universal Saint-Venant boundary. The verification uses stresses written by CalculiX at the four C3D10 integration points, not the extrapolated and nodally averaged stress field in the FRD file. Exact outer-fiber values are extrapolated deterministically from a local fit because the integration points are interior to the elements; they are not directly evaluated FEA stresses. Equal `volume/4` integration-point weights are valid here because the four-point tetrahedral rule has equal weights and the benchmark's C3D10 elements are straight-sided to numerical precision. Curved quadratic tetrahedra would require pointwise Jacobian weighting. Global integration-point peaks remain diagnostic evidence and are not substituted for the section quantity.

## Axial-bar benchmark

The axial-bar benchmark uses the same 1.0 m by 0.05 m by 0.05 m rectangular solid and the same fully fixed `x = 0` face convention as the cantilever, with a consistent `+X` surface-traction resultant at `x = 1 m`. Fixing UX, UY, and UZ over the entire support face intentionally suppresses free Poisson contraction there. The immediate support region is therefore a three-dimensional constraint perturbation and is not assumed to be in a perfect uniaxial stress state. The analytical uniaxial comparison uses a predeclared interior patch centered at `x = 0.5 m`; this single-mesh benchmark does not establish axial mesh independence or general stress convergence.

## Square-bar torsion benchmark

The torsion benchmark uses the same solid dimensions and fully fixed `x = 0` convention, with a distributed tangential traction on `x = 1` scaled from its actual consistently assembled C3D10 nodal moment to produce `+100 N*m` about X and zero resultant force. Fully restraining the support suppresses Saint-Venant warping there and perturbs the nearby stress field. The linear radial-tangential end traction is resultant-equivalent but is not assumed to equal the exact Saint-Venant traction distribution for a square section.

The analytical twist reference uses `Jt = 0.1406 a^4`; the polar second moment `Iy+Iz` is not used as a torsional constant. Free-end rotation is an equal-node least-squares estimate from transverse surface displacements, while free-end UX is only qualitative warping evidence. Raw DAT integration-point stresses in a predeclared `x = 0.5 m` patch support qualitative shear-field inspection, not an exact analytical stress comparison. This single mesh does not establish torsional mesh independence, general stress convergence, or physical validation.
