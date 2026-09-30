"""S3/SQS transport for the existing secure Docker evaluator.

AWS workers execute the same PreparedTask and coordinator as local evaluation.
Only the trusted host has AWS credentials. Candidate containers have no network.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shinka.secure.archive import SnapshotMetadata
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.canonical import canonical_json_bytes, digest_json
from shinka.secure.contracts import (
    ArtifactRef,
    EnvironmentContract,
    EvaluatorContract,
    JobSpec,
    JobStatus,
    NetworkMode,
    PublicTaskContract,
    ResourceLimits,
    ResultManifest,
)
from shinka.secure.coordinator import PreparedTask
from shinka.secure.dependencies import DependencyBundle
from shinka.secure.errors import ArtifactIntegrityError
from .secure import SecureEvaluationScheduler

SCHEMA = "shinka-aws-evaluation-v1"


def prepared_from_dict(value: dict) -> PreparedTask:
    data = dict(value)
    for name in ("candidate", "evaluator"):
        data[name] = ArtifactRef(**data[name])
    dependencies = dict(data["dependencies"])
    for name in ("artifact", "runtime_artifact"):
        dependencies[name] = ArtifactRef(**dependencies[name])
    dependencies["runtime_names"] = tuple(dependencies["runtime_names"])
    data["dependencies"] = DependencyBundle(**dependencies)
    data["task_contract"] = PublicTaskContract.from_dict(data["task_contract"])
    data["evaluator_contract"] = EvaluatorContract.from_dict(data["evaluator_contract"])
    environment = dict(data["environment"])
    environment["limits"] = ResourceLimits(**environment["limits"])
    environment["network"] = NetworkMode(environment["network"])
    data["environment"] = EnvironmentContract(**environment)
    for name in ("candidate_snapshot", "evaluator_snapshot"):
        data[name] = SnapshotMetadata(**data[name])
    return PreparedTask(**data)


def _missing(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}


class AwsTransport:
    """Immutable artifacts, small queue messages, and conditional result publication."""

    def __init__(
        self,
        *,
        region: str,
        bucket: str,
        queue_url: str,
        prefix: str = "shinka",
        s3=None,
        sqs=None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{0,120}", prefix):
            raise ValueError("AWS prefix must be a safe nonempty object prefix")
        self.bucket, self.queue_url, self.prefix = bucket, queue_url, prefix.rstrip("/")
        self.region = region
        config = Config(
            retries={"mode": "standard", "max_attempts": 5},
            connect_timeout=10,
            read_timeout=30,
        )
        self.s3 = s3 or boto3.client("s3", region_name=region, config=config)
        self.sqs = sqs or boto3.client("sqs", region_name=region, config=config)

    def key(self, suffix: str) -> str:
        return f"{self.prefix}/{suffix}"

    def get_json(self, suffix: str) -> dict | None:
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=self.key(suffix))
        except ClientError as exc:
            if _missing(exc):
                return None
            raise
        with response["Body"] as body:
            data = body.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ArtifactIntegrityError("Remote JSON exceeds size limit")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ArtifactIntegrityError("Remote JSON must be an object")
        return value

    def put_json_once(self, suffix: str, value: dict) -> bool:
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self.key(suffix),
                Body=canonical_json_bytes(value),
                ContentType="application/json",
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
                return False
            raise

    def upload(self, store: ContentAddressedStore, ref: ArtifactRef) -> None:
        path = store.verify(ref.digest, expected_size=ref.size)
        key = self.key(f"objects/{ref.digest.removeprefix('sha256:')}")
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=key)
            if head["ContentLength"] != ref.size:
                raise ArtifactIntegrityError("Remote artifact size mismatch")
            return
        except ClientError as exc:
            if not _missing(exc):
                raise
        self.s3.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={
                "ServerSideEncryption": "AES256",
                "Metadata": {"sha256": ref.digest[7:]},
            },
        )

    def download(self, store: ContentAddressedStore, ref: ArtifactRef) -> Path:
        if ref.size > 2 * 1024**3:
            raise ArtifactIntegrityError("Remote artifact exceeds 2 GiB limit")
        if store.path_for(ref.digest).exists():
            return store.verify(ref.digest, expected_size=ref.size)
        key = self.key(f"objects/{ref.digest.removeprefix('sha256:')}")
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        if response["ContentLength"] != ref.size:
            response["Body"].close()
            raise ArtifactIntegrityError("Remote artifact length mismatch")
        with response["Body"] as body, tempfile.TemporaryFile() as temporary:
            size = 0
            while chunk := body.read(1024 * 1024):
                size += len(chunk)
                if size > ref.size:
                    raise ArtifactIntegrityError("Oversized remote artifact")
                temporary.write(chunk)
            if size != ref.size:
                raise ArtifactIntegrityError("Truncated remote artifact")
            temporary.seek(0)
            actual = store.put_stream(temporary, kind=ref.kind)
        if actual.digest != ref.digest:
            raise ArtifactIntegrityError("Remote artifact digest mismatch")
        return store.verify(ref.digest, expected_size=ref.size)


class AwsSecureEvaluationScheduler(SecureEvaluationScheduler):
    """Evolution-facing scheduler; AWS CPU concurrency is independent of the proposer."""

    job_type = "secure_aws"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        config = self.config
        self.transport = AwsTransport(
            region=config.aws_region,
            bucket=config.aws_bucket,
            queue_url=config.aws_queue_url,
            prefix=config.aws_prefix,
        )
        self.remote_state = self.coordinator.state_root / "aws-jobs"
        self.remote_state.mkdir(mode=0o700, exist_ok=True)
        # A new state directory means an independent experiment, even if its
        # basename or candidate names match an earlier run. Resumes reuse this id.
        run_file = self.remote_state / "run-id"
        temporary = self.remote_state / f"run-{uuid.uuid4().hex}.tmp"
        temporary.write_text(str(uuid.uuid4()))
        try:
            os.link(temporary, run_file)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
        self._run_id = str(uuid.UUID(run_file.read_text()))
        self._remote_results: dict[str, dict] = {}
        store = self.coordinator.artifacts
        template = {
            "schema": SCHEMA,
            "prepared": asdict(self.prepared),
            "candidate_command": config.candidate_command,
            "build_command": config.build_command,
            "resources": asdict(config.resources),
            "timeouts": asdict(config.timeouts),
            "sandbox_user": "65532:65532",
        }
        self.template = store.put_bytes(canonical_json_bytes(template), kind="aws_task")
        for ref in (
            self.prepared.candidate,
            self.prepared.evaluator,
            self.prepared.dependencies.artifact,
            self.prepared.dependencies.runtime_artifact,
            self.template,
        ):
            self.transport.upload(store, ref)

    def _request(self, job_id: str) -> dict:
        # Never interpolate an arbitrary SQS string as a filesystem path.
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("Invalid AWS job id")
        return json.loads((self.remote_state / f"{job_id}.json").read_text())

    def submit_async(self, repo_path_t: str, results_dir_t: str) -> str:
        del results_dir_t
        candidate = self._candidate_artifact(repo_path_t)
        self.transport.upload(self.coordinator.artifacts, candidate)
        identity = {
            "schema": SCHEMA,
            "template": asdict(self.template),
            "candidate": asdict(candidate),
            "run_id": self._run_id,
            "individual_id": Path(repo_path_t).name,
            "timeout_seconds": self.config.aws_job_timeout_seconds,
        }
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest_json(identity)))
        path = self.remote_state / f"{job_id}.json"
        request = (
            json.loads(path.read_text())
            if path.exists()
            else self.transport.get_json(f"jobs/{job_id}/request.json")
        )
        request = request or {**identity, "job_id": job_id, "submitted_at": time.time()}
        if any(request.get(key) != value for key, value in identity.items()):
            raise ArtifactIntegrityError(
                "Existing AWS request has different immutable inputs"
            )
        # Atomic durable intent before publication; re-submission uses the same identity.
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(canonical_json_bytes(request))
        temporary.replace(path)
        self.transport.put_json_once(f"jobs/{job_id}/request.json", request)
        # A crash after S3 publication but before send must be recoverable by re-submitting.
        # Duplicate SQS deliveries are harmless: workers publish results only once.
        queue_options = (
            {"MessageGroupId": job_id, "MessageDeduplicationId": job_id}
            if self.transport.queue_url.endswith(".fifo")
            else {}
        )
        self.transport.sqs.send_message(
            QueueUrl=self.transport.queue_url,
            MessageBody=json.dumps({"schema": SCHEMA, "job_id": job_id}),
            **queue_options,
        )
        return job_id

    def _result(self, job_id: str) -> dict | None:
        if job_id in self._remote_results:
            return self._remote_results[job_id]
        result = self.transport.get_json(f"jobs/{job_id}/result.json")
        if result is None:
            return None
        request = self._request(job_id)
        if result.get("job_id") != job_id or result.get(
            "request_digest"
        ) != digest_json(request):
            raise ArtifactIntegrityError("Remote result belongs to a different request")
        if "manifest" in result:
            manifest = ResultManifest.from_dict(result["manifest"])
            spec = JobSpec.from_dict(result["spec"])
            expected = {
                "candidate_digest": request["candidate"]["digest"],
                "evaluator_digest": self.prepared.evaluator.digest,
                "dependency_digest": self.prepared.dependencies.runtime_artifact.digest,
                "environment_digest": self.prepared.environment.digest,
                "task_contract_digest": self.prepared.task_contract.digest,
                "run_id": request["run_id"],
                "individual_id": request["individual_id"],
            }
            if any(getattr(spec, key) != value for key, value in expected.items()):
                raise ArtifactIntegrityError(
                    "Remote execution identities differ from the submission"
                )
            manifest.validate_against(
                spec,
                public_metric_allowlist=self.config.public_metric_allowlist,
                public_feedback_enabled=self.config.public_feedback_enabled,
                public_feedback_max_chars=self.config.public_feedback_max_chars,
            )
        self._remote_results[job_id] = result
        return result

    def check_job_status(self, job: Any) -> bool:
        identity = str(job.job_id if hasattr(job, "job_id") else job)
        if self._result(identity) is not None:
            return False
        request = self._request(identity)
        deadline = (
            request["submitted_at"]
            + self.config.queue_timeout_seconds
            + request["timeout_seconds"]
        )
        if time.time() > deadline:
            self.transport.put_json_once(
                f"jobs/{identity}/cancel.json", {"cancelled": True}
            )
            self._remote_results[identity] = {
                "job_id": identity,
                "request_digest": digest_json(request),
                "failure_class": "queue_timeout",
            }
            return False
        return True

    def get_job_results(self, job_id: Any, results_dir: str) -> dict:
        identity = str(job_id)
        result = self._result(identity)
        if result is None:
            raise RuntimeError("AWS job has no terminal result yet")
        if "manifest" not in result:
            return {
                "job_failure": {
                    "failure_class": result.get("failure_class", "evaluator_failed"),
                    "evaluation_job_id": identity,
                }
            }
        manifest = ResultManifest.from_dict(result["manifest"])
        identities = self.get_identity(identity)
        output = Path(results_dir)
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        (output / "public_result.json").write_bytes(
            canonical_json_bytes(manifest.public_view())
        )
        if manifest.status is JobStatus.FAILED:
            return {
                "job_failure": {
                    "failure_class": manifest.failure_class.value,
                    **identities,
                },
                "secure_identities": identities,
            }
        return {
            "correct": {"correct": manifest.correct},
            "metrics": {
                "combined_score": manifest.combined_score,
                "public": dict(manifest.public_metrics),
                "public_feedback": manifest.public_feedback or "",
            },
            "secure_identities": identities,
            "stdout_log": "",
            "stderr_log": "",
        }

    def run(self, repo_path_t: str, results_dir_t: str):
        started = time.monotonic()
        job_id = self.submit_async(repo_path_t, results_dir_t)
        deadline = (
            started
            + self.config.queue_timeout_seconds
            + self.config.aws_job_timeout_seconds
        )
        while self.check_job_status(job_id):
            if time.monotonic() > deadline:
                self.transport.put_json_once(
                    f"jobs/{job_id}/cancel.json", {"cancelled": True}
                )
                raise TimeoutError(
                    "AWS evaluation exceeded queue plus execution deadline"
                )
            time.sleep(self.config.aws_poll_seconds)
        return self.get_job_results(job_id, results_dir_t), time.monotonic() - started

    def get_identity(self, job_id: Any) -> dict:
        identity = str(job_id)
        request = self._request(identity)
        result = self._result(identity)
        values = {
            "candidate_digest": request["candidate"]["digest"],
            "evaluation_job_id": identity,
            "aws_task_digest": self.template.digest,
        }
        if result and "manifest" in result:
            manifest = ResultManifest.from_dict(result["manifest"])
            values.update(
                {
                    name: getattr(manifest, name)
                    for name in (
                        "runtime_artifact_digest",
                        "evaluator_digest",
                        "dependency_digest",
                        "environment_digest",
                        "job_spec_digest",
                    )
                }
            )
            values.update(
                result_digest=manifest.digest, evaluation_attempt_id=manifest.attempt_id
            )
        return values

    def acknowledge_persisted(self, job_id: Any) -> None:
        self.transport.put_json_once(
            f"jobs/{job_id}/persisted.json", {"persisted": True}
        )

    async def cancel_job_async(self, job_id: Any) -> bool:
        identity = str(job_id)
        if await asyncio.to_thread(self._result, identity) is not None:
            return False
        await asyncio.to_thread(
            self.transport.put_json_once,
            f"jobs/{identity}/cancel.json",
            {"cancelled": True},
        )
        return True
