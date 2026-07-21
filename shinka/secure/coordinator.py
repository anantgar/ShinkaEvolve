"""Trusted local coordinator for snapshots, builds, workers, and recovery."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .archive import ArchiveLimits, DEFAULT_EXCLUDES, SnapshotMetadata
from .artifacts import ContentAddressedStore
from .build import SecureBuildBackend
from .canonical import canonical_json_bytes
from .contracts import (
    ArtifactRef,
    EnvironmentContract,
    EvaluatorContract,
    JobSpec,
    PublicTaskContract,
    ResourceLimits,
    ResultManifest,
    TimeoutPolicy,
)
from .containers import DockerEngine
from .dependencies import DependencyBundle, DependencyManifest, DependencyPreparer
from .errors import FailureClass, SecureExecutionError, SecurityPolicyError
from .evaluator import failure_manifest
from .jobs import EvaluationJobStore, JobPhase, JobRecord
from .worker import WorkerRequest

BUILD_RESULT_SCHEMA_VERSION = "shinka-build-result-v1"


@dataclass(frozen=True)
class PreparedTask:
    candidate: ArtifactRef
    evaluator: ArtifactRef
    dependencies: DependencyBundle
    task_contract: PublicTaskContract
    evaluator_contract: EvaluatorContract
    environment: EnvironmentContract
    candidate_snapshot: SnapshotMetadata
    evaluator_snapshot: SnapshotMetadata


@dataclass(frozen=True)
class SecureJobHandle:
    job_id: str
    attempt_id: str
    worker_pid: int | None = None


@dataclass(frozen=True)
class ReconciliationAction:
    job_id: str
    action: str
    detail: str = ""


class SecureEvaluationCoordinator:
    """One local-container execution path; raw evaluation is not available."""

    def __init__(
        self,
        state_root: Path | str,
        *,
        engine: DockerEngine | None = None,
        archive_limits: ArchiveLimits = ArchiveLimits(),
    ) -> None:
        root = Path(state_root).expanduser()
        if root.exists() and root.is_symlink():
            raise SecurityPolicyError("Secure state root cannot be a symlink")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        self.state_root = root.resolve()
        self.artifacts = ContentAddressedStore(self.state_root / "artifacts")
        self.jobs = EvaluationJobStore(self.state_root / "jobs.sqlite")
        self.engine = engine or DockerEngine()
        self.archive_limits = archive_limits
        self.requests = self.state_root / "requests"
        self.logs = self.state_root / "logs"
        self.requests.mkdir(mode=0o700, exist_ok=True)
        self.logs.mkdir(mode=0o700, exist_ok=True)
        self._workers: dict[str, subprocess.Popen[bytes]] = {}

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    def prepare_task(
        self,
        *,
        candidate_source: Path | str,
        evaluator_source: Path | str,
        task_contract: PublicTaskContract,
        evaluator_contract: EvaluatorContract,
        dependency_manifest: DependencyManifest,
        environment: EnvironmentContract,
        dependency_https_hosts: Sequence[str] = (),
    ) -> PreparedTask:
        candidate_path = Path(candidate_source).resolve()
        evaluator_path = Path(evaluator_source).resolve()
        if not candidate_path.is_dir() or not evaluator_path.is_dir():
            raise SecurityPolicyError(
                "Candidate and evaluator sources must be directories"
            )
        if candidate_path == evaluator_path or self._is_within(
            evaluator_path, candidate_path
        ):
            raise SecurityPolicyError(
                "Candidate source cannot contain the evaluator source"
            )
        if self._is_within(self.state_root, candidate_path) or self._is_within(
            self.state_root, evaluator_path
        ):
            raise SecurityPolicyError("Secure state must be outside snapshot sources")
        entrypoint = evaluator_path / evaluator_contract.entrypoint
        if not entrypoint.is_file():
            raise SecurityPolicyError("Evaluator entrypoint is missing")
        if (
            evaluator_contract.private_data_manifest
            and not (
                evaluator_path / evaluator_contract.private_data_manifest
            ).is_file()
        ):
            raise SecurityPolicyError("Evaluator private-data manifest is missing")

        images = {
            environment.mutation_image,
            environment.build_image,
            environment.runtime_image,
            *dependency_manifest.images,
            *(item.image for item in dependency_manifest.system),
        }
        self.engine.preflight(
            images=images,
            provider_network=None,
        )
        candidate, candidate_meta = self.artifacts.put_tree(
            candidate_path,
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.archive_limits,
        )
        evaluator_excludes = list(DEFAULT_EXCLUDES)
        if self._is_within(candidate_path, evaluator_path):
            relative_candidate = candidate_path.relative_to(evaluator_path).as_posix()
            evaluator_excludes.extend([relative_candidate, f"{relative_candidate}/**"])
        evaluator, evaluator_meta = self.artifacts.put_tree(
            evaluator_path,
            kind="evaluator",
            excludes=tuple(evaluator_excludes),
            limits=self.archive_limits,
        )
        dependencies = DependencyPreparer(
            self.artifacts,
            allowed_https_hosts=dependency_https_hosts,
        ).prepare(dependency_manifest)
        return PreparedTask(
            candidate=candidate,
            evaluator=evaluator,
            dependencies=dependencies,
            task_contract=task_contract,
            evaluator_contract=evaluator_contract,
            environment=environment,
            candidate_snapshot=candidate_meta,
            evaluator_snapshot=evaluator_meta,
        )

    @staticmethod
    def _stable_ids(run_id: str, idempotency_key: str, attempt: int) -> tuple[str, str]:
        job_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, f"shinka-evaluation:{run_id}:{idempotency_key}"
            )
        )
        attempt_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"shinka-evaluation:{run_id}:{idempotency_key}:attempt:{attempt}",
            )
        )
        return job_id, attempt_id

    def _artifact_ref(self, digest: str, *, kind: str) -> ArtifactRef:
        path = self.artifacts.verify(digest)
        return ArtifactRef(digest=digest, size=path.stat().st_size, kind=kind)

    def _build_candidate(
        self,
        *,
        prepared: PreparedTask,
        candidate: ArtifactRef,
        build_command: tuple[str, ...],
        run_id: str,
        individual_id: str,
        idempotency_key: str,
        resources: ResourceLimits,
        timeouts: TimeoutPolicy,
        sandbox_user: str | None,
    ) -> ArtifactRef:
        if not build_command:
            return ArtifactRef(
                digest=candidate.digest,
                size=candidate.size,
                kind="runtime",
            )
        build_key = (
            f"build:{idempotency_key}:{candidate.digest}:{prepared.environment.digest}"
        )
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, build_key))
        attempt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{build_key}:attempt:1"))
        spec = JobSpec(
            job_id=job_id,
            run_id=run_id,
            individual_id=individual_id,
            attempt_id=attempt_id,
            candidate_digest=candidate.digest,
            runtime_artifact_digest=candidate.digest,
            evaluator_digest=prepared.evaluator.digest,
            dependency_digest=prepared.dependencies.artifact.digest,
            environment_digest=prepared.environment.digest,
            task_contract_digest=prepared.task_contract.digest,
            evaluator_contract=prepared.evaluator_contract,
            runtime_image=prepared.environment.build_image,
            candidate_command=build_command,
            build_command=build_command,
            timeouts=timeouts,
            resources=resources,
        )
        record = self.jobs.prepare(
            spec,
            idempotency_key=build_key,
            kind="build",
        )
        if (
            record.state
            in {
                JobPhase.RESULT_VALIDATED,
                JobPhase.PERSISTED,
                JobPhase.CLEANED,
            }
            and record.result_digest
        ):
            manifest_path = self.artifacts.verify(record.result_digest)
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if loaded.get("schema_version") != BUILD_RESULT_SCHEMA_VERSION:
                raise SecurityPolicyError(
                    "Persisted build result has an invalid schema"
                )
            return ArtifactRef(**loaded["runtime_artifact"])
        if record.state is not JobPhase.PREPARED:
            raise SecurityPolicyError("Existing build attempt is incomplete")

        try:
            self.jobs.transition(job_id, JobPhase.QUEUED)
            self.jobs.transition(job_id, JobPhase.STARTING)
            self.jobs.transition(job_id, JobPhase.RUNNING)
            result = SecureBuildBackend(
                engine=self.engine,
                artifacts=self.artifacts,
                image=prepared.environment.build_image,
                limits=resources,
                sandbox_user=sandbox_user,
                archive_limits=self.archive_limits,
            ).build(
                candidate=candidate,
                dependencies=prepared.dependencies.artifact,
                command=build_command,
                job_id=job_id,
                attempt_id=attempt_id,
                timeout_seconds=timeouts.startup_seconds,
            )
            self.jobs.transition(job_id, JobPhase.COLLECTING)
            build_manifest = {
                "schema_version": BUILD_RESULT_SCHEMA_VERSION,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "candidate_digest": candidate.digest,
                "dependency_digest": prepared.dependencies.artifact.digest,
                "environment_digest": prepared.environment.digest,
                "runtime_artifact": asdict(result.runtime_artifact),
            }
            reference = self.artifacts.put_bytes(
                canonical_json_bytes(build_manifest), kind="build_result_manifest"
            )
            self.jobs.transition(
                job_id,
                JobPhase.RESULT_VALIDATED,
                result_digest=reference.digest,
            )
            self.jobs.transition(job_id, JobPhase.PERSISTED)
            self.jobs.transition(job_id, JobPhase.CLEANED)
            return result.runtime_artifact
        except BaseException as exc:
            failure = (
                exc.failure_class
                if isinstance(exc, SecureExecutionError)
                else FailureClass.BUILD_FAILED
            )
            diagnostic = self.artifacts.put_bytes(
                str(exc).encode("utf-8", errors="replace"),
                kind="operator_diagnostic",
            )
            current = self.jobs.get(job_id)
            if current and current.state not in {JobPhase.FAILED, JobPhase.CLEANED}:
                self.jobs.transition(
                    job_id,
                    JobPhase.FAILED,
                    failure_class=failure,
                    diagnostic_digest=diagnostic.digest,
                )
            current = self.jobs.get(job_id)
            if current and current.state is JobPhase.FAILED:
                self.jobs.transition(job_id, JobPhase.CLEANED)
            raise

    def submit(
        self,
        *,
        prepared: PreparedTask,
        candidate: ArtifactRef | None,
        run_id: str,
        individual_id: str,
        idempotency_key: str,
        candidate_command: tuple[str, ...],
        build_command: tuple[str, ...] = (),
        resources: ResourceLimits | None = None,
        timeouts: TimeoutPolicy | None = None,
        attempt: int = 1,
        sandbox_user: str | None = None,
    ) -> SecureJobHandle:
        resources = resources or prepared.environment.limits
        timeouts = timeouts or TimeoutPolicy()
        candidate = candidate or prepared.candidate
        runtime = self._build_candidate(
            prepared=prepared,
            candidate=candidate,
            build_command=build_command,
            run_id=run_id,
            individual_id=individual_id,
            idempotency_key=idempotency_key,
            resources=resources,
            timeouts=timeouts,
            sandbox_user=sandbox_user,
        )
        job_id, attempt_id = self._stable_ids(run_id, idempotency_key, attempt)
        spec = JobSpec(
            job_id=job_id,
            run_id=run_id,
            individual_id=individual_id,
            attempt_id=attempt_id,
            candidate_digest=candidate.digest,
            runtime_artifact_digest=runtime.digest,
            evaluator_digest=prepared.evaluator.digest,
            dependency_digest=prepared.dependencies.runtime_artifact.digest,
            environment_digest=prepared.environment.digest,
            task_contract_digest=prepared.task_contract.digest,
            evaluator_contract=prepared.evaluator_contract,
            runtime_image=prepared.environment.runtime_image,
            candidate_command=candidate_command,
            build_command=build_command,
            timeouts=timeouts,
            resources=resources,
        )
        request_path = self.requests / f"{attempt_id}.json"
        request = WorkerRequest(
            state_root=str(self.state_root),
            jobs_db=str(self.jobs.path),
            job_spec=spec,
            task_contract=prepared.task_contract,
            evaluator_artifact=prepared.evaluator,
            runtime_artifact=runtime,
            dependency_artifact=prepared.dependencies.runtime_artifact,
            container_executable=self.engine.executable,
            sandbox_user=sandbox_user,
        )
        # The immutable request is durable before the PREPARED state becomes
        # visible. No untrusted side effect has happened at this point.
        if self.jobs.get(job_id) is None:
            self._persist_worker_request(request_path, request)
        record = self.jobs.prepare(
            spec,
            idempotency_key=f"evaluation:{idempotency_key}:attempt:{attempt}",
            staging_path=str(self.state_root / "workers" / attempt_id / "result.json"),
        )
        if record.state is not JobPhase.PREPARED:
            return SecureJobHandle(
                job_id=job_id,
                attempt_id=attempt_id,
                worker_pid=record.worker_pid,
            )
        self._persist_worker_request(request_path, request)
        self.jobs.transition(job_id, JobPhase.QUEUED)
        self.jobs.transition(job_id, JobPhase.STARTING)
        process = self._launch_worker(request_path, attempt_id)
        self.jobs.record_worker_launch(
            job_id,
            worker_pid=process.pid,
            backend_id=f"worker:{process.pid}",
        )
        self._workers[job_id] = process
        return SecureJobHandle(
            job_id=job_id, attempt_id=attempt_id, worker_pid=process.pid
        )

    def _launch_worker(
        self,
        request_path: Path,
        attempt_id: str,
    ) -> subprocess.Popen[bytes]:
        stdout_path = self.logs / f"{attempt_id}.stdout.log"
        stderr_path = self.logs / f"{attempt_id}.stderr.log"
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LC_ALL": "C.UTF-8",
        }
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "XDG_RUNTIME_DIR"):
            if name in os.environ:
                environment[name] = os.environ[name]
        with (
            stdout_path.open("ab", buffering=0) as stdout,
            stderr_path.open("ab", buffering=0) as stderr,
        ):
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "shinka.secure.worker",
                    "--request",
                    str(request_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=environment,
                cwd=self.state_root,
                start_new_session=True,
            )

    @staticmethod
    def _persist_worker_request(path: Path, request: WorkerRequest) -> None:
        payload = request.to_json_bytes()
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise SecurityPolicyError(
                    "Durable worker request conflicts with immutable launch intent"
                )
            return
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != payload:
                    raise SecurityPolicyError(
                        "Concurrent worker request conflicts with launch intent"
                    )
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _pid_is_worker(record: JobRecord) -> bool:
        if not record.worker_pid:
            return False
        try:
            import psutil

            process = psutil.Process(record.worker_pid)
            command = " ".join(process.cmdline())
            return "shinka.secure.worker" in command and record.attempt_id in command
        except Exception:
            return False

    def _terminate_worker(self, record: JobRecord) -> None:
        if not self._pid_is_worker(record):
            return
        assert record.worker_pid is not None
        try:
            os.killpg(record.worker_pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and self._pid_is_worker(record):
            time.sleep(0.1)
        if self._pid_is_worker(record):
            try:
                os.killpg(record.worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _cleanup_job_containers(self, record: JobRecord) -> None:
        for handle in self.engine.list_managed():
            if (
                handle.job_id == record.job_id
                and handle.attempt_id == record.attempt_id
            ):
                self.engine.stop(handle, timeout_seconds=5.0)
                self.engine.remove(handle, force=True)

    def _mark_failed(
        self,
        record: JobRecord,
        failure: FailureClass,
        message: str,
    ) -> JobRecord:
        spec = JobSpec.from_dict(record.spec)
        manifest = failure_manifest(spec, failure)
        manifest.validate_against(
            spec,
            public_metric_allowlist=spec.evaluator_contract.public_metric_allowlist,
            public_feedback_enabled=spec.evaluator_contract.public_feedback_enabled,
            public_feedback_max_chars=spec.evaluator_contract.public_feedback_max_chars,
        )
        result = self.artifacts.put_bytes(
            canonical_json_bytes(asdict(manifest)),
            kind="failure_result_manifest",
        )
        diagnostic = self.artifacts.put_bytes(
            message.encode("utf-8", errors="replace"),
            kind="operator_diagnostic",
        )
        self._terminate_worker(record)
        self._cleanup_job_containers(record)
        current = self.jobs.get(record.job_id)
        if current is None:
            raise KeyError(record.job_id)
        if current.state not in {JobPhase.FAILED, JobPhase.CLEANED}:
            return self.jobs.transition(
                record.job_id,
                JobPhase.FAILED,
                failure_class=failure,
                diagnostic_digest=diagnostic.digest,
                result_digest=result.digest,
            )
        return current

    def wait(
        self,
        handle: SecureJobHandle,
        *,
        poll_seconds: float = 0.25,
    ) -> JobRecord:
        while True:
            record = self.jobs.get(handle.job_id)
            if record is None:
                raise KeyError(handle.job_id)
            if record.state in {
                JobPhase.RESULT_VALIDATED,
                JobPhase.FAILED,
                JobPhase.PERSISTED,
                JobPhase.CLEANED,
            }:
                return record
            spec = JobSpec.from_dict(record.spec)
            now = time.time()
            reference_time = record.started_at or record.created_at
            if (
                record.state is JobPhase.QUEUED
                and record.queued_at is not None
                and (now - record.queued_at > spec.timeouts.queue_seconds)
            ):
                return self._mark_failed(
                    record,
                    FailureClass.QUEUE_TIMEOUT,
                    "Evaluation exceeded its explicit queue timeout",
                )
            if (
                record.state is JobPhase.STARTING
                and record.queued_at is not None
                and (now - record.queued_at > spec.timeouts.startup_seconds)
            ):
                return self._mark_failed(
                    record,
                    FailureClass.STARTUP_TIMEOUT,
                    "Evaluation worker exceeded its explicit startup timeout",
                )
            if spec.timeouts.wall_seconds is not None and (
                now - reference_time > spec.timeouts.wall_seconds
            ):
                return self._mark_failed(
                    record,
                    FailureClass.WALL_TIMEOUT,
                    "Evaluation exceeded its explicit wall timeout",
                )
            if record.heartbeat_at is not None and (
                now - record.heartbeat_at > spec.timeouts.heartbeat_seconds
            ):
                return self._mark_failed(
                    record,
                    FailureClass.HEARTBEAT_TIMEOUT,
                    "Evaluation heartbeat became stale",
                )
            process = self._workers.get(handle.job_id)
            if process is not None and process.poll() is not None:
                refreshed = self.jobs.get(handle.job_id)
                if refreshed and refreshed.state in {
                    JobPhase.STARTING,
                    JobPhase.RUNNING,
                    JobPhase.COLLECTING,
                }:
                    return self._mark_failed(
                        refreshed,
                        FailureClass.WORKER_LOST,
                        "Evaluator worker exited without a terminal record",
                    )
            time.sleep(poll_seconds)

    def get_result(self, job_id: str) -> ResultManifest:
        record = self.jobs.get(job_id)
        if record is None or record.result_digest is None:
            raise SecurityPolicyError("Job has no authoritative result manifest")
        if record.state not in {
            JobPhase.RESULT_VALIDATED,
            JobPhase.PERSISTED,
            JobPhase.CLEANED,
            JobPhase.FAILED,
        }:
            raise SecurityPolicyError("Job result is not terminal")
        path = self.artifacts.verify(record.result_digest)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        manifest = ResultManifest.from_dict(loaded)
        spec = JobSpec.from_dict(record.spec)
        allowlist = spec.evaluator_contract.public_metric_allowlist
        manifest.validate_against(
            spec,
            public_metric_allowlist=allowlist,
            public_feedback_enabled=spec.evaluator_contract.public_feedback_enabled,
            public_feedback_max_chars=spec.evaluator_contract.public_feedback_max_chars,
        )
        return manifest

    def _recover_staged_result(self, record: JobRecord) -> JobRecord | None:
        if record.state is not JobPhase.COLLECTING or not record.staging_path:
            return None
        staging = Path(record.staging_path)
        try:
            staging.resolve().relative_to(self.state_root)
        except ValueError:
            return None
        if not staging.is_file() or staging.is_symlink():
            return None
        try:
            loaded = json.loads(staging.read_text(encoding="utf-8"))
            manifest = ResultManifest.from_dict(loaded)
            spec = JobSpec.from_dict(record.spec)
            manifest.validate_against(
                spec,
                public_metric_allowlist=spec.evaluator_contract.public_metric_allowlist,
                public_feedback_enabled=spec.evaluator_contract.public_feedback_enabled,
                public_feedback_max_chars=spec.evaluator_contract.public_feedback_max_chars,
            )
            reference = self.artifacts.put_bytes(
                canonical_json_bytes(asdict(manifest)), kind="result_manifest"
            )
            return self.jobs.transition(
                record.job_id,
                JobPhase.RESULT_VALIDATED,
                result_digest=reference.digest,
                event_type="staged_result_recovered",
            )
        except Exception:
            return None

    def acknowledge_persisted(self, job_id: str) -> JobRecord:
        record = self.jobs.get(job_id)
        if record is None:
            raise KeyError(job_id)
        if record.state is JobPhase.RESULT_VALIDATED:
            record = self.jobs.transition(job_id, JobPhase.PERSISTED)
        if record.state not in {JobPhase.PERSISTED, JobPhase.FAILED, JobPhase.CLEANED}:
            raise SecurityPolicyError("Job cannot be cleaned before result persistence")
        if record.state is not JobPhase.CLEANED:
            self._cleanup_job_containers(record)
            log_payload = bytearray()
            for suffix in ("stdout.log", "stderr.log"):
                path = self.logs / f"{record.attempt_id}.{suffix}"
                if path.exists():
                    data = path.read_bytes()[: 4 * 1024 * 1024]
                    log_payload.extend(data)
                    path.unlink(missing_ok=True)
            payload: dict[str, Any] = {}
            if log_payload:
                log_ref = self.artifacts.put_bytes(
                    bytes(log_payload), kind="worker_log"
                )
                payload["worker_log_digest"] = log_ref.digest
            request = self.requests / f"{record.attempt_id}.json"
            request.unlink(missing_ok=True)
            record = self.jobs.transition(
                job_id,
                JobPhase.CLEANED,
                payload=payload,
            )
        return record

    def reconcile(self) -> list[ReconciliationAction]:
        actions: list[ReconciliationAction] = []
        containers = self.engine.list_managed()
        by_identity = {
            (handle.job_id, handle.attempt_id): handle for handle in containers
        }
        records = self.jobs.list_recoverable()
        mutation_records = self.jobs.list_recoverable_mutations()
        known = {(record.job_id, record.attempt_id) for record in records}
        known.update((record.job_id, record.attempt_id) for record in mutation_records)
        for handle in containers:
            if (handle.job_id, handle.attempt_id) not in known:
                self.engine.stop(handle, timeout_seconds=5.0)
                self.engine.remove(handle, force=True)
                actions.append(
                    ReconciliationAction(
                        job_id=handle.job_id,
                        action="orphan_removed",
                        detail=handle.name,
                    )
                )
        for mutation in mutation_records:
            identity = (mutation.job_id, mutation.attempt_id)
            handle = by_identity.get(identity)
            if handle is not None:
                self.engine.stop(handle, timeout_seconds=5.0)
                self.engine.remove(handle, force=True)
            self.jobs.fail_mutation(
                mutation.attempt_id,
                failure_class=FailureClass.WORKER_LOST,
            )
            self.jobs.mark_mutation_cleaned(mutation.attempt_id)
            actions.append(
                ReconciliationAction(
                    job_id=mutation.job_id,
                    action="mutation_marked_lost",
                    detail=mutation.attempt_id,
                )
            )
        for record in records:
            identity = (record.job_id, record.attempt_id)
            if record.state in {JobPhase.RESULT_VALIDATED, JobPhase.FAILED}:
                actions.append(
                    ReconciliationAction(
                        record.job_id, "result_ready", record.state.value
                    )
                )
                continue
            if record.state is JobPhase.PERSISTED:
                self.acknowledge_persisted(record.job_id)
                actions.append(ReconciliationAction(record.job_id, "cleaned"))
                continue
            recovered = self._recover_staged_result(record)
            if recovered is not None:
                actions.append(ReconciliationAction(record.job_id, "result_recovered"))
                continue
            request_path = self.requests / f"{record.attempt_id}.json"
            if record.state is JobPhase.PREPARED:
                if not request_path.is_file():
                    self._mark_failed(
                        record,
                        FailureClass.WORKER_LOST,
                        "Prepared launch intent had no durable worker request",
                    )
                    actions.append(ReconciliationAction(record.job_id, "marked_lost"))
                    continue
                self.jobs.transition(record.job_id, JobPhase.QUEUED)
                record = self.jobs.transition(record.job_id, JobPhase.STARTING)
            elif record.state is JobPhase.QUEUED:
                if not request_path.is_file():
                    self._mark_failed(
                        record,
                        FailureClass.WORKER_LOST,
                        "Queued launch intent had no durable worker request",
                    )
                    actions.append(ReconciliationAction(record.job_id, "marked_lost"))
                    continue
                record = self.jobs.transition(record.job_id, JobPhase.STARTING)
            if self._pid_is_worker(record):
                actions.append(ReconciliationAction(record.job_id, "reattached"))
                continue
            if record.state is JobPhase.STARTING and request_path.is_file():
                process = self._launch_worker(request_path, record.attempt_id)
                self.jobs.record_worker_launch(
                    record.job_id,
                    worker_pid=process.pid,
                    backend_id=f"worker:{process.pid}",
                )
                self._workers[record.job_id] = process
                actions.append(ReconciliationAction(record.job_id, "relaunched"))
                continue
            if identity in by_identity or record.state in {
                JobPhase.STARTING,
                JobPhase.RUNNING,
                JobPhase.COLLECTING,
            }:
                self._mark_failed(
                    record,
                    FailureClass.WORKER_LOST,
                    "Worker was lost during coordinator reconciliation",
                )
                actions.append(ReconciliationAction(record.job_id, "marked_lost"))
            else:
                self._mark_failed(
                    record,
                    FailureClass.WORKER_LOST,
                    "Recoverable job had no worker or durable launch request",
                )
                actions.append(ReconciliationAction(record.job_id, "marked_lost"))
        return actions
