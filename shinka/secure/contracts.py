"""Versioned public, evaluator, job, and result contracts."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes, digest_json, validate_digest
from .errors import ConfigurationError, FailureClass, ResultValidationError

CONTRACT_SCHEMA_VERSION = "shinka-secure-contract-v1"
RESULT_SCHEMA_VERSION = "shinka-secure-result-v1"
MAX_PUBLIC_FEEDBACK_CHARS = 2_000
_PINNED_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def _safe_relative_path(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field_name} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigurationError(f"{field_name} must be a normalized relative path")
    return path.as_posix()


def _safe_path_list(values: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _safe_relative_path(value, field_name=field_name) for value in values
        )
    )


def validate_pinned_image(image: str) -> str:
    if not isinstance(image, str) or not _PINNED_IMAGE.fullmatch(image):
        raise ConfigurationError(
            "Container images must be pinned as repository@sha256:<64 lowercase hex>"
        )
    return image


class NetworkMode(str, Enum):
    DISABLED = "disabled"
    PROVIDER_ONLY = "provider_only"


class JobStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ArtifactRef:
    digest: str
    size: int
    kind: str
    media_type: str = "application/vnd.shinka.archive.v1+tar"

    def __post_init__(self) -> None:
        validate_digest(self.digest)
        if self.size < 0:
            raise ConfigurationError("Artifact size cannot be negative")
        if not self.kind or not self.kind.replace("_", "").isalnum():
            raise ConfigurationError("Artifact kind must be an identifier")


@dataclass(frozen=True)
class PublicTaskContract:
    task_id: str
    objective: str
    candidate_protocol: str
    mutable_paths: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    agent_omitted_paths: tuple[str, ...] = ()
    public_metric_allowlist: tuple[str, ...] = ()
    public_feedback_enabled: bool = False
    public_feedback_max_chars: int = MAX_PUBLIC_FEEDBACK_CHARS
    schema_version: str = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ConfigurationError("Unsupported public task contract version")
        if not self.task_id.strip() or not self.objective.strip():
            raise ConfigurationError("Task id and objective are required")
        if not self.candidate_protocol.strip():
            raise ConfigurationError("A candidate process/service protocol is required")
        object.__setattr__(
            self,
            "mutable_paths",
            _safe_path_list(self.mutable_paths, field_name="mutable_paths"),
        )
        object.__setattr__(
            self,
            "required_paths",
            _safe_path_list(self.required_paths, field_name="required_paths"),
        )
        object.__setattr__(
            self,
            "agent_omitted_paths",
            _safe_path_list(self.agent_omitted_paths, field_name="agent_omitted_paths"),
        )
        if (
            self.public_feedback_max_chars < 0
            or self.public_feedback_max_chars > 16_000
        ):
            raise ConfigurationError(
                "public_feedback_max_chars must be between 0 and 16000"
            )
        if any(
            not name or not isinstance(name, str)
            for name in self.public_metric_allowlist
        ):
            raise ConfigurationError("Public metric names must be non-empty strings")

    @property
    def digest(self) -> str:
        return digest_json(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicTaskContract":
        data = dict(value)
        for name in (
            "mutable_paths",
            "required_paths",
            "agent_omitted_paths",
            "public_metric_allowlist",
        ):
            if name in data:
                data[name] = tuple(data[name])
        return cls(**data)


@dataclass(frozen=True)
class EvaluatorContract:
    entrypoint: str
    callable_name: str = "evaluate"
    private_data_manifest: str | None = None
    public_metric_allowlist: tuple[str, ...] = ()
    public_feedback_enabled: bool = False
    public_feedback_max_chars: int = MAX_PUBLIC_FEEDBACK_CHARS
    schema_version: str = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entrypoint",
            _safe_relative_path(self.entrypoint, field_name="evaluator.entrypoint"),
        )
        if self.private_data_manifest is not None:
            object.__setattr__(
                self,
                "private_data_manifest",
                _safe_relative_path(
                    self.private_data_manifest,
                    field_name="evaluator.private_data_manifest",
                ),
            )
        if not self.callable_name.isidentifier():
            raise ConfigurationError(
                "Evaluator callable_name must be a Python identifier"
            )
        if (
            self.public_feedback_max_chars < 0
            or self.public_feedback_max_chars > 16_000
        ):
            raise ConfigurationError(
                "Evaluator public_feedback_max_chars must be between 0 and 16000"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluatorContract":
        data = dict(value)
        if "public_metric_allowlist" in data:
            data["public_metric_allowlist"] = tuple(data["public_metric_allowlist"])
        return cls(**data)


@dataclass(frozen=True)
class ResourceLimits:
    cpus: float
    memory_bytes: int
    pids: int
    open_files: int = 1024
    output_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if not math.isfinite(self.cpus) or self.cpus <= 0:
            raise ConfigurationError("cpus must be finite and > 0")
        if self.memory_bytes < 64 * 1024 * 1024:
            raise ConfigurationError("memory_bytes must be at least 64 MiB")
        if self.pids <= 0 or self.open_files <= 0 or self.output_bytes <= 0:
            raise ConfigurationError("PID, open-file, and output limits must be > 0")


@dataclass(frozen=True)
class TimeoutPolicy:
    queue_seconds: float = 300.0
    startup_seconds: float = 1800.0
    heartbeat_seconds: float = 600.0
    request_seconds: float | None = None
    wall_seconds: float | None = 28_800.0
    collection_seconds: float = 300.0
    cleanup_seconds: float = 120.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ConfigurationError(f"{name} timeout must be finite and > 0")


@dataclass(frozen=True)
class EnvironmentContract:
    mutation_image: str
    build_image: str
    runtime_image: str
    backend: str = "local_docker"
    network: NetworkMode = NetworkMode.DISABLED
    limits: ResourceLimits = field(
        default_factory=lambda: ResourceLimits(
            cpus=2.0,
            memory_bytes=2 * 1024 * 1024 * 1024,
            pids=128,
        )
    )

    def __post_init__(self) -> None:
        if self.backend != "local_docker":
            raise ConfigurationError("Only the local_docker backend is supported")
        validate_pinned_image(self.mutation_image)
        validate_pinned_image(self.build_image)
        validate_pinned_image(self.runtime_image)

    @property
    def digest(self) -> str:
        return digest_json(asdict(self))


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    run_id: str
    individual_id: str
    attempt_id: str
    candidate_digest: str
    runtime_artifact_digest: str
    evaluator_digest: str
    dependency_digest: str
    environment_digest: str
    task_contract_digest: str
    evaluator_contract: EvaluatorContract
    runtime_image: str
    candidate_command: tuple[str, ...]
    build_command: tuple[str, ...] = ()
    timeouts: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    resources: ResourceLimits = field(
        default_factory=lambda: ResourceLimits(
            cpus=2.0,
            memory_bytes=2 * 1024 * 1024 * 1024,
            pids=128,
        )
    )
    schema_version: str = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value in (
            self.candidate_digest,
            self.runtime_artifact_digest,
            self.evaluator_digest,
            self.dependency_digest,
            self.environment_digest,
            self.task_contract_digest,
        ):
            validate_digest(value)
        validate_pinned_image(self.runtime_image)
        if not all((self.job_id, self.run_id, self.individual_id, self.attempt_id)):
            raise ConfigurationError(
                "Job, run, individual, and attempt ids are required"
            )
        if not self.candidate_command or any(
            not item for item in self.candidate_command
        ):
            raise ConfigurationError(
                "candidate_command must be a non-empty argv sequence"
            )

    @property
    def digest(self) -> str:
        return digest_json(asdict(self))

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "JobSpec":
        data = dict(value)
        data["evaluator_contract"] = EvaluatorContract.from_dict(
            data["evaluator_contract"]
        )
        data["timeouts"] = TimeoutPolicy(**data.get("timeouts", {}))
        data["resources"] = ResourceLimits(**data["resources"])
        data["candidate_command"] = tuple(data["candidate_command"])
        data["build_command"] = tuple(data.get("build_command", ()))
        return cls(**data)


def _finite_json(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResultValidationError(f"Non-finite result value at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _finite_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ResultValidationError(f"Non-string result key at {path}")
            _finite_json(item, path=f"{path}.{key}")
        return
    raise ResultValidationError(f"Unsupported result value at {path}")


@dataclass(frozen=True)
class ResultManifest:
    job_id: str
    run_id: str
    individual_id: str
    attempt_id: str
    candidate_digest: str
    runtime_artifact_digest: str
    evaluator_digest: str
    dependency_digest: str
    environment_digest: str
    job_spec_digest: str
    status: JobStatus
    correct: bool
    combined_score: float | None
    public_metrics: Mapping[str, Any] = field(default_factory=dict)
    public_feedback: str | None = None
    private_metrics: Mapping[str, Any] = field(default_factory=dict)
    operator_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    phase_timings: Mapping[str, float] = field(default_factory=dict)
    resources: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    failure_class: FailureClass | None = None
    schema_version: str = RESULT_SCHEMA_VERSION

    def validate_against(
        self,
        job: JobSpec,
        *,
        public_metric_allowlist: Sequence[str],
        public_feedback_enabled: bool,
        public_feedback_max_chars: int = MAX_PUBLIC_FEEDBACK_CHARS,
    ) -> None:
        if self.schema_version != RESULT_SCHEMA_VERSION:
            raise ResultValidationError("Unsupported result schema version")
        exact = {
            "job_id": job.job_id,
            "run_id": job.run_id,
            "individual_id": job.individual_id,
            "attempt_id": job.attempt_id,
            "candidate_digest": job.candidate_digest,
            "runtime_artifact_digest": job.runtime_artifact_digest,
            "evaluator_digest": job.evaluator_digest,
            "dependency_digest": job.dependency_digest,
            "environment_digest": job.environment_digest,
            "job_spec_digest": job.digest,
        }
        for name, expected in exact.items():
            if getattr(self, name) != expected:
                raise ResultValidationError(f"Result {name} does not match the job")

        _finite_json(dict(self.public_metrics), path="$.public_metrics")
        _finite_json(dict(self.private_metrics), path="$.private_metrics")
        _finite_json(dict(self.operator_diagnostics), path="$.operator_diagnostics")
        _finite_json(dict(self.phase_timings), path="$.phase_timings")
        _finite_json(dict(self.resources), path="$.resources")

        unknown_public = sorted(set(self.public_metrics) - set(public_metric_allowlist))
        if unknown_public:
            raise ResultValidationError(
                f"Evaluator emitted non-allowlisted public metrics: {unknown_public}"
            )
        if self.public_feedback:
            if not public_feedback_enabled:
                raise ResultValidationError("Public text feedback is disabled")
            if len(self.public_feedback) > public_feedback_max_chars:
                raise ResultValidationError(
                    "Public text feedback exceeds its size limit"
                )

        if self.status is JobStatus.SUCCEEDED:
            if self.failure_class is not None:
                raise ResultValidationError("Successful result has a failure class")
            if self.combined_score is None or not math.isfinite(self.combined_score):
                raise ResultValidationError("Successful result needs one finite score")
        else:
            if self.failure_class is None:
                raise ResultValidationError("Failed result needs a typed failure class")
            if self.correct or self.combined_score is not None:
                raise ResultValidationError("Failed result cannot be correct or scored")
            if self.public_metrics or self.public_feedback:
                raise ResultValidationError(
                    "Failed result cannot expose public feedback"
                )

    @property
    def digest(self) -> str:
        return digest_json(asdict(self))

    def public_view(self) -> dict[str, Any]:
        """Return the only fields eligible for future mutation prompts."""

        if self.status is not JobStatus.SUCCEEDED:
            return {"status": self.status.value}
        view: dict[str, Any] = {
            "status": self.status.value,
            "combined_score": self.combined_score,
            "public_metrics": dict(self.public_metrics),
        }
        if self.public_feedback:
            view["public_feedback"] = self.public_feedback
        return view

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResultManifest":
        data = dict(value)
        data["status"] = JobStatus(data["status"])
        failure = data.get("failure_class")
        data["failure_class"] = FailureClass(failure) if failure else None
        data["artifacts"] = tuple(
            ArtifactRef(**item) if not isinstance(item, ArtifactRef) else item
            for item in data.get("artifacts", ())
        )
        return cls(**data)
