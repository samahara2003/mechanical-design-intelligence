"""PostgreSQL-leased Engineering Worker V0 for the controlled bracket case."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analysis_provenance import (
    analysis_definition_fingerprint,
    analysis_provenance_to_dict,
)
from analysis_results import analysis_result_to_dict
from engineering_assessment import engineering_assessment_to_dict
from bracket_execution import execute_controlled_bracket, validate_controlled_bracket_definition
from engineering_domain import analysis_definition_from_dict
from worker_storage import PrivateWorkerStorage


LEASE_SECONDS = 30 * 60


class WorkerStateError(RuntimeError):
    pass


class StepIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedAnalysis:
    analysis_id: str
    claim_token: str
    definition: dict
    definition_sha256: str
    model_version_id: str
    model_id: str
    cad_sha256: str
    source_size_bytes: int
    source_key: str


def load_local_environment(repository: Path) -> None:
    """Load the ignored development env without ever logging its values."""
    path = repository / "web" / ".env.local"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


def connect_database() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None or not database_url.strip():
        raise WorkerStateError("DATABASE_URL is required")
    return psycopg.connect(database_url, row_factory=dict_row)


def claim_analysis(
    connection: psycopg.Connection,
    worker_id: str,
    lease_seconds: int = LEASE_SECONDS,
) -> ClaimedAnalysis | None:
    """Atomically claim/reclaim one runnable row using SKIP LOCKED and a fencing token."""
    claim_token = str(uuid.uuid4())
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            WITH candidate AS (
              SELECT j.analysis_id
              FROM analysis_jobs j
              JOIN analyses a ON a.id = j.analysis_id
              WHERE a.status IN ('queued', 'running')
                AND j.available_at <= now()
                AND (
                  j.status = 'queued'
                  OR (j.status = 'claimed' AND j.lease_expires_at <= now())
                )
              ORDER BY j.available_at, j.created_at
              FOR UPDATE OF j SKIP LOCKED
              LIMIT 1
            )
            UPDATE analysis_jobs j
            SET status = 'claimed',
                attempt_count = j.attempt_count + 1,
                claim_token = %s,
                claimed_by = %s,
                claimed_at = now(),
                lease_expires_at = now() + (%s * interval '1 second'),
                completed_at = NULL,
                failure_reason = NULL
            FROM candidate
            WHERE j.analysis_id = candidate.analysis_id
            RETURNING j.analysis_id
            """,
            (claim_token, worker_id, lease_seconds),
        )
        claimed = cursor.fetchone()
        if claimed is None:
            return None
        cursor.execute(
            """
            UPDATE analyses
            SET status = 'running', updated_at = now()
            WHERE id = %s AND status = 'queued'
            """,
            (claimed["analysis_id"],),
        )
        cursor.execute(
            """
            SELECT a.id AS analysis_id, a.status, a.engineering_definition,
                   a.definition_sha256, mv.id AS model_version_id, mv.model_id,
                   mv.cad_sha256, mv.source_size_bytes,
                   mv.artifact_storage_key AS source_key
            FROM analyses a
            JOIN model_versions mv ON mv.id = a.model_version_id
            WHERE a.id = %s
            """,
            (claimed["analysis_id"],),
        )
        record = cursor.fetchone()
        if record is None or record["status"] != "running":
            raise WorkerStateError("claimed job has no runnable Analysis")
        if record["source_key"] is None:
            raise WorkerStateError("ModelVersion has no private STEP object key")
        return ClaimedAnalysis(
            analysis_id=str(record["analysis_id"]),
            claim_token=claim_token,
            definition=record["engineering_definition"],
            definition_sha256=record["definition_sha256"],
            model_version_id=str(record["model_version_id"]),
            model_id=str(record["model_id"]),
            cad_sha256=record["cad_sha256"],
            source_size_bytes=record["source_size_bytes"],
            source_key=record["source_key"],
        )


def finalize_success(
    connection: psycopg.Connection,
    claim: ClaimedAnalysis,
    result_summary: dict,
    assessment_summary: dict,
    provenance_summary: dict,
) -> None:
    """Fence stale claims and atomically publish result plus terminal states."""
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.status AS job_status, j.claim_token, a.status AS analysis_status
            FROM analysis_jobs j JOIN analyses a ON a.id = j.analysis_id
            WHERE j.analysis_id = %s FOR UPDATE OF j, a
            """,
            (claim.analysis_id,),
        )
        state = cursor.fetchone()
        validate_finalization_fence(state, claim.claim_token)
        cursor.execute(
            "UPDATE analyses SET status = 'completed', updated_at = now() WHERE id = %s AND status = 'running'",
            (claim.analysis_id,),
        )
        if cursor.rowcount != 1:
            raise WorkerStateError("Analysis completion lost a lifecycle race")
        cursor.execute(
            """
            INSERT INTO analysis_results
                (analysis_id, result_summary, assessment_summary, provenance_summary)
            VALUES (%s, %s, %s, %s)
            """,
            (
                claim.analysis_id,
                Jsonb(result_summary),
                Jsonb(assessment_summary),
                Jsonb(provenance_summary),
            ),
        )
        cursor.execute(
            """
            UPDATE analysis_jobs
            SET status = 'finished', completed_at = now(), failure_reason = NULL
            WHERE analysis_id = %s AND status = 'claimed' AND claim_token = %s
            """,
            (claim.analysis_id, claim.claim_token),
        )
        if cursor.rowcount != 1:
            raise WorkerStateError("job finalization lost its claim fence")


def safe_failure_reason(phase: str, error: BaseException) -> str:
    safe_phase = re.sub(r"[^a-z0-9_]+", "_", phase.lower()).strip("_") or "worker"
    return f"{safe_phase} failed ({type(error).__name__})"[:1000]


def validate_finalization_fence(state: dict | None, claim_token: str) -> None:
    """Allow publication only for the currently claimed, running attempt."""
    if (
        state is None
        or state["job_status"] != "claimed"
        or str(state["claim_token"]) != claim_token
        or state["analysis_status"] != "running"
    ):
        raise WorkerStateError("claim is stale or Analysis is no longer running")


def finalize_failure(
    connection: psycopg.Connection,
    claim: ClaimedAnalysis,
    reason: str,
) -> None:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT j.status AS job_status, j.claim_token, a.status AS analysis_status
            FROM analysis_jobs j JOIN analyses a ON a.id = j.analysis_id
            WHERE j.analysis_id = %s FOR UPDATE OF j, a
            """,
            (claim.analysis_id,),
        )
        state = cursor.fetchone()
        if state is None or state["job_status"] != "claimed" or str(state["claim_token"]) != claim.claim_token:
            return
        if state["analysis_status"] == "running":
            cursor.execute(
                "UPDATE analyses SET status = 'failed', updated_at = now() WHERE id = %s AND status = 'running'",
                (claim.analysis_id,),
            )
        cursor.execute(
            """
            UPDATE analysis_jobs
            SET status = 'finished', completed_at = now(), failure_reason = %s
            WHERE analysis_id = %s AND status = 'claimed' AND claim_token = %s
            """,
            (reason[:1000], claim.analysis_id, claim.claim_token),
        )


def _expected_source_key(claim: ClaimedAnalysis) -> str:
    return f"models/{claim.model_id}/versions/{claim.model_version_id}/source.step"


def run_claim(
    connection: psycopg.Connection,
    storage: PrivateWorkerStorage,
    repository: Path,
    work_root: Path,
    claim: ClaimedAnalysis,
) -> dict:
    phase = "definition_reconstruction"
    try:
        definition = analysis_definition_from_dict(claim.definition)
        phase = "definition_model_version"
        if definition.model_version.value != claim.model_version_id:
            raise WorkerStateError("definition ModelVersion reference differs from Analysis")
        phase = "definition_fingerprint"
        fingerprint = analysis_definition_fingerprint(definition)
        if fingerprint != claim.definition_sha256:
            raise WorkerStateError("authoritative definition fingerprint differs from frozen Analysis")
        phase = "controlled_geometry_scope"
        validate_controlled_bracket_definition(definition)
        phase = "source_storage_key"
        if claim.source_key != _expected_source_key(claim):
            raise WorkerStateError("ModelVersion STEP key is not server-controlled")

        run_dir = work_root / claim.analysis_id / claim.claim_token
        step_path = run_dir / "source.step"
        phase = "step_integrity"
        downloaded_sha256, downloaded_size = storage.download(claim.source_key, step_path)
        if downloaded_sha256 != claim.cad_sha256 or downloaded_size != claim.source_size_bytes:
            raise StepIntegrityError("downloaded STEP does not match ModelVersion integrity metadata")

        phase = "engineering_execution"
        execution = execute_controlled_bracket(repository, step_path, run_dir, definition)
        result_summary = analysis_result_to_dict(execution.result)
        assessment_summary = engineering_assessment_to_dict(execution.assessment)
        provenance_summary = analysis_provenance_to_dict(
            execution.provenance, include_local_paths=False
        )
        phase = "artifact_upload"
        uploaded = {}
        for role, path in execution.generated_artifacts.items():
            key, sha256, size = storage.upload_immutable(
                claim.analysis_id, claim.claim_token, role, path
            )
            uploaded[role] = {"storage_key": key, "sha256": sha256, "byte_size": size}
        provenance_summary["artifacts"]["cad_step"]["storage_key"] = claim.source_key
        for role, evidence in uploaded.items():
            provenance_summary["artifacts"][role]["storage_key"] = evidence["storage_key"]
        phase = "durable_finalization"
        finalize_success(
            connection, claim, result_summary, assessment_summary, provenance_summary
        )
        return {
            "status": "completed",
            "analysis_id": claim.analysis_id,
            "downloaded_step_sha256_verified": True,
            "artifact_count": len(uploaded),
        }
    except Exception as error:
        reason = safe_failure_reason(phase, error)
        finalize_failure(connection, claim, reason)
        return {"status": "failed", "analysis_id": claim.analysis_id, "reason": reason}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="claim at most one job and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--work-root", type=Path, default=Path("artifacts/worker"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository = Path(__file__).resolve().parents[1]
    load_local_environment(repository)
    worker_id = f"{socket.gethostname()}-{uuid.uuid4()}"
    connection = connect_database()
    storage = PrivateWorkerStorage()
    try:
        while True:
            claim = claim_analysis(connection, worker_id)
            if claim is not None:
                print(json.dumps(run_claim(
                    connection, storage, repository, args.work_root.resolve(), claim
                )))
                if args.once:
                    return 0
            elif args.once:
                print(json.dumps({"status": "idle"}))
                return 0
            time.sleep(args.poll_seconds)
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
