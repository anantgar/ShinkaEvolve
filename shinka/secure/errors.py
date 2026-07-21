"""Typed failures for secure repository mutation and evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class FailureClass(str, Enum):
    """Stable failure classes persisted in job and result records."""

    CONFIGURATION = "configuration"
    CONTAINER_PREFLIGHT = "container_preflight"
    SECURITY_POLICY = "security_policy"
    SNAPSHOT_INVALID = "snapshot_invalid"
    ARTIFACT_INTEGRITY = "artifact_integrity"
    DEPENDENCY_PREPARATION = "dependency_preparation"
    MUTATION_FAILED = "mutation_failed"
    BUILD_FAILED = "build_failed"
    PROTOCOL_FAILED = "protocol_failed"
    EVALUATOR_FAILED = "evaluator_failed"
    RESULT_INVALID = "result_invalid"
    QUEUE_TIMEOUT = "queue_timeout"
    STARTUP_TIMEOUT = "startup_timeout"
    REQUEST_TIMEOUT = "request_timeout"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    WALL_TIMEOUT = "wall_timeout"
    COLLECTION_TIMEOUT = "collection_timeout"
    CLEANUP_FAILED = "cleanup_failed"
    WORKER_LOST = "worker_lost"
    CANCELLED = "cancelled"


class SecureExecutionError(RuntimeError):
    """Base exception whose public and private diagnostics stay separate."""

    def __init__(
        self,
        failure_class: FailureClass,
        public_message: str,
        *,
        private_diagnostic: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(public_message)
        self.failure_class = failure_class
        self.public_message = public_message
        self.private_diagnostic = private_diagnostic
        self.details = dict(details or {})


class ConfigurationError(SecureExecutionError):
    def __init__(self, message: str) -> None:
        super().__init__(FailureClass.CONFIGURATION, message)


class SecurityPolicyError(SecureExecutionError):
    def __init__(self, message: str, *, private_diagnostic: str | None = None) -> None:
        super().__init__(
            FailureClass.SECURITY_POLICY,
            message,
            private_diagnostic=private_diagnostic,
        )


class SnapshotError(SecureExecutionError):
    def __init__(self, message: str, *, private_diagnostic: str | None = None) -> None:
        super().__init__(
            FailureClass.SNAPSHOT_INVALID,
            message,
            private_diagnostic=private_diagnostic,
        )


class ArtifactIntegrityError(SecureExecutionError):
    def __init__(self, message: str) -> None:
        super().__init__(FailureClass.ARTIFACT_INTEGRITY, message)


class ResultValidationError(SecureExecutionError):
    def __init__(self, message: str, *, private_diagnostic: str | None = None) -> None:
        super().__init__(
            FailureClass.RESULT_INVALID,
            message,
            private_diagnostic=private_diagnostic,
        )
