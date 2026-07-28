"""Canonical JSON and SHA-256 helpers used for immutable identities."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

from .errors import ConfigurationError

DIGEST_PREFIX = "sha256:"


def _json_value(value: Any, *, path: str = "$") -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value), path=path)
    if isinstance(value, Enum):
        return _json_value(value.value, path=path)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError(f"Non-finite number at {path}")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(f"Non-string JSON key at {path}")
            normalized[key] = _json_value(item, path=f"{path}.{key}")
        return normalized
    raise ConfigurationError(
        f"Unsupported JSON value at {path}: {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a value deterministically and reject non-finite numbers."""

    normalized = _json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return f"{DIGEST_PREFIX}{hashlib.sha256(data).hexdigest()}"


def sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return f"{DIGEST_PREFIX}{digest.hexdigest()}", size


def validate_digest(digest: str) -> str:
    if not isinstance(digest, str) or not digest.startswith(DIGEST_PREFIX):
        raise ConfigurationError("Digest must use the sha256:<hex> form")
    value = digest[len(DIGEST_PREFIX) :]
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ConfigurationError("Digest must contain 64 lowercase SHA-256 hex digits")
    return digest


def digest_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))
