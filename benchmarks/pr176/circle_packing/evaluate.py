"""Self-contained evaluator for the frozen PR #176 circle-packing task."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
import traceback
from pathlib import Path
from typing import Any, Optional

import numpy as np


def _load_program(program_path: str) -> Any:
    spec = importlib.util.spec_from_file_location("candidate", program_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load candidate: {program_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate(
    result: Any,
    atol: float = 0.0,
) -> tuple[bool, Optional[str], Optional[tuple[np.ndarray, np.ndarray, float]]]:
    if not isinstance(result, tuple) or len(result) != 3:
        return False, "run_packing() must return (centers, radii, reported_sum)", None

    centers, radii, reported_sum = result
    centers = np.asarray(centers)
    radii = np.asarray(radii)
    try:
        reported_sum = float(reported_sum)
    except (TypeError, ValueError):
        return False, "reported_sum must be numeric", None

    if centers.shape != (26, 2):
        return False, f"centers shape must be (26, 2), got {centers.shape}", None
    if radii.shape != (26,):
        return False, f"radii shape must be (26,), got {radii.shape}", None
    if (
        not np.all(np.isfinite(centers))
        or not np.all(np.isfinite(radii))
        or not np.isfinite(reported_sum)
    ):
        return False, "centers, radii, and reported_sum must be finite", None
    if np.any(radii < 0):
        return False, "radii must be nonnegative", None
    if not np.isclose(np.sum(radii), reported_sum, atol=atol):
        return False, "reported_sum does not match the sum of radii", None

    for index, ((x_coord, y_coord), radius) in enumerate(zip(centers, radii)):
        if (
            x_coord - radius < -atol
            or x_coord + radius > 1 + atol
            or y_coord - radius < -atol
            or y_coord + radius > 1 + atol
        ):
            return False, f"circle {index} is outside the unit square", None

    for left in range(26):
        for right in range(left + 1, 26):
            distance = np.linalg.norm(centers[left] - centers[right])
            if distance < radii[left] + radii[right] - atol:
                return False, f"circles {left} and {right} overlap", None

    return True, None, (centers, radii, reported_sum)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(program_path: str, results_dir: str) -> None:
    output_dir = Path(results_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    correct = False
    error: Optional[str] = None
    metrics: dict[str, Any] = {
        "combined_score": 0.0,
        "public": {},
        "private": {},
    }

    try:
        module = _load_program(program_path)
        if not hasattr(module, "run_packing"):
            raise AttributeError("Candidate does not define run_packing()")
        valid, error, normalized = _validate(module.run_packing())
        correct = valid
        if valid and normalized is not None:
            centers, radii, reported_sum = normalized
            metrics = {
                "combined_score": reported_sum,
                "public": {"num_circles": 26},
                "private": {
                    "reported_sum_of_radii": reported_sum,
                    "minimum_radius": float(np.min(radii)),
                    "maximum_radius": float(np.max(radii)),
                },
            }
    except Exception as exc:  # Candidate failures are evaluator results.
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    metrics["execution_time_mean"] = time.perf_counter() - started
    metrics["num_valid_runs"] = int(correct)
    metrics["num_invalid_runs"] = int(not correct)
    _write_json(output_dir / "metrics.json", metrics)
    _write_json(output_dir / "correct.json", {"correct": correct, "error": error})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(args.program_path, args.results_dir)
