"""External verifier for AlphaEvolve's convex-body Heilbronn n=13 task.

The minimum-triangle/convex-hull-area objective follows section B.10 of
AlphaEvolve's released ``mathematical_results.ipynb``.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np


def _area(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])) / 2.0


def _hull(points: np.ndarray) -> np.ndarray:
    ordered = sorted(set(map(tuple, points.tolist())))
    if len(ordered) < 3:
        raise ValueError("at least three distinct points are required")

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return np.asarray(lower[:-1] + upper[:-1])


def assess(value: Any) -> tuple[float, dict[str, float]]:
    points = np.asarray(value, dtype=float)
    if points.shape != (13, 2) or not np.all(np.isfinite(points)):
        raise ValueError("construct() must return a finite array with shape (13, 2)")
    hull = _hull(points)
    hull_area = (
        abs(
            np.dot(hull[:, 0], np.roll(hull[:, 1], -1))
            - np.dot(hull[:, 1], np.roll(hull[:, 0], -1))
        )
        / 2.0
    )
    if hull_area <= 0.0:
        raise ValueError("convex hull has zero area")
    minimum = min(_area(*triple) for triple in itertools.combinations(points, 3))
    normalized = float(minimum / hull_area)
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
