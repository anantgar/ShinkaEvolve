"""Exact evaluator for AlphaEvolve's sums-versus-differences construction.

The boolean-array counter and lower-bound formula are ported from section B.6
of AlphaEvolve's released ``mathematical_results.ipynb``. Local size and range
caps remain as explicit resource guards for repeated evolution runs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, Any]]:
    raw = np.asarray(value)
    if raw.ndim != 1 or not 2 <= len(raw) <= 2_500:
        raise ValueError("construct() must return 2 to 2,500 entries")
    numeric = raw.astype(float)
    if not np.all(np.isfinite(numeric)) or not np.array_equal(
        numeric, np.rint(numeric)
    ):
        raise ValueError("set entries must be finite integers")
    values = np.unique(raw.astype(np.int64))
    if len(values) != len(raw) or values[0] != 0 or values[-1] <= 0:
        raise ValueError("U must be distinct nonnegative integers containing zero")
    max_value = int(values[-1])
    if max_value > 5_000_000:
        raise ValueError("maximum entry exceeds the evaluator resource cap")
    differences = np.zeros(2 * max_value + 1, dtype=bool)
    sums = np.zeros(2 * max_value + 1, dtype=bool)
    for value in values:
        differences[value - values + max_value] = True
        sums[value + values] = True
    difference_count = int(np.sum(differences))
    sum_count = int(np.sum(sums))
    bound = 1.0 + math.log(difference_count / sum_count) / math.log(2 * max_value + 1)
    return bound, {
        "lower_bound_c6": bound,
        "set_size": len(values),
        "max_value": max_value,
        "difference_set_size": difference_count,
        "sumset_size": sum_count,
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
        score, public = assess(_load(repo_path).construct())
        return {"combined_score": score, "public": public}, True, ""
    except Exception as exc:
        return (
            {"combined_score": 0.0, "public": {}, "text_feedback": str(exc)},
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
