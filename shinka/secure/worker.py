"""Detached trusted evaluator worker entrypoint."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import threading
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .artifacts import ContentAddressedStore
from .canonical import canonical_json_bytes
from .contracts import ArtifactRef, JobSpec, PublicTaskContract
from .containers import DockerEngine
from .errors import FailureClass, SecureExecutionError
from .evaluator import (
    EvaluatorJobContext,
    PrivateInputs,
    ResultWriter,
    failure_manifest,
)
from .jobs import EvaluationJobStore, JobPhase
from .runtime import ContainerCandidateRunner

WORKER_REQUEST_VERSION = "shinka-evaluator-worker-v1"


@dataclass(frozen=True)
class WorkerRequest:
    state_root: str
    jobs_db: str
    job_spec: JobSpec
    task_contract: PublicTaskContract
    evaluator_artifact: ArtifactRef
    runtime_artifact: ArtifactRef
    dependency_artifact: ArtifactRef
    container_executable: str = "docker"
    sandbox_user: str | None = None
    schema_version: str = WORKER_REQUEST_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkerRequest":
        data = dict(value)
        data["job_spec"] = JobSpec.from_dict(data["job_spec"])
        data["task_contract"] = PublicTaskContract.from_dict(data["task_contract"])
        for name in (
            "evaluator_artifact",
            "runtime_artifact",
            "dependency_artifact",
        ):
            data[name] = ArtifactRef(**data[name])
        request = cls(**data)
        if request.schema_version != WORKER_REQUEST_VERSION:
            raise ValueError("Unsupported evaluator worker request")
        return request

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(asdict(self))


def _load_evaluator(root: Path, entrypoint: str, callable_name: str):
    path = (root / entrypoint).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError("Evaluator entrypoint leaves its snapshot") from exc
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Evaluator entrypoint is missing or unsafe")
    module_name = f"shinka_evaluator_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Evaluator module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec.loader.exec_module(module)
    evaluator = getattr(module, callable_name, None)
    if not callable(evaluator):
        raise RuntimeError("Evaluator callable is missing")
    return evaluator


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod((mode & 0o555) or 0o500)
        else:
            path.chmod(mode & 0o555)
    root.chmod(0o500)


def _make_removable(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_symlink():
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
    try:
        root.chmod(0o700)
    except OSError:
        pass


def _heartbeat_loop(
    jobs: EvaluationJobStore,
    job_id: str,
    interval: float,
    stop: threading.Event,
) -> None:
    while not stop.wait(interval):
        try:
            jobs.heartbeat(job_id, phase="evaluator")
        except Exception:
            return


def run_worker(request: WorkerRequest) -> int:
    state_root = Path(request.state_root).resolve()
    artifacts = ContentAddressedStore(state_root / "artifacts")
    jobs = EvaluationJobStore(request.jobs_db)
    spec = request.job_spec
    attempt_root = state_root / "workers" / spec.attempt_id
    evaluator_root = attempt_root / "evaluator"
    staging = attempt_root / "result.json"
    stop_heartbeat = threading.Event()
    heartbeat: threading.Thread | None = None
    runner: ContainerCandidateRunner | None = None
    failure: FailureClass | None = None
    diagnostic: str | None = None
    try:
        attempt_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        current = jobs.get(spec.job_id)
        if current is None or current.state is not JobPhase.STARTING:
            raise RuntimeError("Worker launch intent is missing or stale")
        jobs.transition(
            spec.job_id,
            JobPhase.RUNNING,
            expected={JobPhase.STARTING},
            backend_id=f"worker:{os.getpid()}",
            worker_pid=os.getpid(),
        )
        interval = max(1.0, min(30.0, spec.timeouts.heartbeat_seconds / 3.0))
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(jobs, spec.job_id, interval, stop_heartbeat),
            daemon=True,
        )
        heartbeat.start()
        artifacts.materialize_archive(request.evaluator_artifact, evaluator_root)
        _make_read_only(evaluator_root)
        private_inputs = PrivateInputs.load(
            evaluator_root, spec.evaluator_contract.private_data_manifest
        )
        evaluator = _load_evaluator(
            evaluator_root,
            spec.evaluator_contract.entrypoint,
            spec.evaluator_contract.callable_name,
        )
        runner = ContainerCandidateRunner(
            engine=DockerEngine(request.container_executable),
            artifacts=artifacts,
            runtime_artifact=request.runtime_artifact,
            dependency_artifact=request.dependency_artifact,
            image=spec.runtime_image,
            command=spec.candidate_command,
            limits=spec.resources,
            job_id=spec.job_id,
            attempt_id=spec.attempt_id,
            startup_timeout_seconds=spec.timeouts.startup_seconds,
            request_timeout_seconds=spec.timeouts.request_seconds,
            sandbox_user=request.sandbox_user,
        )
        context = EvaluatorJobContext(spec, jobs, artifacts)
        writer = ResultWriter(
            spec=spec,
            task_contract=request.task_contract,
            artifacts=artifacts,
        )
        with runner:
            evaluator(context, runner, private_inputs, writer)
        runner = None
        jobs.transition(spec.job_id, JobPhase.COLLECTING)
        manifest = writer.manifest
        manifest.validate_against(
            spec,
            public_metric_allowlist=request.task_contract.public_metric_allowlist,
            public_feedback_enabled=request.task_contract.public_feedback_enabled,
            public_feedback_max_chars=request.task_contract.public_feedback_max_chars,
        )
        payload = canonical_json_bytes(asdict(manifest))
        with staging.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        result_reference = artifacts.put_bytes(payload, kind="result_manifest")
        jobs.transition(
            spec.job_id,
            JobPhase.RESULT_VALIDATED,
            result_digest=result_reference.digest,
        )
        return 0
    except BaseException as exc:
        if isinstance(exc, SecureExecutionError):
            failure = exc.failure_class
            diagnostic = exc.private_diagnostic or str(exc)
        else:
            failure = FailureClass.EVALUATOR_FAILED
            diagnostic = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-64_000:]
        try:
            failure_result = failure_manifest(spec, failure)
            failure_result.validate_against(
                spec,
                public_metric_allowlist=request.task_contract.public_metric_allowlist,
                public_feedback_enabled=request.task_contract.public_feedback_enabled,
                public_feedback_max_chars=request.task_contract.public_feedback_max_chars,
            )
            result_ref = artifacts.put_bytes(
                canonical_json_bytes(asdict(failure_result)),
                kind="failure_result_manifest",
            )
            diagnostic_ref = artifacts.put_bytes(
                (diagnostic or "worker failure").encode("utf-8", errors="replace"),
                kind="operator_diagnostic",
            )
            record = jobs.get(spec.job_id)
            if record is not None and record.state not in {
                JobPhase.FAILED,
                JobPhase.CLEANED,
            }:
                jobs.transition(
                    spec.job_id,
                    JobPhase.FAILED,
                    failure_class=failure,
                    diagnostic_digest=diagnostic_ref.digest,
                    result_digest=result_ref.digest,
                )
        except Exception:
            pass
        return 1
    finally:
        stop_heartbeat.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2.0)
        if runner is not None:
            try:
                runner.stop()
            except Exception:
                pass
        _make_removable(evaluator_root)
        shutil.rmtree(attempt_root, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    request_path = args.request.resolve()
    loaded = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit("Worker request must be a JSON object")
    request = WorkerRequest.from_dict(loaded)
    return run_worker(request)


if __name__ == "__main__":
    raise SystemExit(main())
