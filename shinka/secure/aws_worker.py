"""Dedicated EC2 worker: one secure evaluation at a time, with SQS heartbeats."""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import fcntl
import json
import logging
import os
import platform
from pathlib import Path
import signal
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid

import boto3

from shinka.launch.aws import AwsTransport, SCHEMA, prepared_from_dict
from .canonical import canonical_json_bytes, digest_json
from .artifacts import ContentAddressedStore
from .contracts import ArtifactRef, ResourceLimits, TimeoutPolicy
from .containers import DockerEngine
from .coordinator import SecureEvaluationCoordinator
from .errors import FailureClass, SecureExecutionError
from .jobs import JobPhase

LOG = logging.getLogger(__name__)


def execute(request: dict, transport: AwsTransport, root: Path) -> dict:
    """Trusted child process. Candidate compilation and execution stay in containers."""
    if request.get("schema") != SCHEMA:
        raise ValueError("Unsupported AWS evaluation request")
    engine = DockerEngine(allow_rootful_dedicated_vm=True)
    coordinator = SecureEvaluationCoordinator(root, engine=engine)
    coordinator.reconcile()
    store = coordinator.artifacts
    template_path = transport.download(store, ArtifactRef(**request["template"]))
    template = json.loads(template_path.read_text())
    if template.get("schema") != SCHEMA:
        raise ValueError("Unsupported AWS task template")
    prepared = prepared_from_dict(template["prepared"])
    candidate = ArtifactRef(**request["candidate"])
    for ref in (
        prepared.candidate,
        prepared.evaluator,
        prepared.dependencies.artifact,
        prepared.dependencies.runtime_artifact,
        candidate,
    ):
        transport.download(store, ref)
    for image in sorted(
        {prepared.environment.build_image, prepared.environment.runtime_image}
    ):
        # Images are immutable and registry authentication is host-only.
        registry = image.split("/")[0]
        ecr = re.fullmatch(
            r"(\d{12})\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?", registry
        )
        if ecr:
            response = boto3.client("ecr", region_name=ecr[2]).get_authorization_token(
                registryIds=[ecr[1]]
            )
            token = response["authorizationData"][0]["authorizationToken"]
            username, password = base64.b64decode(token).decode().split(":", 1)
            subprocess.run(
                [
                    engine.executable,
                    "login",
                    registry,
                    "--username",
                    username,
                    "--password-stdin",
                ],
                input=password.encode(),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        subprocess.run(
            [engine.executable, "pull", image],
            check=True,
            timeout=600,
            stdout=subprocess.DEVNULL,
        )
    engine.preflight(
        images={prepared.environment.build_image, prepared.environment.runtime_image}
    )
    handle = coordinator.submit(
        prepared=prepared,
        candidate=candidate,
        run_id=request["run_id"],
        individual_id=request["individual_id"],
        idempotency_key=request["job_id"],
        candidate_command=tuple(template["candidate_command"]),
        build_command=tuple(template["build_command"]),
        resources=ResourceLimits(**template["resources"]),
        timeouts=TimeoutPolicy(**template["timeouts"]),
        sandbox_user=template["sandbox_user"],
    )
    coordinator.wait(handle)
    manifest = coordinator.get_result(handle.job_id)
    record = coordinator.jobs.get(handle.job_id)
    if record is None:
        raise RuntimeError("Worker lost durable job record")
    spec = record.spec
    spec_ref = store.put_bytes(canonical_json_bytes(spec), kind="job_spec")
    if spec_ref.digest != record.spec_digest:
        raise RuntimeError("Durable job specification digest mismatch")
    # Preserve complete private measurements, build failures, and immutable job inputs.
    for ref in manifest.artifacts:
        transport.upload(store, ref)
    operator_artifacts = []
    for role, digest in (
        ("job_spec", record.spec_digest),
        ("result", record.result_digest),
        ("diagnostic", record.diagnostic_digest),
        ("checkpoint", record.checkpoint_digest),
        ("runtime", record.runtime_artifact_digest),
    ):
        if digest:
            path = store.verify(digest)
            ref = ArtifactRef(
                digest=digest, size=path.stat().st_size, kind="worker_artifact"
            )
            transport.upload(store, ref)
            operator_artifacts.append({"role": role, **asdict(ref)})
    result = {
        "job_id": request["job_id"],
        "request_digest": digest_json(request),
        "manifest": asdict(manifest),
        "spec": spec,
        "operator_artifacts": operator_artifacts,
        "worker": {
            "machine": platform.machine(),
            "platform": platform.platform(),
            "instance_type": os.environ.get("SHINKA_WORKER_INSTANCE_TYPE"),
            "ami_id": os.environ.get("SHINKA_WORKER_AMI_ID"),
            "wheel_sha256": os.environ.get("SHINKA_WORKER_WHEEL_SHA256"),
        },
    }
    coordinator.acknowledge_persisted(handle.job_id)
    return result


def cleanup(root: Path) -> None:
    """Stop detached evaluator process groups and all containers owned by their jobs."""
    coordinator = SecureEvaluationCoordinator(
        root, engine=DockerEngine(allow_rootful_dedicated_vm=True)
    )
    for record in coordinator.jobs.list_recoverable():
        try:
            if record.state in {
                JobPhase.RESULT_VALIDATED,
                JobPhase.PERSISTED,
                JobPhase.FAILED,
            }:
                coordinator._terminate_worker(record)
            else:
                coordinator._mark_failed(
                    record,
                    FailureClass.CANCELLED,
                    "AWS execution was interrupted or cancelled",
                )
            coordinator.acknowledge_persisted(record.job_id)
        except Exception:
            LOG.exception("Could not clean up job %s", record.job_id)
            raise SecureExecutionError(
                FailureClass.CLEANUP_FAILED,
                "Worker cleanup failed; this host must not accept another evaluation",
            ) from None


def process_message(
    message: dict, transport: AwsTransport, root: Path, *, max_timeout: float = 39000
) -> None:
    envelope = json.loads(message["Body"])
    job_id = str(uuid.UUID(envelope["job_id"]))
    if envelope.get("schema") != SCHEMA or job_id != envelope["job_id"]:
        raise ValueError("Invalid queue envelope")
    receipt = message["ReceiptHandle"]
    suffix = f"jobs/{job_id}"
    if transport.get_json(f"{suffix}/result.json") is not None:
        transport.sqs.delete_message(
            QueueUrl=transport.queue_url, ReceiptHandle=receipt
        )
        return
    request = transport.get_json(f"{suffix}/request.json")
    if request is None or request.get("job_id") != job_id:
        raise ValueError("Queue message has no matching immutable request")
    timeout = request.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or not 60 <= timeout <= max_timeout:
        raise ValueError("Invalid execution timeout")
    job_root = root / "jobs" / job_id
    job_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Recover interrupted attempts before starting a fresh local coordinator DB.
    for prior in job_root.glob("secure-*"):
        cleanup(prior)
    attempt_root = job_root / f"secure-{uuid.uuid4().hex}"
    request_file = job_root / "request.json"
    request_file.write_bytes(canonical_json_bytes(request))
    result_file = job_root / "result.json"
    command = [
        sys.executable,
        "-m",
        "shinka.secure.aws_worker",
        "--execute",
        str(request_file),
        "--state-root",
        str(attempt_root),
        "--region",
        transport.region,
        "--bucket",
        transport.bucket,
        "--queue-url",
        transport.queue_url,
        "--prefix",
        transport.prefix,
        "--result-file",
        str(result_file),
    ]
    started = time.monotonic()
    terminal = None
    process = None
    with (job_root / "worker.log").open("ab") as log:
        try:
            if transport.get_json(f"{suffix}/cancel.json") is not None:
                terminal = "cancelled"
            else:
                process = subprocess.Popen(
                    command, stdout=log, stderr=log, start_new_session=True
                )
                while process.poll() is None:
                    if transport.get_json(f"{suffix}/cancel.json") is not None:
                        terminal = "cancelled"
                        break
                    if time.monotonic() - started > timeout:
                        terminal = "wall_timeout"
                        break
                    # Visibility always stays below SQS's 12-hour cap from receipt.
                    transport.sqs.change_message_visibility(
                        QueueUrl=transport.queue_url,
                        ReceiptHandle=receipt,
                        VisibilityTimeout=120,
                    )
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pass
                if terminal is None and process.returncode != 0:
                    raise RuntimeError(
                        "Evaluation worker failed; job will retry or reach DLQ"
                    )
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if attempt_root.exists():
                cleanup(attempt_root)
    if terminal:
        result = {
            "job_id": job_id,
            "request_digest": digest_json(request),
            "failure_class": terminal,
        }
    else:
        result = json.loads(result_file.read_text())
    log_store = ContentAddressedStore(job_root / "logs")
    log_ref = log_store.put_file(job_root / "worker.log", kind="operator_log")
    transport.upload(log_store, log_ref)
    result["worker_log"] = asdict(log_ref)
    transport.put_json_once(f"{suffix}/result.json", result)
    transport.sqs.delete_message(QueueUrl=transport.queue_url, ReceiptHandle=receipt)
    # All artifacts and the operator log are now durable in S3; bound worker disk use.
    shutil.rmtree(job_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--prefix", default="shinka")
    parser.add_argument("--state-root", type=Path, default=Path("/var/lib/shinka"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    transport = AwsTransport(
        region=args.region,
        bucket=args.bucket,
        queue_url=args.queue_url,
        prefix=args.prefix,
    )
    args.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.execute:
        request = json.loads(args.execute.read_text())
        try:
            result = execute(request, transport, args.state_root)
        except Exception as exc:
            # Build/protocol/policy failures are terminal; host crashes are retried by SQS.
            LOG.error("Evaluation failed\n%s", traceback.format_exc())
            if isinstance(exc, SecureExecutionError) and exc.private_diagnostic:
                LOG.error("Private diagnostic: %s", exc.private_diagnostic[:16000])
            result = {
                "job_id": request["job_id"],
                "request_digest": digest_json(request),
                "failure_class": (
                    exc.failure_class.value
                    if isinstance(exc, SecureExecutionError)
                    else "evaluator_failed"
                ),
            }
        args.result_file.write_bytes(canonical_json_bytes(result))
        return
    # Keep one lock on the entire host, including compilation and both timed processes.
    with (args.state_root / "worker.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for prior in (args.state_root / "jobs").glob("*/secure-*"):
            cleanup(prior)
        while True:
            messages = transport.sqs.receive_message(
                QueueUrl=transport.queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=120,
            ).get("Messages", [])
            for message in messages:
                try:
                    process_message(message, transport, args.state_root)
                except Exception as exc:
                    LOG.exception(
                        "SQS attempt failed; message remains available for retry"
                    )
                    if (
                        isinstance(exc, SecureExecutionError)
                        and exc.failure_class is FailureClass.CLEANUP_FAILED
                    ):
                        raise
            if args.once:
                break


if __name__ == "__main__":
    main()
