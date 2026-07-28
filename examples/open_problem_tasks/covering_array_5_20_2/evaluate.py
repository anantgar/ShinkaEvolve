"""Exact external evaluator for binary CA(N; 5, 20, 2)."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np


COLUMN_SUBSETS = tuple(itertools.combinations(range(20), 5))


def assess(value: Any) -> tuple[float, dict[str, Any], bool]:
    raw = np.asarray(value)
    if raw.ndim != 2 or raw.shape[1] != 20 or not 32 <= raw.shape[0] <= 2_000:
        raise ValueError(
            "construct_array() must return an N x 20 array, 32 <= N <= 2000"
        )
    try:
        numeric = raw.astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("array entries must be numeric") from exc
    if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, (0.0, 1.0))):
        raise ValueError("all entries must be exactly 0 or 1")
    array = numeric.astype(np.uint8)
    missing = 0
    for columns in COLUMN_SUBSETS:
        patterns = (
            array[:, columns[0]]
            | (array[:, columns[1]] << 1)
            | (array[:, columns[2]] << 2)
            | (array[:, columns[3]] << 3)
            | (array[:, columns[4]] << 4)
        )
        missing += 32 - int(np.count_nonzero(np.bincount(patterns, minlength=32)))
    complete = missing == 0
    rows = len(array)
    score = -float(rows) if complete else -1_000_000.0 - missing - rows / 1_000_000
    return (
        score,
        {
            "rows": rows,
            "uncovered_interactions": missing,
            "is_covering_array": complete,
            "column_subsets_checked": len(COLUMN_SUBSETS),
            "required_patterns_per_subset": 32,
        },
        complete,
    )


def _load(repo_path: Path):
    source = repo_path / "solution.py"
    spec = importlib.util.spec_from_file_location("candidate_solution", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(repo_path: Path) -> tuple[dict[str, Any], bool, str]:
    try:
        score, public, complete = assess(_load(repo_path).construct_array())
        feedback = (
            ""
            if complete
            else f"{public['uncovered_interactions']} interactions are uncovered"
        )
        return (
            {
                "combined_score": score,
                "public": public,
                "text_feedback": feedback,
            },
            complete,
            feedback,
        )
    except Exception as exc:
        return (
            {"combined_score": -2_000_000.0, "public": {}, "text_feedback": str(exc)},
            False,
            str(exc),
        )


def main(repo_path: str, results_dir: str) -> None:
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    metrics, correct, error = evaluate(Path(repo_path).resolve())
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (output / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(args.repo_path, args.results_dir)
