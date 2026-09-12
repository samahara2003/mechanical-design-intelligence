"""Private S3-compatible artifact storage used only by the Engineering Worker."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class WorkerStorageError(RuntimeError):
    pass


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise WorkerStorageError(f"{name} is required")
    return value


def _require_uuid(value: str, label: str) -> None:
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value, re.I) is None:
        raise WorkerStorageError(f"{label} must be a UUID")


def artifact_object_key(analysis_id: str, claim_token: str, role: str, suffix: str) -> str:
    _require_uuid(analysis_id, "analysis ID")
    _require_uuid(claim_token, "claim token")
    if role not in {"mesh", "solver_input", "solver_dat", "solver_frd"}:
        raise WorkerStorageError("unsupported engineering artifact role")
    if not re.fullmatch(r"\.[a-z0-9]+", suffix):
        raise WorkerStorageError("artifact suffix is invalid")
    return f"analyses/{analysis_id}/attempts/{claim_token}/{role}{suffix}"


class PrivateWorkerStorage:
    def __init__(self) -> None:
        account_id = _required_environment("R2_ACCOUNT_ID")
        self.bucket = _required_environment("R2_BUCKET_NAME")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            region_name="auto",
            aws_access_key_id=_required_environment("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=_required_environment("R2_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4"),
        )

    def download(self, key: str, destination: Path) -> tuple[str, int]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        count = 0
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        with destination.open("wb") as output:
            for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
        if response.get("ContentLength") != count:
            raise WorkerStorageError("downloaded object length differs from R2 ContentLength")
        return digest.hexdigest(), count

    def upload_immutable(
        self, analysis_id: str, claim_token: str, role: str, path: Path
    ) -> tuple[str, str, int]:
        key = artifact_object_key(analysis_id, claim_token, role, path.suffix.lower())
        data_sha256 = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                data_sha256.update(chunk)
                size += len(chunk)
        sha256 = data_sha256.hexdigest()
        try:
            with path.open("rb") as body:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=body,
                    ContentLength=size,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                    Metadata={
                        "sha256": sha256,
                        "analysis-id": analysis_id,
                        "claim-token": claim_token,
                        "artifact-role": role,
                    },
                )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
            metadata = existing.get("Metadata", {})
            if existing.get("ContentLength") != size or metadata.get("sha256") != sha256:
                raise WorkerStorageError("immutable artifact key already contains different bytes") from error
        return key, sha256, size

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
