"""External evaluator for ShinkaEvolve's 26-circle paper task."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any, tolerance: float = 1e-6) -> tuple[float, dict[str, Any]]:
    try:
        centers, radii, reported_sum = value
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "run_packing() must return centers, radii, reported_sum"
        ) from exc
    centers = np.asarray(centers, dtype=float)
    radii = np.asarray(radii, dtype=float)
    reported_sum = float(reported_sum)
    if centers.shape != (26, 2) or radii.shape != (26,):
        raise ValueError("expected centers shape (26,2) and radii shape (26,)")
    if (
        not np.all(np.isfinite(centers))
        or not np.all(np.isfinite(radii))
        or not np.isfinite(reported_sum)
    ):
        raise ValueError("packing values must be finite")
    if np.any(radii < 0.0):
        raise ValueError("radii must be nonnegative")
    actual_sum = float(np.sum(radii))
    if not np.isclose(reported_sum, actual_sum, atol=tolerance, rtol=1e-12):
        raise ValueError("reported radius sum does not match the verified sum")
    if np.any(centers - radii[:, None] < -tolerance) or np.any(
        centers + radii[:, None] > 1.0 + tolerance
    ):
        raise ValueError("a circle lies outside the unit square")
    delta = centers[:, None, :] - centers[None, :, :]
    distances = np.linalg.norm(delta, axis=2)
    left, right = np.triu_indices(26, 1)
    if np.any(distances[left, right] < radii[left] + radii[right] - tolerance):
        raise ValueError("circles overlap")
    return actual_sum, {
        "sum_radii": actual_sum,
        "validation_tolerance": tolerance,
        "num_circles": 26,
    }


def _load(repo_path: Path):
    source = repo_path / "solution.py"
    spec = importlib.util.spec_from_file_location("candidate_solution", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(
    repo_path: Path, tolerance: float = 1e-6
) -> tuple[dict[str, Any], bool, str]:
    try:
        score, public = assess(_load(repo_path).run_packing(), tolerance)
        return {"combined_score": score, "public": public}, True, ""
    except Exception as exc:
        return (
            {"combined_score": 0.0, "public": {}, "text_feedback": str(exc)},
            False,
            str(exc),
        )


def main(repo_path: str, results_dir: str, tolerance: float) -> None:
    if not np.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be finite and nonnegative")
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    metrics, correct, error = evaluate(Path(repo_path).resolve(), tolerance)
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
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()
    main(args.repo_path, args.results_dir, args.tolerance)
