"""Exact integer verifier for AlphaEvolve's 11D kissing construction.

Rounding and the norm/distance certificate follow section B.11 of
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


def assess(value: Any) -> tuple[float, dict[str, int]]:
    raw = np.asarray(value)
    if raw.ndim != 2 or raw.shape[1] != 11 or not 2 <= len(raw) <= 2_000:
        raise ValueError("construct() must return between 2 and 2,000 vectors in R^11")
    if (
        not np.issubdtype(raw.dtype, np.number)
        or np.issubdtype(raw.dtype, np.complexfloating)
        or not np.all(np.isfinite(raw))
    ):
        raise ValueError("coordinates must be finite real numbers")
    rounded = np.around(raw)
    if np.max(np.abs(rounded)) > np.iinfo(np.int64).max:
        raise ValueError("rounded coordinates do not fit in int64")
    points = [tuple(int(coordinate) for coordinate in point) for point in rounded]
    norms = [sum(int(x) ** 2 for x in point) for point in points]
    if min(norms) == 0:
        raise ValueError("the construction contains the origin")
    max_norm = max(norms)
    min_distance = min(
        sum((x - y) ** 2 for x, y in zip(left, right))
        for left, right in itertools.combinations(points, 2)
    )
    if min_distance < max_norm:
        raise ValueError("minimum squared distance is below maximum squared norm")
    return float(len(points)), {
        "num_spheres": len(points),
        "max_squared_norm": max_norm,
        "min_squared_distance": min_distance,
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
