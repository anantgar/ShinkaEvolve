"""Immutable adapter for exact replay checks and bounded full-engine searches.

Only the host controller's request duration is fitness. UCI time/NPS fields are
never used. Search completion requires bestmove, not the asynchronous readyok.
"""

from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import re
import selectors
import struct
import subprocess
import sys
import time

try:
    from .service import MAX_FRAME, line, receive, send
except ImportError:
    from replay_service import MAX_FRAME, line, receive, send

MOVE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")


def parse_search_info(text: str) -> dict | None:
    words = text.split()
    if words[:2] == ["info", "string"] or not words or words[0] != "info":
        return None
    if not all(key in words for key in ("depth", "seldepth", "score", "nodes", "pv")):
        return None
    result = {}
    for key in ("depth", "seldepth", "nodes"):
        if words.count(key) != 1:
            raise ValueError("Duplicate UCI search field")
        result[key] = int(words[words.index(key) + 1])
        if not 0 <= result[key] < 2**63:
            raise ValueError("Invalid UCI search value")
    if "multipv" in words and words[words.index("multipv") + 1] != "1":
        raise ValueError("Only single-PV search is permitted")
    index = words.index("score")
    kind, value = words[index + 1 : index + 3]
    if kind not in {"cp", "mate"}:
        raise ValueError("Invalid UCI score kind")
    result["score_kind"] = kind
    result["score"] = int(value)
    result["bound"] = next(
        (word for word in words if word in {"lowerbound", "upperbound"}), ""
    )
    result["pv"] = words[words.index("pv") + 1 :]
    if not result["pv"] or any(not MOVE.fullmatch(move) for move in result["pv"]):
        raise ValueError("Invalid UCI principal variation")
    return result


def finish_search(info: dict | None, text: str) -> dict:
    words = text.split()
    if info is None or words[:1] != ["bestmove"] or len(words) not in {2, 4}:
        raise ValueError("Search ended without a complete result")
    if not MOVE.fullmatch(words[1]):
        raise ValueError("Search returned an invalid move")
    ponder = ""
    if len(words) == 4:
        if words[2] != "ponder" or not MOVE.fullmatch(words[3]):
            raise ValueError("Search returned an invalid ponder move")
        ponder = words[3]
    if words[1] != info["pv"][0]:
        raise ValueError("Best move disagrees with the principal variation")
    return {**info, "bestmove": words[1], "ponder": ponder}


class UciEngine:
    def __init__(
        self, binary: Path, network: Path, hash_mb: int, *, cpu: int | None = None
    ):
        self.process = subprocess.Popen(
            (["taskset", "-c", str(cpu)] if cpu is not None else []) + [str(binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C"},
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.pending = bytearray()
        self.lines = deque()
        self.output_bytes = 0
        try:
            self.command("uci")
            self.until("uciok")
            self.command(
                "setoption name Threads value 1",
                f"setoption name Hash value {hash_mb}",
                "setoption name MultiPV value 1",
                "setoption name Ponder value false",
                "setoption name UCI_LimitStrength value false",
                "setoption name UCI_ShowWDL value false",
                "setoption name SyzygyPath value",
                f"setoption name EvalFile value {line(network)}",
                "isready",
            )
            self.until("readyok")
        except BaseException:
            self.close()
            raise

    def command(self, *commands: str) -> None:
        self.process.stdin.write(("\n".join(commands) + "\n").encode())
        self.process.stdin.flush()

    def read_line(self, deadline: float) -> str:
        while not self.lines:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Full-engine search timed out")
            for key, _ in self.selector.select(min(remaining, 1.0)):
                chunk = key.fileobj.read(65536)
                if not chunk:
                    raise RuntimeError("Full-engine protocol stream ended")
                self.output_bytes += len(chunk)
                if self.output_bytes > MAX_FRAME:
                    raise ValueError("Full-engine output exceeded its bound")
                if key.data == "stderr":
                    raise RuntimeError("Full engine emitted unexpected diagnostics")
                self.pending.extend(chunk)
                while b"\n" in self.pending:
                    raw, _, rest = self.pending.partition(b"\n")
                    self.pending = bytearray(rest)
                    decoded = raw.decode("utf-8").rstrip("\r")
                    if "CRITICAL ERROR" in decoded:
                        raise ValueError("Stockfish rejected a position or command")
                    self.lines.append(decoded)
        return self.lines.popleft()

    def until(self, terminal: str) -> None:
        deadline = time.monotonic() + 120
        while self.read_line(deadline) != terminal:
            pass

    def search(self, case: dict, depth: int) -> dict:
        self.output_bytes = 0
        self.command(
            "setoption name UCI_Chess960 value "
            + ("true" if case.get("chess960", False) else "false"),
            "ucinewgame",
            "isready",
        )
        self.until("readyok")
        position = "position fen " + line(case["fen"])
        if case["moves"]:
            position += " moves " + line(" ".join(case["moves"]))
        self.command(position, f"go depth {depth}")
        deadline = time.monotonic() + 120
        info = None
        while True:
            text = self.read_line(deadline)
            if text.startswith("bestmove "):
                return finish_search(info, text)
            parsed = parse_search_info(text)
            if parsed is not None:
                info = parsed

    def close(self) -> None:
        try:
            self.command("quit")
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self.selector.close()
            for stream in (
                self.process.stdin,
                self.process.stdout,
                self.process.stderr,
            ):
                stream.close()


class Replay:
    def __init__(self, binary: Path, network: Path):
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(binary.with_name("replay_service.py")),
                str(binary),
                str(network),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C"},
        )
        ready = receive(self.process.stdout)
        if ready != {"type": "ready", "protocol": "shinka-candidate-v1"}:
            self.close()
            raise ValueError("Exact-check service did not become ready")
        self.index = 0

    def request(self, payload: dict) -> dict:
        self.index += 1
        message = json.dumps({"id": str(self.index), "input": payload}).encode()
        if len(message) > MAX_FRAME:
            raise ValueError("Exact-check request is too large")
        self.process.stdin.write(struct.pack(">I", len(message)) + message)
        self.process.stdin.flush()
        response = receive(self.process.stdout)
        if response.get("type") != "response" or response.get("id") != str(self.index):
            raise ValueError("Exact-check response identity mismatch")
        return response["output"]

    def close(self) -> None:
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stdout.close()


def main() -> None:
    binary, network = Path(sys.argv[1]), Path(sys.argv[2])
    engine_binary = binary.with_name("stockfish")
    replay = None
    engine = None
    cases = []
    depth = None
    send({"type": "ready", "protocol": "shinka-candidate-v1"})
    try:
        while True:
            try:
                request = receive(sys.stdin.buffer)
            except EOFError:
                break
            payload = request["input"]
            operation = payload["op"]
            if operation in {"info", "load", "run"}:
                if engine is not None:
                    engine.close()
                    engine = None
                if replay is None:
                    replay = Replay(binary, network)
                output = replay.request(payload)
                if operation == "info":
                    output["search_binary_bytes"] = engine_binary.stat().st_size
            elif operation == "search_load":
                cases = payload["cases"]
                depth, hash_mb = payload["depth"], payload["hash_mb"]
                if (
                    not isinstance(cases, list)
                    or not 1 <= len(cases) <= 256
                    or type(depth) is not int
                    or not 1 <= depth <= 24
                    or type(hash_mb) is not int
                    or not 1 <= hash_mb <= 512
                ):
                    raise ValueError("Invalid fixed-search settings")
                for case in cases:
                    line(case["fen"])
                    if (
                        not isinstance(case["moves"], list)
                        or len(case["moves"]) > 192
                        or any(not MOVE.fullmatch(move) for move in case["moves"])
                        or type(case.get("chess960", False)) is not bool
                    ):
                        raise ValueError("Invalid fixed-search case")
                if replay is not None:
                    replay.close()
                    replay = None
                if engine is not None:
                    engine.close()
                engine = UciEngine(engine_binary, network, hash_mb)
                output = {"loaded": len(cases)}
            elif operation == "search":
                passes, offset = payload["passes"], payload["offset"]
                if (
                    engine is None
                    or not cases
                    or type(passes) is not int
                    or not 1 <= passes <= 16
                    or type(offset) is not int
                    or not 0 <= offset < len(cases)
                ):
                    raise ValueError("Invalid fixed-search request")
                records = [
                    engine.search(cases[(index + offset) % len(cases)], depth)
                    for _ in range(passes)
                    for index in range(len(cases))
                ]
                canonical = json.dumps(
                    records, sort_keys=True, separators=(",", ":")
                ).encode()
                output = {
                    "calls": len(records),
                    "nodes": sum(record["nodes"] for record in records),
                    "checksum": hashlib.sha256(canonical).hexdigest(),
                    "records": records,
                }
            else:
                raise ValueError("Unknown operation")
            send({"type": "response", "id": request["id"], "output": output})
    finally:
        if replay is not None:
            replay.close()
        if engine is not None:
            engine.close()


if __name__ == "__main__":
    main()
