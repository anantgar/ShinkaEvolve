"""Trusted source and measurement policy. No candidate code is imported here."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprints(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in {".git", ".shinka"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed: {relative}")
        if path.is_file():
            result[relative.as_posix()] = sha256(path)
        elif not path.is_dir():
            raise ValueError(f"Special file is not allowed: {relative}")
    return result


def validate_source(root: Path, expected: dict[str, str], mutable: list[str]) -> None:
    actual = fingerprints(root)
    # No extra files, removed files, executable hooks, generated nets or build scripts.
    if actual.keys() != expected.keys():
        raise ValueError("Candidate file set differs from the pinned seed")
    for name, digest in expected.items():
        allowed = any(name == p or name.startswith(p + "/") for p in mutable)
        if not allowed and actual[name] != digest:
            raise ValueError(f"Candidate changed immutable file: {name}")


def load_manifest(path: Path) -> dict:
    value = json.loads(path.read_text())
    if value.get("schema") not in {"stockfish-inference-v2", "stockfish-inference-v3"}:
        raise ValueError(
            "Prepare a new stockfish-inference-v2 or v3 campaign and rebuild the image"
        )
    benchmark = value["benchmark"]
    weights = benchmark["workload_weights"]
    if (
        set(weights) != {"incremental", "refresh", "hot"}
        or not all(math.isfinite(w) and w > 0 for w in weights.values())
        or not math.isclose(sum(weights.values()), 1.0)
    ):
        raise ValueError("Workload weights must be positive and sum to one")
    for key in ("pairs", "passes", "warmups", "branches", "maximum_passes"):
        if type(benchmark[key]) is not int or benchmark[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if benchmark["pairs"] < 6 or benchmark["pairs"] % 2:
        raise ValueError("An even number of at least six AB/BA pairs is required")
    if benchmark["branches"] > 32 or benchmark["maximum_passes"] > 65536:
        raise ValueError("Benchmark exceeds replay protocol limits")
    if "passes_by_workload" in benchmark:
        counts = benchmark["passes_by_workload"]
        if set(counts) != set(weights) or any(
            type(n) is not int or not 1 <= n <= benchmark["maximum_passes"]
            for n in counts.values()
        ):
            raise ValueError("Invalid frozen pass counts")
    for key in ("max_memory_ratio", "max_binary_ratio", "max_structure_ratio"):
        if not math.isfinite(benchmark[key]) or benchmark[key] < 1:
            raise ValueError(f"Invalid {key}")
    if benchmark["passes"] > benchmark["maximum_passes"]:
        raise ValueError("Initial passes exceed maximum passes")
    for key in (
        "minimum_sample_seconds",
        "max_log_standard_error",
        "max_workload_log_standard_error",
        "max_aa_log_bias",
    ):
        if not math.isfinite(benchmark[key]) or benchmark[key] <= 0:
            raise ValueError(f"Invalid {key}")
    if value["schema"] == "stockfish-inference-v3":
        search = value["search_benchmark"]
        isolation = search.get("process_isolation", "paired")
        if isolation not in {"paired", "sequential_abba_v1"}:
            raise ValueError("Invalid search process isolation")
        if isolation == "sequential_abba_v1" and search["rounds_per_block"] != 2:
            raise ValueError("Sequential ABBA requires two timing pairs per block")
        for name, low, high in (
            ("process_blocks", 6, 256),
            ("rounds_per_block", 2, 32),
            ("warmups", 1, 8),
            ("depth", 1, 24),
            ("hash_mb", 1, 512),
            ("passes", 1, 16),
            ("maximum_passes", 1, 16),
        ):
            if type(search[name]) is not int or not low <= search[name] <= high:
                raise ValueError(f"Invalid search {name}")
        if search["process_blocks"] % 2 or search["rounds_per_block"] % 2:
            raise ValueError("Search startup and timing orders must be balanced")
        if search["passes"] > search["maximum_passes"]:
            raise ValueError("Search passes exceed the declared maximum")
        for name in (
            "minimum_sample_seconds",
            "max_log_standard_error",
            "max_aa_log_bias",
        ):
            if (
                type(search[name]) not in {int, float}
                or not math.isfinite(search[name])
                or search[name] <= 0
            ):
                raise ValueError(f"Invalid search {name}")
        if type(search["seed"]) is not int:
            raise ValueError("Invalid search ordering seed")
    return value
