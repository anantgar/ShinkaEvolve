"""Trusted evaluator API; candidate repositories are reachable only through a runner."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, Mapping

from .artifacts import ContentAddressedStore
from .contracts import (
    ArtifactRef,
    JobSpec,
    JobStatus,
    PublicTaskContract,
    ResultManifest,
)
from .errors import FailureClass, ResultValidationError, SecurityPolicyError
from .jobs import EvaluationJobStore

PRIVATE_DATA_SCHEMA_VERSION = "shinka-private-data-v1"


@dataclass(frozen=True)
class PrivateInputs:
    """Named private inputs rooted in the immutable evaluator snapshot."""

    root: Path
    paths: Mapping[str, Path]

    @classmethod
    def load(cls, evaluator_root: Path, manifest_path: str | None) -> "PrivateInputs":
        if manifest_path is None:
            return cls(root=evaluator_root, paths={})
        manifest_file = (evaluator_root / manifest_path).resolve()
        try:
            manifest_file.relative_to(evaluator_root.resolve())
        except ValueError as exc:
            raise SecurityPolicyError(
                "Private-data manifest leaves evaluator snapshot"
            ) from exc
        if not manifest_file.is_file() or manifest_file.is_symlink():
            raise SecurityPolicyError("Private-data manifest is missing or unsafe")
        if manifest_file.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            loaded = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
        else:
            loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != PRIVATE_DATA_SCHEMA_VERSION
        ):
            raise SecurityPolicyError("Private-data manifest schema is invalid")
        raw_inputs = loaded.get("inputs", {})
        if not isinstance(raw_inputs, dict):
            raise SecurityPolicyError("Private-data manifest inputs must be an object")
        paths: dict[str, Path] = {}
        for name, relative in raw_inputs.items():
            if not isinstance(name, str) or not name or not isinstance(relative, str):
                raise SecurityPolicyError("Private-data manifest entry is invalid")
            pure = PurePosixPath(relative.replace("\\", "/"))
            if pure.is_absolute() or any(
                part in {"", ".", ".."} for part in pure.parts
            ):
                raise SecurityPolicyError(
                    "Private-data path must be normalized and relative"
                )
            path = (evaluator_root / pure.as_posix()).resolve()
            try:
                path.relative_to(evaluator_root.resolve())
            except ValueError as exc:
                raise SecurityPolicyError(
                    "Private-data path leaves evaluator snapshot"
                ) from exc
            if not path.exists():
                raise SecurityPolicyError(f"Declared private input is missing: {name}")
            paths[name] = path
        return cls(root=evaluator_root, paths=paths)

    def path(self, name: str) -> Path:
        try:
            return self.paths[name]
        except KeyError as exc:
            raise KeyError(f"Private input is not declared: {name}") from exc

    def open(self, name: str, mode: str = "rb") -> BinaryIO:
        if mode not in {"rb", "r"}:
            raise SecurityPolicyError("Private inputs are read-only")
        return self.path(name).open(mode)


class EvaluatorJobContext:
    """Trusted job identity, heartbeat, and optional checkpoint capability."""

    def __init__(
        self,
        spec: JobSpec,
        jobs: EvaluationJobStore,
        artifacts: ContentAddressedStore,
        *,
        max_checkpoint_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.spec = spec
        self._jobs = jobs
        self._artifacts = artifacts
        self._max_checkpoint_bytes = max_checkpoint_bytes

    @property
    def job_id(self) -> str:
        return self.spec.job_id

    @property
    def attempt_id(self) -> str:
        return self.spec.attempt_id

    def heartbeat(self, phase: str | None = None) -> None:
        self._jobs.heartbeat(self.spec.job_id, phase=phase)

    def checkpoint(self, data: bytes) -> ArtifactRef:
        if len(data) > self._max_checkpoint_bytes:
            raise SecurityPolicyError("Evaluator checkpoint exceeds its size limit")
        reference = self._artifacts.put_bytes(data, kind="evaluator_checkpoint")
        self._jobs.checkpoint(self.spec.job_id, reference.digest)
        return reference


class ResultWriter:
    """Construct the authoritative result while enforcing public namespaces."""

    def __init__(
        self,
        *,
        spec: JobSpec,
        task_contract: PublicTaskContract,
        artifacts: ContentAddressedStore,
        max_artifact_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.spec = spec
        self.task_contract = task_contract
        self.artifacts = artifacts
        self.max_artifact_bytes = max_artifact_bytes
        evaluator_allowlist = set(spec.evaluator_contract.public_metric_allowlist)
        task_allowlist = set(task_contract.public_metric_allowlist)
        if evaluator_allowlist != task_allowlist:
            raise ResultValidationError(
                "Evaluator and public task metric allowlists must match exactly"
            )
        if (
            spec.evaluator_contract.public_feedback_enabled
            != task_contract.public_feedback_enabled
        ):
            raise ResultValidationError(
                "Evaluator and public task feedback policies must match exactly"
            )
        if (
            spec.evaluator_contract.public_feedback_max_chars
            != task_contract.public_feedback_max_chars
        ):
            raise ResultValidationError(
                "Evaluator and public task feedback limits must match exactly"
            )
        self._manifest: ResultManifest | None = None
        self._artifacts: list[ArtifactRef] = []
        self._timings: dict[str, float] = {}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not name or name in self._timings:
            raise ResultValidationError("Evaluator phase name is empty or duplicated")
        started = time.monotonic()
        try:
            yield
        finally:
            self._timings[name] = time.monotonic() - started

    def add_private_artifact(
        self, data: bytes, *, kind: str = "evaluator_output"
    ) -> ArtifactRef:
        if len(data) > self.max_artifact_bytes:
            raise ResultValidationError("Evaluator artifact exceeds its size limit")
        reference = self.artifacts.put_bytes(data, kind=kind)
        self._artifacts.append(reference)
        return reference

    def succeed(
        self,
        *,
        correct: bool,
        combined_score: float,
        public_metrics: Mapping[str, Any] | None = None,
        private_metrics: Mapping[str, Any] | None = None,
        public_feedback: str | None = None,
        operator_diagnostics: Mapping[str, Any] | None = None,
        phase_timings: Mapping[str, float] | None = None,
        resources: Mapping[str, Any] | None = None,
    ) -> ResultManifest:
        if self._manifest is not None:
            raise ResultValidationError("Evaluator attempted to finalize twice")
        timings = {**self._timings, **dict(phase_timings or {})}
        manifest = ResultManifest(
            job_id=self.spec.job_id,
            run_id=self.spec.run_id,
            individual_id=self.spec.individual_id,
            attempt_id=self.spec.attempt_id,
            candidate_digest=self.spec.candidate_digest,
            runtime_artifact_digest=self.spec.runtime_artifact_digest,
            evaluator_digest=self.spec.evaluator_digest,
            dependency_digest=self.spec.dependency_digest,
            environment_digest=self.spec.environment_digest,
            job_spec_digest=self.spec.digest,
            status=JobStatus.SUCCEEDED,
            correct=bool(correct),
            combined_score=combined_score,
            public_metrics=dict(public_metrics or {}),
            public_feedback=public_feedback,
            private_metrics=dict(private_metrics or {}),
            operator_diagnostics=dict(operator_diagnostics or {}),
            phase_timings=timings,
            resources=dict(resources or {}),
            artifacts=tuple(self._artifacts),
        )
        manifest.validate_against(
            self.spec,
            public_metric_allowlist=self.task_contract.public_metric_allowlist,
            public_feedback_enabled=self.task_contract.public_feedback_enabled,
            public_feedback_max_chars=self.task_contract.public_feedback_max_chars,
        )
        self._manifest = manifest
        return manifest

    @property
    def manifest(self) -> ResultManifest:
        if self._manifest is None:
            raise ResultValidationError(
                "Evaluator returned without finalizing a result"
            )
        return self._manifest


def failure_manifest(spec: JobSpec, failure_class: FailureClass) -> ResultManifest:
    return ResultManifest(
        job_id=spec.job_id,
        run_id=spec.run_id,
        individual_id=spec.individual_id,
        attempt_id=spec.attempt_id,
        candidate_digest=spec.candidate_digest,
        runtime_artifact_digest=spec.runtime_artifact_digest,
        evaluator_digest=spec.evaluator_digest,
        dependency_digest=spec.dependency_digest,
        environment_digest=spec.environment_digest,
        job_spec_digest=spec.digest,
        status=JobStatus.FAILED,
        correct=False,
        combined_score=None,
        failure_class=failure_class,
    )
