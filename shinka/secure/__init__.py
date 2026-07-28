"""Secure repository mutation and evaluator isolation primitives."""

from .canonical import canonical_json_bytes, digest_json, sha256_bytes, validate_digest
from .contracts import (
    ArtifactRef,
    EnvironmentContract,
    EvaluatorContract,
    JobSpec,
    JobStatus,
    NetworkMode,
    PublicTaskContract,
    ResourceLimits,
    ResultManifest,
    TimeoutPolicy,
    validate_pinned_image,
)
from .errors import (
    ArtifactIntegrityError,
    ConfigurationError,
    FailureClass,
    ResultValidationError,
    SecureExecutionError,
    SecurityPolicyError,
    SnapshotError,
)

__all__ = [
    "ArtifactIntegrityError",
    "ArtifactRef",
    "ConfigurationError",
    "EnvironmentContract",
    "EvaluatorContract",
    "FailureClass",
    "JobSpec",
    "JobStatus",
    "NetworkMode",
    "PublicTaskContract",
    "ResourceLimits",
    "ResultManifest",
    "ResultValidationError",
    "SecureExecutionError",
    "SecurityPolicyError",
    "SnapshotError",
    "TimeoutPolicy",
    "canonical_json_bytes",
    "digest_json",
    "sha256_bytes",
    "validate_digest",
    "validate_pinned_image",
]
