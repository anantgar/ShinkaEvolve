"""Minimal framed-stdio service for the secure candidate runtime."""

from __future__ import annotations

import json
import struct
import sys

import numpy as np

from src.packing import run_packing


MAX_FRAME_BYTES = 64 * 1024 * 1024


def _send(value: dict) -> None:
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise RuntimeError("response frame is too large")
    sys.stdout.buffer.write(struct.pack(">I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _read_frame() -> dict | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise RuntimeError("truncated request header")
    size = struct.unpack(">I", header)[0]
    if size == 0 or size > MAX_FRAME_BYTES:
        raise RuntimeError("invalid request frame size")
    payload = sys.stdin.buffer.read(size)
    if len(payload) != size:
        raise RuntimeError("truncated request payload")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("request must be an object")
    return value


def _serialise_result() -> dict:
    centers, radii, reported_sum = run_packing()
    centers = np.asarray(centers, dtype=float)
    radii = np.asarray(radii, dtype=float)
    return {
        "centers": centers.tolist(),
        "radii": radii.tolist(),
        "reported_sum": float(reported_sum),
    }


def main() -> None:
    _send({"type": "ready", "protocol": "shinka-candidate-v1"})
    while True:
        request = _read_frame()
        if request is None:
            return
        if request.get("type") != "request" or not isinstance(request.get("id"), str):
            raise RuntimeError("invalid request")
        try:
            _send({"type": "response", "id": request["id"], "output": _serialise_result()})
        except Exception:
            _send({"type": "error", "id": request["id"], "error": "candidate failed"})


if __name__ == "__main__":
    main()
