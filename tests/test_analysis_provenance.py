import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import mock_open, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from analysis_provenance import (  # noqa: E402
    ENGINEERING_EVIDENCE_POSTPROCESSOR_ID,
    ENGINEERING_EVIDENCE_POSTPROCESSOR_VERSION,
    AnalysisProvenance,
    ArtifactKind,
    ArtifactProvenance,
    PostprocessorProvenance,
    ProvenanceError,
    ToolProvenance,
    analysis_definition_fingerprint,
    analysis_provenance_to_dict,
    artifact_provenance_from_file,
    build_analysis_provenance,
    canonical_analysis_definition_json,
    sha256_file,
)
from axial_bar_definition import axial_bar_analysis_definition  # noqa: E402
from axial_bar_verification import (  # noqa: E402
    EXPECTED_SOLVER_INPUT_SHA256 as AXIAL_DECK_SHA256,
)
from cantilever_definition import cantilever_analysis_definition  # noqa: E402
from cantilever_verification import (  # noqa: E402
    EXPECTED_SOLVER_INPUT_SHA256 as CANTILEVER_DECK_SHA256,
)
from engineering_domain import ForceLoad  # noqa: E402


KNOWN_ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
WORKSPACE = Path(__file__).resolve().parents[1]


class AnalysisProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = axial_bar_analysis_definition("4.15.2", "2.23")

    def test_definition_fingerprint_is_deterministic_canonical_json(self) -> None:
        first = analysis_definition_fingerprint(self.definition)
        second = analysis_definition_fingerprint(self.definition)
        self.assertEqual(first, second)
        canonical = canonical_analysis_definition_json(self.definition)
        self.assertEqual(canonical, canonical_analysis_definition_json(self.definition))
        self.assertNotIn(" ", canonical)
        self.assertEqual(len(first), 64)

    def test_engineering_changes_change_definition_fingerprint(self) -> None:
        original = analysis_definition_fingerprint(self.definition)
        force = self.definition.loads[0]
        changed_force = replace(
            self.definition,
            loads=(ForceLoad(force.target, force.magnitude_n + 1.0, force.direction),),
        )
        changed_mesh = replace(
            self.definition,
            mesh=replace(self.definition.mesh, characteristic_size_m=0.01),
        )
        self.assertNotEqual(original, analysis_definition_fingerprint(changed_force))
        self.assertNotEqual(original, analysis_definition_fingerprint(changed_mesh))

    def test_streaming_sha256_known_bytes_and_missing_file_failure(self) -> None:
        with patch("pathlib.Path.open", mock_open(read_data=b"abc")):
            self.assertEqual(sha256_file(Path("known.bin"), chunk_size=1), KNOWN_ABC_SHA256)
        artifact = artifact_provenance_from_file(ArtifactKind.MESH, WORKSPACE / "AGENTS.md")
        self.assertEqual(artifact.byte_size, (WORKSPACE / "AGENTS.md").stat().st_size)
        with self.assertRaises(ProvenanceError):
            sha256_file(WORKSPACE / "definitely_missing_provenance_file.bin")

    def test_provenance_is_immutable_and_serializes_deterministically(self) -> None:
        artifacts = {
            kind: ArtifactProvenance(kind, KNOWN_ABC_SHA256, 3, f"C:/run/{kind.value}")
            for kind in ArtifactKind
        }
        provenance = AnalysisProvenance(
            self.definition.model_version,
            analysis_definition_fingerprint(self.definition),
            artifacts[ArtifactKind.CAD_STEP],
            artifacts[ArtifactKind.MESH],
            artifacts[ArtifactKind.SOLVER_INPUT],
            artifacts[ArtifactKind.SOLVER_DAT],
            artifacts[ArtifactKind.SOLVER_FRD],
            ToolProvenance("Gmsh", "4.15.2"),
            ToolProvenance("CalculiX", "2.23"),
            PostprocessorProvenance(
                ENGINEERING_EVIDENCE_POSTPROCESSOR_ID,
                ENGINEERING_EVIDENCE_POSTPROCESSOR_VERSION,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            provenance.analysis_definition_sha256 = "0" * 64
        first = json.dumps(analysis_provenance_to_dict(provenance), separators=(",", ":"))
        second = json.dumps(analysis_provenance_to_dict(provenance), separators=(",", ":"))
        self.assertEqual(first, second)
        self.assertEqual(provenance.gmsh.version, "4.15.2")
        self.assertEqual(provenance.calculix.version, "2.23")

    def test_local_path_is_not_core_content_identity(self) -> None:
        first = ArtifactProvenance(ArtifactKind.MESH, KNOWN_ABC_SHA256, 3, "C:/one/mesh.msh")
        second = ArtifactProvenance(ArtifactKind.MESH, KNOWN_ABC_SHA256, 3, "D:/two/mesh.msh")
        self.assertEqual(first, second)
        self.assertNotEqual(
            analysis_provenance_to_dict(self._provenance_with_mesh(first)),
            analysis_provenance_to_dict(self._provenance_with_mesh(second)),
        )
        self.assertEqual(
            analysis_provenance_to_dict(
                self._provenance_with_mesh(first), include_local_paths=False
            ),
            analysis_provenance_to_dict(
                self._provenance_with_mesh(second), include_local_paths=False
            ),
        )

    def test_builder_captures_tools_and_current_postprocessor_contract(self) -> None:
        readable_file = WORKSPACE / "AGENTS.md"
        provenance = build_analysis_provenance(
            self.definition,
            cad_step_path=readable_file,
            mesh_path=readable_file,
            solver_input_path=readable_file,
            solver_dat_path=readable_file,
            solver_frd_path=readable_file,
            gmsh_version="4.15.2",
            calculix_version="2.23",
        )
        self.assertEqual(provenance.gmsh, ToolProvenance("Gmsh", "4.15.2"))
        self.assertEqual(provenance.calculix, ToolProvenance("CalculiX", "2.23"))
        self.assertEqual(
            provenance.postprocessor.identifier, ENGINEERING_EVIDENCE_POSTPROCESSOR_ID
        )

    def test_benchmark_solver_deck_regression_targets(self) -> None:
        self.assertEqual(
            AXIAL_DECK_SHA256,
            "667ee2d057709f187516107ee1e14e3534a47a9d799d0e43e6cfda98be07d5a9",
        )
        self.assertEqual(
            CANTILEVER_DECK_SHA256,
            "4f4d4333f88806a8255c862380d56a95b305f1755ce4dddda25b06c2f356d6ea",
        )

    def test_axial_and_cantilever_definitions_have_distinct_fingerprints(self) -> None:
        cantilever = cantilever_analysis_definition("4.15.2", "2.23")
        self.assertNotEqual(
            analysis_definition_fingerprint(self.definition),
            analysis_definition_fingerprint(cantilever),
        )

    def _provenance_with_mesh(self, mesh: ArtifactProvenance) -> AnalysisProvenance:
        artifacts = {
            kind: ArtifactProvenance(kind, KNOWN_ABC_SHA256, 3)
            for kind in ArtifactKind
        }
        return AnalysisProvenance(
            self.definition.model_version,
            analysis_definition_fingerprint(self.definition),
            artifacts[ArtifactKind.CAD_STEP],
            mesh,
            artifacts[ArtifactKind.SOLVER_INPUT],
            artifacts[ArtifactKind.SOLVER_DAT],
            artifacts[ArtifactKind.SOLVER_FRD],
            ToolProvenance("Gmsh", "4.15.2"),
            ToolProvenance("CalculiX", "2.23"),
            PostprocessorProvenance("engineering-core-analysis-result", "1"),
        )


if __name__ == "__main__":
    unittest.main()
