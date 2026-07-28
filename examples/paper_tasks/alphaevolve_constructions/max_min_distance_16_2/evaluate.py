"""External verifier for AlphaEvolve's (n,d)=(16,2) distance-ratio task.

The squared distance-ratio objective follows section B.8 of AlphaEvolve's
released ``mathematical_results.ipynb``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, float]]:
    points = np.asarray(value, dtype=float)
    if points.shape != (16, 2) or not np.all(np.isfinite(points)):
        raise ValueError("construct() must return a finite array with shape (16, 2)")
    delta = points[:, None, :] - points[None, :, :]
    squared = np.einsum("ijk,ijk->ij", delta, delta)[np.triu_indices(16, 1)]
    minimum = float(np.min(squared))
    if minimum <= 0.0:
        raise ValueError("all points must be distinct")
    ratio_squared = float(np.max(squared) / minimum)
    return -ratio_squared, {"ratio_squared": ratio_squared}


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
