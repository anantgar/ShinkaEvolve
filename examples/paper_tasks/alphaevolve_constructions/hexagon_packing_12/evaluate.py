"""External verifier for AlphaEvolve's 12-unit-hexagon packing task.

The separating-axis and containment checks follow section B.7 of
AlphaEvolve's released ``mathematical_results.ipynb``.
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


def _hexagon(x: float, y: float, side: float, angle_degrees: float) -> np.ndarray:
    angles = math.radians(angle_degrees) + 2.0 * math.pi * np.arange(6) / 6.0
    return np.column_stack((x + side * np.cos(angles), y + side * np.sin(angles)))


def _projection(polygon: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    values = polygon @ axis
    return float(np.min(values)), float(np.max(values))


def _intersect(first: np.ndarray, second: np.ndarray) -> bool:
    for polygon in (first, second):
        for edge in np.roll(polygon, -1, axis=0) - polygon:
            axis = np.asarray((-edge[1], edge[0])) / np.linalg.norm(edge)
            first_min, first_max = _projection(first, axis)
            second_min, second_max = _projection(second, axis)
            if first_max < second_min or second_max < first_min:
                return False
    return True


def _inside(point: np.ndarray, polygon: np.ndarray) -> bool:
    edges = np.roll(polygon, -1, axis=0) - polygon
    offsets = point - polygon
    return bool(
        np.all(edges[:, 0] * offsets[:, 1] - edges[:, 1] * offsets[:, 0] >= 0.0)
    )


def assess(value: Any) -> tuple[float, dict[str, float]]:
    if not isinstance(value, dict):
        raise ValueError("construct() must return a dictionary")
    inner = np.asarray(value.get("inner"), dtype=float)
    center = np.asarray(value.get("outer_center"), dtype=float)
    side = float(value.get("outer_side"))
    angle = float(value.get("outer_angle_degrees", 0.0))
    if (
        inner.shape != (12, 3)
        or center.shape != (2,)
        or not np.all(np.isfinite(inner))
        or not np.all(np.isfinite(center))
        or not math.isfinite(side)
        or side <= 0.0
    ):
        raise ValueError("invalid inner or outer hexagon parameters")
    polygons = [_hexagon(x, y, 1.0, rotation) for x, y, rotation in inner]
    if any(
        _intersect(left, right) for left, right in itertools.combinations(polygons, 2)
    ):
        raise ValueError(
            "inner hexagons intersect; the paper verifier rejects tangency"
        )
    outer = _hexagon(center[0], center[1], side, angle)
    if any(not _inside(vertex, outer) for polygon in polygons for vertex in polygon):
        raise ValueError("an inner hexagon is not contained in the outer hexagon")
    return -side, {"outer_side_length": side}


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
            {"combined_score": -1.0e30, "public": {}, "text_feedback": str(exc)},
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
