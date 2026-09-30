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
    if value.get("schema") != "stockfish-inference-v2":
        raise ValueError(
            "Prepare a new stockfish-inference-v2 campaign and rebuild the image"
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
    return value
