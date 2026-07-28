"""Exact external evaluator for a 29-mark Golomb ruler."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, Any], bool]:
    raw = np.asarray(value)
    if raw.shape != (29,):
        raise ValueError("construct_marks() must return exactly 29 marks")
    try:
        numeric = raw.astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("marks must be numeric integers") from exc
    if not np.all(np.isfinite(numeric)) or not np.array_equal(
        numeric, np.rint(numeric)
    ):
        raise ValueError("marks must be finite integers")
    marks = [int(mark) for mark in numeric]
    if marks[0] != 0:
        raise ValueError("the first mark must be zero")
    if any(left >= right for left, right in zip(marks, marks[1:])):
        raise ValueError("marks must be strictly increasing")
    if marks[-1] > 1_000_000_000:
        raise ValueError("ruler length exceeds the evaluator resource cap")

    differences = [
        marks[right] - marks[left]
        for right in range(1, len(marks))
        for left in range(right)
    ]
    multiplicities = Counter(differences)
    repeated_values = sum(count - 1 for count in multiplicities.values())
    feasible = repeated_values == 0
    length = marks[-1]
    score = (
        -float(length)
        if feasible
        else -1_000_000_000.0 - repeated_values - length / 1_000_000_000
    )
    return (
        score,
        {
            "marks": marks,
            "length": length,
            "distinct_differences": len(multiplicities),
            "required_distinct_differences": 29 * 28 // 2,
            "repeated_difference_count": repeated_values,
            "is_golomb_ruler": feasible,
        },
        feasible,
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
        score, public, feasible = assess(_load(repo_path).construct_marks())
        feedback = (
            ""
            if feasible
            else f"{public['repeated_difference_count']} repeated differences"
        )
        return (
            {
                "combined_score": score,
                "public": public,
                "text_feedback": feedback,
            },
            feasible,
            feedback,
        )
    except Exception as exc:
        return (
            {
                "combined_score": -2_000_000_000.0,
                "public": {},
                "text_feedback": str(exc),
            },
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
