"""Secure local-container evaluation scheduler."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from shinka.secure.archive import DEFAULT_EXCLUDES
from shinka.secure.contracts import (
    EnvironmentContract,
    EvaluatorContract,
    JobStatus,
    NetworkMode,
    PublicTaskContract,
    ResourceLimits,
    TimeoutPolicy,
    validate_pinned_image,
)
from shinka.secure.coordinator import (
    PreparedTask,
    SecureEvaluationCoordinator,
    SecureJobHandle,
)
from shinka.secure.containers import DockerEngine
from shinka.secure.dependencies import DependencyManifest
from shinka.secure.errors import ConfigurationError, FailureClass
from shinka.secure.jobs import JobPhase

from .scheduler import JobConfig


@dataclass
class SecureJobConfig(JobConfig):
    """Evaluator and candidate-service contract for secure evaluation."""

    evaluator_repo_path: Optional[str] = None
    evaluator_entrypoint: str = "evaluate.py"
    evaluator_callable: str = "evaluate"
    private_data_manifest: Optional[str] = None
    dependency_manifest_path: Optional[str] = None
    build_image: Optional[str] = None
    runtime_image: Optional[str] = None
    candidate_command: List[str] = field(default_factory=list)
    build_command: List[str] = field(default_factory=list)
    required_paths: List[str] = field(default_factory=list)
    candidate_protocol: str = "framed_stdio_v1"
    public_metric_allowlist: List[str] = field(default_factory=list)
    public_feedback_enabled: bool = False
    public_feedback_max_chars: int = 2000
    dependency_https_hosts: List[str] = field(default_factory=list)
    container_executable: str = "docker"
    dedicated_container_vm: bool = False
    sandbox_user: Optional[str] = None
    cpus: float = 2.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    pids: int = 128
    open_files: int = 1024
    output_bytes: int = 64 * 1024 * 1024
    queue_timeout_seconds: float = 300.0
    startup_timeout_seconds: float = 1800.0
    heartbeat_timeout_seconds: float = 600.0
    request_timeout_seconds: Optional[float] = None
    wall_timeout_seconds: Optional[float] = 28_800.0
    collection_timeout_seconds: float = 300.0
    cleanup_timeout_seconds: float = 120.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def resources(self) -> ResourceLimits:
        return ResourceLimits(
            cpus=self.cpus,
            memory_bytes=self.memory_bytes,
            pids=self.pids,
            open_files=self.open_files,
            output_bytes=self.output_bytes,
        )

    @property
    def timeouts(self) -> TimeoutPolicy:
        return TimeoutPolicy(
            queue_seconds=self.queue_timeout_seconds,
            startup_seconds=self.startup_timeout_seconds,
            heartbeat_seconds=self.heartbeat_timeout_seconds,
            request_seconds=self.request_timeout_seconds,
            wall_seconds=self.wall_timeout_seconds,
            collection_seconds=self.collection_timeout_seconds,
            cleanup_seconds=self.cleanup_timeout_seconds,
        )


def validate_secure_job_config(
    config: SecureJobConfig,
    *,
    mutation_image: str | None,
) -> None:
    if not config.evaluator_repo_path:
        raise ConfigurationError("Secure mode requires job.evaluator_repo_path")
    if not config.candidate_command:
        raise ConfigurationError("Secure mode requires job.candidate_command")
    for label, value in (
        ("evo.mutation_image", mutation_image),
        ("job.build_image", config.build_image),
        ("job.runtime_image", config.runtime_image),
    ):
        if not value:
            raise ConfigurationError(
                f"Secure mode requires {label} pinned as name@sha256:<64 hex>"
            )
        try:
            validate_pinned_image(value)
        except ConfigurationError as exc:
            raise ConfigurationError(f"Invalid {label}: {exc}") from exc


def _load_dependency_manifest(path: Optional[str]) -> DependencyManifest:
    if path is None:
        return DependencyManifest()
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ConfigurationError("Dependency manifest is missing or unsafe")
    loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigurationError("Dependency manifest must be a YAML/JSON object")
    return DependencyManifest.from_dict(loaded)


class SecureEvaluationScheduler:
    """Evolution-facing facade over the durable secure coordinator."""

    job_type = "secure_local"

    def __init__(
        self,
        *,
        config: SecureJobConfig,
        state_root: str,
        candidate_source: str,
        mutation_image: str,
        task_id: str = "shinka-task",
        objective: str = "Improve the candidate while preserving correctness.",
        mutable_paths: Optional[List[str]] = None,
        agent_omitted_paths: Optional[List[str]] = None,
        verbose: bool = False,
    ) -> None:
        validate_secure_job_config(config, mutation_image=mutation_image)
        self.config = config
        self.verbose = verbose
        self.coordinator = SecureEvaluationCoordinator(
            state_root,
            engine=DockerEngine(
                config.container_executable,
                allow_rootful_dedicated_vm=config.dedicated_container_vm,
            ),
        )
        self.recovery_actions = self.coordinator.reconcile()
        task_contract = PublicTaskContract(
            task_id=task_id,
            objective=objective,
            candidate_protocol=config.candidate_protocol,
            mutable_paths=tuple(mutable_paths or ()),
            required_paths=tuple(config.required_paths),
            agent_omitted_paths=tuple(agent_omitted_paths or ()),
            public_metric_allowlist=tuple(config.public_metric_allowlist),
            public_feedback_enabled=config.public_feedback_enabled,
            public_feedback_max_chars=config.public_feedback_max_chars,
        )
        evaluator_contract = EvaluatorContract(
            entrypoint=config.evaluator_entrypoint,
            callable_name=config.evaluator_callable,
            private_data_manifest=config.private_data_manifest,
            public_metric_allowlist=tuple(config.public_metric_allowlist),
            public_feedback_enabled=config.public_feedback_enabled,
            public_feedback_max_chars=config.public_feedback_max_chars,
        )
        environment = EnvironmentContract(
            mutation_image=mutation_image,
            build_image=config.build_image or "",
            runtime_image=config.runtime_image or "",
            network=NetworkMode.DISABLED,
            limits=config.resources,
        )
        self.prepared: PreparedTask = self.coordinator.prepare_task(
            candidate_source=candidate_source,
            evaluator_source=config.evaluator_repo_path or "",
            task_contract=task_contract,
            evaluator_contract=evaluator_contract,
            dependency_manifest=_load_dependency_manifest(
                config.dependency_manifest_path
            ),
            environment=environment,
            dependency_https_hosts=config.dependency_https_hosts,
        )
        self._handles: dict[str, SecureJobHandle] = {}
        self._run_id = Path(state_root).resolve().parent.name or "shinka-run"

    def _candidate_artifact(self, candidate_path: str):
        reference, _metadata = self.coordinator.artifacts.put_tree(
            Path(candidate_path).resolve(),
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.coordinator.archive_limits,
        )
        return reference

    def submit_async(
        self,
        exec_fname_t: str,
        results_dir_t: str,
        repo_path_t: Optional[str] = None,
    ) -> str:
        del exec_fname_t, results_dir_t
        if not repo_path_t:
            raise ConfigurationError("Secure evaluation requires a candidate path")
        candidate = self._candidate_artifact(repo_path_t)
        individual_id = Path(repo_path_t).name
        idempotency_key = f"{individual_id}:{candidate.digest}"
        handle = self.coordinator.submit(
            prepared=self.prepared,
            candidate=candidate,
            run_id=self._run_id,
            individual_id=individual_id,
            idempotency_key=idempotency_key,
            candidate_command=tuple(self.config.candidate_command),
            build_command=tuple(self.config.build_command),
            resources=self.config.resources,
            timeouts=self.config.timeouts,
            sandbox_user=self.config.sandbox_user,
        )
        self._handles[handle.job_id] = handle
        return handle.job_id

    def run(
        self,
        exec_fname_t: str,
        results_dir_t: str,
        repo_path_t: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], float]:
        started = time.monotonic()
        job_id = self.submit_async(exec_fname_t, results_dir_t, repo_path_t)
        self.coordinator.wait(self._handles[job_id])
        return self.get_job_results(job_id, results_dir_t), time.monotonic() - started

    def check_job_status(self, job: Any) -> bool:
        job_id = str(job.job_id if hasattr(job, "job_id") else job)
        record = self.coordinator.jobs.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record.state not in {
            JobPhase.RESULT_VALIDATED,
            JobPhase.PERSISTED,
            JobPhase.FAILED,
            JobPhase.CLEANED,
        }

    def get_job_results(self, job_id: Any, results_dir: str) -> Dict[str, Any]:
        identity = str(job_id)
        record = self.coordinator.jobs.get(identity)
        if record is None:
            raise KeyError(identity)
        if record.state not in {
            JobPhase.RESULT_VALIDATED,
            JobPhase.PERSISTED,
            JobPhase.FAILED,
            JobPhase.CLEANED,
        }:
            handle = self._handles.get(identity) or SecureJobHandle(
                record.job_id, record.attempt_id, record.worker_pid
            )
            self.coordinator.wait(handle)
        manifest = self.coordinator.get_result(identity)
        identities = {
            "candidate_digest": manifest.candidate_digest,
            "runtime_artifact_digest": manifest.runtime_artifact_digest,
            "evaluator_digest": manifest.evaluator_digest,
            "dependency_digest": manifest.dependency_digest,
            "environment_digest": manifest.environment_digest,
            "job_spec_digest": manifest.job_spec_digest,
            "result_digest": manifest.digest,
            "evaluation_job_id": manifest.job_id,
            "evaluation_attempt_id": manifest.attempt_id,
        }
        if manifest.status is JobStatus.FAILED:
            return {
                "job_failure": {
                    "failure_class": (
                        manifest.failure_class.value
                        if manifest.failure_class
                        else FailureClass.EVALUATOR_FAILED.value
                    ),
                    **identities,
                },
                "secure_identities": identities,
            }
        result = {
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
        output = Path(results_dir)
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        (output / "public_result.json").write_text(
            json.dumps(manifest.public_view(), sort_keys=True),
            encoding="utf-8",
        )
        return result

    def acknowledge_persisted(self, job_id: Any) -> None:
        self.coordinator.acknowledge_persisted(str(job_id))

    def get_identity(self, job_id: Any) -> Dict[str, Optional[str]]:
        record = self.coordinator.jobs.get(str(job_id))
        if record is None:
            return {}
        return {
            "candidate_digest": record.candidate_digest,
            "runtime_artifact_digest": record.runtime_artifact_digest,
            "evaluator_digest": record.evaluator_digest,
            "dependency_digest": record.dependency_digest,
            "environment_digest": record.environment_digest,
            "job_spec_digest": record.spec_digest,
            "result_digest": record.result_digest,
            "evaluation_job_id": record.job_id,
            "evaluation_attempt_id": record.attempt_id,
        }

    async def submit_async_nonblocking(
        self,
        exec_fname_t: str,
        results_dir_t: str,
        repo_path_t: Optional[str] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.submit_async, exec_fname_t, results_dir_t, repo_path_t
        )

    async def check_job_status_async(self, job: Any) -> bool:
        return await asyncio.to_thread(self.check_job_status, job)

    async def get_job_results_async(
        self, job_id: Any, results_dir: str
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_job_results, job_id, results_dir)

    async def batch_check_status_async(self, jobs: List[Any]) -> List[bool]:
        return await asyncio.gather(*(self.check_job_status_async(job) for job in jobs))

    async def cancel_job_async(self, job_id: Any) -> bool:
        identity = str(job_id)
        record = self.coordinator.jobs.get(identity)
        if record is None or record.state in {
            JobPhase.RESULT_VALIDATED,
            JobPhase.CLEANED,
        }:
            return False
        await asyncio.to_thread(
            self.coordinator._mark_failed,
            record,
            FailureClass.CANCELLED,
            "Evaluation was explicitly cancelled",
        )
        return True

    def shutdown(self) -> None:
        self.coordinator.reconcile()
