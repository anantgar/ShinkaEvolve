"""External verifier for AlphaEvolve's 11-point Heilbronn triangle task.

Containment and area normalization follow section B.9 of AlphaEvolve's
released ``mathematical_results.ipynb``.
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


def _area(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])) / 2.0


def assess(value: Any) -> tuple[float, dict[str, float]]:
    points = np.asarray(value, dtype=float)
    if points.shape != (11, 2) or not np.all(np.isfinite(points)):
        raise ValueError("construct() must return a finite array with shape (11, 2)")
    root_three = math.sqrt(3.0)
    for x, y in points:
        if not (y >= 0.0 and root_three * x <= root_three - y and y <= root_three * x):
            raise ValueError("a point lies outside the unit equilateral triangle")
    minimum = min(_area(*triple) for triple in itertools.combinations(points, 3))
    normalized = minimum / (root_three / 4.0)
    if normalized <= 0.0:
        raise ValueError("three points are collinear or duplicated")
    return normalized, {"minimum_normalized_triangle_area": normalized}


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
