"""Operator-only fixed-search correctness and staged timing; never Shinka fitness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time
from types import SimpleNamespace

from .corpus import validate_traces
from .evaluate import InvalidCandidate
from .harness.search_service import UciEngine
from .policy import load_manifest, sha256
from .search_checkpoint import SearchCheckpoint
from .search_evaluate import measure_search
from .search_scoring import search_measurement_rejection, validate_search_aa


class LocalRunner:
    """Trusted operator binaries only; preserves the existing request protocol."""

    def __init__(self, binary: Path, network: Path, cpu: int | None):
        self.binary, self.network, self.cpu = binary, network, cpu
        self.engine = None
        self.stderr = b""
        self.temporary = tempfile.TemporaryDirectory(prefix="nnue-validation-")

    def __enter__(self):
        return self

    def request(self, payload: dict):
        if payload["op"] == "search_load":
            executable = Path(self.temporary.name) / "stockfish"
            shutil.copyfile(self.binary, executable)
            executable.chmod(0o755)
            self.cases, self.depth = payload["cases"], payload["depth"]
            self.engine = UciEngine(
                executable, self.network, payload["hash_mb"], cpu=self.cpu
            )
            return SimpleNamespace(output={"loaded": len(self.cases)})
        start = time.perf_counter()
        records = [
            self.engine.search(
                self.cases[(index + payload["offset"]) % len(self.cases)], self.depth
            )
            for _ in range(payload["passes"])
            for index in range(len(self.cases))
        ]
        elapsed = time.perf_counter() - start
        output = {
            "calls": len(records),
            "nodes": sum(r["nodes"] for r in records),
            "records": records,
            "checksum": hashlib.sha256(
                json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        return SimpleNamespace(output=output, elapsed_seconds=elapsed)

    def peak_memory_bytes(self):
        # This driver does not claim the secure evaluator's resource validation.
        return 0

    def __exit__(self, *_):
        try:
            if self.engine is not None:
                self.engine.close()
        finally:
            self.temporary.cleanup()


def _validate(args) -> dict:
    corpus = json.loads(args.corpus.read_text())
    cases = corpus["search_cases"]
    validate_traces(cases)
    if len(cases) > 256:
        raise ValueError("At most 256 search cases")
    role = corpus.get("provenance", {}).get("role")
    if args.stage in {"screening", "finalist"} and role != args.stage:
        raise ValueError("Use the matching reserved corpus role for this stage")
    settings = dict(
        load_manifest(Path(__file__).with_name("search_manifest.json"))[
            "search_benchmark"
        ]
    )
    settings.update(
        process_blocks=128 if args.stage == "finalist" else 24,
        depth=args.depth,
        hash_mb=args.hash_mb,
        passes=args.passes,
    )
    if args.stage == "correctness":
        # Reuse the same complete checker but explicitly make no timing claim.
        settings["process_blocks"] = 6
    else:
        if platform.system() != "Linux" or args.cpu is None:
            raise ValueError("Timing requires native Linux and an explicit --cpu")
        available = os.sched_getaffinity(0)
        if args.cpu not in available or len(available) < 2:
            raise ValueError(
                "Reserve one available engine CPU and at least one controller CPU"
            )
        os.sched_setaffinity(0, available - {args.cpu})
    candidate = args.baseline if args.control == "aa" else args.candidate
    args.output.mkdir(parents=True, exist_ok=False)
    identity = {
        "schema": "stockfish-candidate-validation-v1",
        "stage": args.stage,
        "control": args.control,
        "settings": settings,
        "cpu": args.cpu,
        "baseline_sha256": sha256(args.baseline),
        "candidate_sha256": sha256(candidate),
        "network_sha256": sha256(args.network),
        "corpus_sha256": sha256(args.corpus),
        "protocol_sha256": {
            name: sha256(Path(__file__).parent / name)
            for name in (
                "validate_candidate.py",
                "search_evaluate.py",
                "search_scoring.py",
                "search_checkpoint.py",
                "harness/search_service.py",
            )
        },
        "machine": platform.machine(),
        "system": platform.platform(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if platform.system() == "Linux"
        else None,
        "fitness_admitted": False,
        "resource_checks": "not provided by this operator driver",
        "scope": "Standalone search measurement; complete controls and release checks are still required",
    }
    (args.output / "plan.json").write_text(json.dumps(identity, indent=2) + "\n")

    def publish(payload):
        path = args.output / "checkpoint.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)

    checkpoint = SearchCheckpoint({}, publish)
    try:
        measurement = measure_search(
            lambda role, _: LocalRunner(
                args.baseline if role == "baseline" else candidate,
                args.network,
                args.cpu,
            ),
            cases=cases,
            settings=settings,
            heartbeat=lambda _: None,
            checkpoint=checkpoint,
        )
        rejection = None
        if args.stage != "correctness":
            rejection = search_measurement_rejection(measurement, settings)
            if rejection is None and args.control == "aa":
                try:
                    validate_search_aa(measurement, settings)
                except RuntimeError as exc:
                    rejection = str(exc)
        # A screening result is selection evidence only, never a promotion claim.
        public = {
            key: value
            for key, value in measurement.items()
            if key not in {"blocks", "combined_score"}
        }
        result = {
            **identity,
            "correct": True,
            "measurement_accepted": rejection is None,
            "rejection": rejection,
            "supports_speed_claim": False,
            "measurement": public if args.stage != "correctness" else None,
        }
    except Exception as exc:
        result = {
            **identity,
            "correct": False if isinstance(exc, InvalidCandidate) else None,
            "measurement_accepted": False,
            "supports_speed_claim": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    finally:
        checkpoint.flush()
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def validate(args) -> dict:
    # Timing excludes the engine CPU from this controller, only for this run.
    # Restore the caller's mask even on errors so sequential API calls can
    # reserve the same engine CPU again.
    affinity = os.sched_getaffinity(0) if platform.system() == "Linux" else None
    try:
        return _validate(args)
    finally:
        if affinity is not None:
            os.sched_setaffinity(0, affinity)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "network", "corpus", "output"):
        parser.add_argument(
            "--" + name, type=lambda p: Path(p).resolve(), required=True
        )
    parser.add_argument(
        "--stage", choices=("correctness", "screening", "finalist"), required=True
    )
    parser.add_argument("--control", choices=("aa", "candidate"), default="candidate")
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--depth", type=int, choices=range(1, 25), default=13)
    parser.add_argument("--hash-mb", type=int, choices=range(1, 513), default=16)
    parser.add_argument("--passes", type=int, choices=range(1, 17), default=1)
    result = validate(parser.parse_args())
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "stage",
                    "correct",
                    "measurement_accepted",
                    "fitness_admitted",
                )
            }
        )
    )
    raise SystemExit(0 if result["measurement_accepted"] else 1)


if __name__ == "__main__":
    main()
