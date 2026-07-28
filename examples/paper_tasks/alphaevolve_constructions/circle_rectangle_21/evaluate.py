"""External verifier for AlphaEvolve's 21-circle perimeter-4 rectangle task.

The disjointness, circumscribing rectangle, and sum-of-radii checks follow
section B.13 of AlphaEvolve's released ``mathematical_results.ipynb``.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, float]]:
    circles = np.asarray(value, dtype=float)
    if circles.shape != (21, 3) or not np.all(np.isfinite(circles)):
        raise ValueError(
            "construct() must return finite (x,y,r) rows with shape (21, 3)"
        )
    if np.any(circles[:, 2] < 0.0):
        raise ValueError("radii must be non-negative")
    for left, right in itertools.combinations(circles, 2):
        if math.hypot(left[0] - right[0], left[1] - right[1]) < left[2] + right[2]:
            raise ValueError("circles overlap")
    width = float(
        np.max(circles[:, 0] + circles[:, 2]) - np.min(circles[:, 0] - circles[:, 2])
    )
    height = float(
        np.max(circles[:, 1] + circles[:, 2]) - np.min(circles[:, 1] - circles[:, 2])
    )
    perimeter = 2.0 * (width + height)
    if perimeter > 4.0:
        raise ValueError(
            "the minimum enclosing axis-aligned rectangle has perimeter above 4"
        )
    score = float(np.sum(circles[:, 2]))
    return score, {"sum_radii": score, "rectangle_perimeter": perimeter}


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
