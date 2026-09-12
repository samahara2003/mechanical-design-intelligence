import sys
import unittest
from dataclasses import replace
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bracket_definition import bracket_analysis_definition  # noqa: E402
from bracket_execution import (  # noqa: E402
    ControlledBracketError,
    validate_controlled_bracket_definition,
)
from engineering_domain import ModelVersionReference  # noqa: E402
from engineering_worker import (  # noqa: E402
    ClaimedAnalysis,
    WorkerStateError,
    finalize_success,
    safe_failure_reason,
)
from worker_storage import (  # noqa: E402
    PrivateWorkerStorage,
    WorkerStorageError,
    artifact_object_key,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {}

    def put_object(self, **request) -> None:
        key = request["Key"]
        if key in self.objects:
            raise AssertionError("immutable key was reused")
        self.objects[key] = {
            "bytes": request["Body"].read(),
            "metadata": request["Metadata"],
        }


class NoOpContext:
    def __init__(self, value=None) -> None:
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args) -> None:
        return None


class FakeFinalizationCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.rowcount = -1
        self.selected = None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters) -> None:
        sql = " ".join(statement.split())
        if sql.startswith("SELECT j.status"):
            self.selected = dict(self.connection.state)
            return
        if sql.startswith("UPDATE analyses SET status = 'completed'"):
            if self.connection.state["analysis_status"] == "running":
                self.connection.state["analysis_status"] = "completed"
                self.rowcount = 1
            else:
                self.rowcount = 0
            return
        if sql.startswith("INSERT INTO analysis_results"):
            if self.connection.result is not None:
                raise AssertionError("duplicate result")
            self.connection.result = {
                "result": parameters[1].obj,
                "assessment": parameters[2].obj,
                "provenance": parameters[3].obj,
            }
            return
        if sql.startswith("UPDATE analysis_jobs"):
            if (
                self.connection.state["job_status"] == "claimed"
                and self.connection.state["claim_token"] == parameters[1]
            ):
                self.connection.state["job_status"] = "finished"
                self.rowcount = 1
            else:
                self.rowcount = 0
            return
        raise AssertionError(f"unexpected SQL in finalization test: {sql}")

    def fetchone(self):
        return self.selected


class FakeFinalizationConnection:
    def __init__(self, state) -> None:
        self.state = state
        self.result = None

    def transaction(self):
        return NoOpContext()

    def cursor(self):
        return FakeFinalizationCursor(self)


def claimed(analysis_id: str, claim_token: str) -> ClaimedAnalysis:
    return ClaimedAnalysis(
        analysis_id=analysis_id,
        claim_token=claim_token,
        definition={},
        definition_sha256="a" * 64,
        model_version_id="11111111-1111-4111-8111-111111111111",
        model_id="22222222-2222-4222-8222-222222222222",
        cad_sha256="b" * 64,
        source_size_bytes=1,
        source_key="unused",
    )


class EngineeringWorkerLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = replace(
            bracket_analysis_definition("4.15.2", "2.23"),
            model_version=ModelVersionReference("11111111-1111-4111-8111-111111111111"),
        )

    def test_controlled_bracket_definition_is_explicit(self) -> None:
        validate_controlled_bracket_definition(self.definition)
        with self.assertRaises(ControlledBracketError):
            validate_controlled_bracket_definition(
                replace(self.definition, material=replace(self.definition.material, poissons_ratio=0.29))
            )

    def test_artifact_keys_are_server_controlled(self) -> None:
        analysis_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        claim_token = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.assertEqual(
            artifact_object_key(analysis_id, claim_token, "solver_dat", ".dat"),
            f"analyses/{analysis_id}/attempts/{claim_token}/solver_dat.dat",
        )
        with self.assertRaises(WorkerStorageError):
            artifact_object_key(analysis_id, claim_token, "../../unsafe", ".dat")

    def test_partial_upload_crash_reclaim_uses_attempt_namespace_and_fence(self) -> None:
        analysis_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        attempt_a = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
        attempt_b = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
        client = FakeS3Client()
        storage = PrivateWorkerStorage.__new__(PrivateWorkerStorage)
        storage.bucket = "private-test-bucket"
        storage.client = client
        artifact = Path(__file__).resolve().parents[1] / "artifacts" / "_worker_retry_result.frd"
        try:
            artifact.write_bytes(b"attempt A timestamp-bearing bytes")
            key_a, sha_a, _ = storage.upload_immutable(
                analysis_id, attempt_a, "solver_frd", artifact
            )

            # A crashes; expiry/reclaim replaces the current fencing token.
            artifact.write_bytes(b"attempt B different but valid bytes")
            key_b, sha_b, _ = storage.upload_immutable(
                analysis_id, attempt_b, "solver_frd", artifact
            )
        finally:
            artifact.unlink(missing_ok=True)

        self.assertNotEqual(key_a, key_b)
        self.assertNotEqual(sha_a, sha_b)
        # The expired A lease is reclaimed; B is now the database's only valid claim.
        current = {
            "job_status": "claimed",
            "claim_token": attempt_b,
            "analysis_status": "running",
        }
        connection = FakeFinalizationConnection(current)
        winning_result = {"attempt": attempt_b}
        winning_assessment = {"assessment_version": "engineering-assessment/1"}
        winning_provenance = {
            "artifacts": {"solver_frd": {"storage_key": key_b, "sha256": sha_b}}
        }
        finalize_success(
            connection,
            claimed(analysis_id, attempt_b),
            winning_result,
            winning_assessment,
            winning_provenance,
        )
        with self.assertRaises(WorkerStateError):
            finalize_success(
                connection,
                claimed(analysis_id, attempt_a),
                {"attempt": attempt_a},
                {"assessment_version": "engineering-assessment/1"},
                {"artifacts": {"solver_frd": {"storage_key": key_a, "sha256": sha_a}}},
            )
        self.assertEqual(connection.result["result"], winning_result)
        self.assertEqual(connection.result["assessment"], winning_assessment)
        self.assertEqual(connection.result["provenance"], winning_provenance)
        self.assertNotIn(key_a, str(connection.result))
        self.assertEqual(client.objects[key_a]["metadata"]["claim-token"], attempt_a)
        self.assertEqual(client.objects[key_b]["metadata"]["claim-token"], attempt_b)

    def test_failure_reason_is_bounded_and_does_not_include_exception_text(self) -> None:
        reason = safe_failure_reason("STEP integrity", RuntimeError("secret-value"))
        self.assertEqual(reason, "step_integrity failed (RuntimeError)")
        self.assertNotIn("secret-value", reason)
        self.assertLessEqual(len(reason), 1000)


if __name__ == "__main__":
    unittest.main()
