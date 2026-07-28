"""External evaluator for AlphaEvolve's C2 autocorrelation bound.

The piecewise-linear integral and objective follow section B.2 of
AlphaEvolve's released ``mathematical_results.ipynb``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, float]]:
    sequence = np.asarray(value, dtype=float)
    if sequence.shape != (50,) or not np.all(np.isfinite(sequence)):
        raise ValueError("construct() must return 50 finite values")
    if np.any(sequence < 0.0) or float(np.sum(sequence)) == 0.0:
        raise ValueError("the sequence must be nonnegative with nonzero sum")
    convolution = np.convolve(sequence, sequence)
    y_points = np.concatenate(([0.0], convolution, [0.0]))
    interval_width = 1.0 / (len(convolution) + 1)
    l2_squared = sum(
        interval_width * (left * left + left * right + right * right) / 3.0
        for left, right in zip(y_points, y_points[1:])
    )
    norm_one = float(np.sum(np.abs(convolution)) / (len(convolution) + 1))
    norm_inf = float(np.max(np.abs(convolution)))
    bound = float(l2_squared / (norm_one * norm_inf))
    return bound, {"lower_bound_c2": bound}


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
