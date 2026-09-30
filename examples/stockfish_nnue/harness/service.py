"""Immutable protocol adapter. Candidate C++ is a separate, networkless process."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys

MAX_FRAME = 32 * 1024 * 1024


def read_exact(stream, size):
    parts = bytearray()
    while len(parts) < size:
        chunk = stream.read(size - len(parts))
        if not chunk:
            raise EOFError
        parts.extend(chunk)
    return bytes(parts)


def receive(stream):
    size = struct.unpack(">I", read_exact(stream, 4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("Invalid frame size")
    return json.loads(read_exact(stream, size))


def send(value):
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_FRAME:
        raise ValueError("Response too large")
    sys.stdout.buffer.write(struct.pack(">I", len(payload)) + payload)
    sys.stdout.buffer.flush()


def line(value):
    value = str(value)
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Newlines are forbidden in protocol fields")
    return value


def main():
    process = subprocess.Popen(
        [sys.argv[1], sys.argv[2]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": "/tmp"},
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        metadata = json.loads(process.stdout.readline(MAX_FRAME))
        send({"type": "ready", "protocol": "shinka-candidate-v1"})
        while True:
            try:
                request = receive(sys.stdin.buffer)
            except EOFError:
                break
            payload = request["input"]
            operation = payload["op"]
            if operation == "info":
                output = {**metadata, "binary_bytes": os.stat(sys.argv[1]).st_size}
            else:
                if operation == "load":
                    traces = payload["traces"]
                    if not 0 < len(traces) <= 10000:
                        raise ValueError("Invalid corpus size")
                    data = [
                        f"LOAD {len(traces)} {int(payload['branches'])} {int(payload['salt'])}"
                    ]
                    for trace in traces:
                        data += [
                            "1" if trace.get("chess960", False) else "0",
                            line(trace["fen"]),
                            line(" ".join(trace["moves"])),
                        ]
                    process.stdin.write("\n".join(data) + "\n")
                elif operation == "run":
                    process.stdin.write(
                        f"RUN {line(payload['workload'])} {int(payload['passes'])} "
                        f"{int(payload['offset'])}\n"
                    )
                else:
                    raise ValueError("Unknown operation")
                process.stdin.flush()
                output = json.loads(process.stdout.readline(MAX_FRAME))
            send({"type": "response", "id": request["id"], "output": output})
    finally:
        process.kill()
        process.wait()


if __name__ == "__main__":
    main()
