"""Bounded length-prefixed JSON protocol for candidate services."""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO, Mapping

from .canonical import canonical_json_bytes
from .errors import FailureClass, SecureExecutionError

CANDIDATE_PROTOCOL_VERSION = "shinka-candidate-v1"
DEFAULT_MAX_FRAME_BYTES = 64 * 1024 * 1024


def encode_frame(
    value: Mapping[str, Any], *, max_bytes: int = DEFAULT_MAX_FRAME_BYTES
) -> bytes:
    payload = canonical_json_bytes(dict(value))
    if not payload or len(payload) > max_bytes:
        raise SecureExecutionError(
            FailureClass.PROTOCOL_FAILED,
            "Candidate protocol frame exceeds its limit",
        )
    return struct.pack(">I", len(payload)) + payload


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError("candidate protocol stream closed")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(
    stream: BinaryIO,
    *,
    max_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> Mapping[str, Any]:
    try:
        length = struct.unpack(">I", _read_exact(stream, 4))[0]
        if length <= 0 or length > max_bytes:
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate protocol frame length is invalid",
            )
        payload = _read_exact(stream, length)
        value = json.loads(payload)
    except SecureExecutionError:
        raise
    except (EOFError, json.JSONDecodeError, UnicodeDecodeError, struct.error) as exc:
        raise SecureExecutionError(
            FailureClass.PROTOCOL_FAILED,
            "Candidate emitted an invalid protocol frame",
            private_diagnostic=str(exc),
        ) from exc
    if not isinstance(value, dict):
        raise SecureExecutionError(
            FailureClass.PROTOCOL_FAILED,
            "Candidate protocol frame must be a JSON object",
        )
    return value


def write_frame(
    stream: BinaryIO,
    value: Mapping[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> int:
    frame = encode_frame(value, max_bytes=max_bytes)
    stream.write(frame)
    stream.flush()
    return len(frame)
