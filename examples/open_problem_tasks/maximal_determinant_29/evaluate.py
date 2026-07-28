"""Exact external evaluator for the order-29 maximal determinant problem."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


RECORD_DETERMINANT = 320 * 7**12 * 2**28


def bareiss_determinant(value: np.ndarray) -> int:
    """Compute an exact integer determinant using fraction-free elimination."""
    matrix = [[int(entry) for entry in row] for row in value.tolist()]
    size = len(matrix)
    sign = 1
    previous_pivot = 1
    for column in range(size - 1):
        pivot_row = next(
            (row for row in range(column, size) if matrix[row][column] != 0),
            None,
        )
        if pivot_row is None:
            return 0
        if pivot_row != column:
            matrix[column], matrix[pivot_row] = matrix[pivot_row], matrix[column]
            sign = -sign
        pivot = matrix[column][column]
        for row in range(column + 1, size):
            for inner in range(column + 1, size):
                numerator = (
                    matrix[row][inner] * pivot
                    - matrix[row][column] * matrix[column][inner]
                )
                matrix[row][inner] = numerator // previous_pivot
            matrix[row][column] = 0
        previous_pivot = pivot
    return sign * matrix[-1][-1]


def assess(value: Any) -> tuple[float, dict[str, Any]]:
    raw = np.asarray(value)
    if raw.shape != (29, 29):
        raise ValueError("construct_matrix() must return shape (29, 29)")
    try:
        numeric = raw.astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("matrix entries must be numeric") from exc
    if not np.all(np.isfinite(numeric)) or not np.all(np.isin(numeric, (-1.0, 1.0))):
        raise ValueError("every matrix entry must be exactly +1 or -1")
    determinant = abs(bareiss_determinant(numeric.astype(np.int8)))
    score = math.log(determinant) if determinant else 0.0
    return score, {
        "absolute_determinant": determinant,
        "record_determinant": RECORD_DETERMINANT,
        "matches_or_beats_record": determinant >= RECORD_DETERMINANT,
    }


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
        score, public = assess(_load(repo_path).construct_matrix())
        return {"combined_score": score, "public": public}, True, ""
    except Exception as exc:
        return (
            {"combined_score": -1.0, "public": {}, "text_feedback": str(exc)},
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
