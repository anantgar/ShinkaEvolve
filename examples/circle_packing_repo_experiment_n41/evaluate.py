"""Secure evaluator for the 41-circle unit-square packing task."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


N_CIRCLES = 41
ATOL = 1e-8
PUBLIC_METRICS = (
    "num_circles",
    "valid",
    "sum_radii",
    "min_radius",
    "max_radius",
    "max_boundary_violation",
    "max_overlap_violation",
)


def _unpack(output: Any) -> tuple[np.ndarray, np.ndarray, float]:
    if isinstance(output, dict):
        centers = output["centers"]
        radii = output["radii"]
        reported_sum = output["reported_sum"]
    elif isinstance(output, (tuple, list)) and len(output) == 3:
        centers, radii, reported_sum = output
    else:
        raise ValueError("candidate output must contain centers, radii, and reported_sum")
    return (
        np.asarray(centers, dtype=float),
        np.asarray(radii, dtype=float),
        float(reported_sum),
    )


def _geometry_metrics(
    centers: np.ndarray, radii: np.ndarray
) -> tuple[float, float]:
    boundary = np.maximum.reduce(
        [
            -(centers[:, 0] - radii),
            centers[:, 0] + radii - 1.0,
            -(centers[:, 1] - radii),
            centers[:, 1] + radii - 1.0,
        ]
    )
    max_boundary = max(0.0, float(np.max(boundary)))
    max_overlap = 0.0
    for index in range(N_CIRCLES):
        deltas = centers[index + 1 :] - centers[index]
        if len(deltas):
            violations = radii[index] + radii[index + 1 :] - np.linalg.norm(
                deltas, axis=1
            )
            max_overlap = max(max_overlap, 0.0, float(np.max(violations)))
    return max_boundary, max_overlap


def _empty_metrics() -> dict[str, Any]:
    return {
        "num_circles": N_CIRCLES,
        "valid": False,
        "sum_radii": 0.0,
        "min_radius": 0.0,
        "max_radius": 0.0,
        "max_boundary_violation": math.inf,
        "max_overlap_violation": math.inf,
    }


def evaluate(job, candidate_runner, private_inputs, result_writer) -> None:
    del private_inputs
    metrics = _empty_metrics()
    feedback = "candidate did not produce a valid result"
    try:
        with result_writer.phase("candidate"):
            response = candidate_runner.request({"op": "run_packing"})
        centers, radii, reported_sum = _unpack(response.output)
        if centers.shape != (N_CIRCLES, 2):
            raise ValueError(f"centers shape must be ({N_CIRCLES}, 2), got {centers.shape}")
        if radii.shape != (N_CIRCLES,):
            raise ValueError(f"radii shape must be ({N_CIRCLES},), got {radii.shape}")
        if not np.all(np.isfinite(centers)) or not np.all(np.isfinite(radii)):
            raise ValueError("centers and radii must be finite")
        if not np.isfinite(reported_sum) or np.any(radii < -ATOL):
            raise ValueError("reported sum and radii must be finite and non-negative")
        actual_sum = float(np.sum(radii))
        boundary, overlap = _geometry_metrics(centers, radii)
        metrics.update(
            sum_radii=actual_sum,
            min_radius=float(np.min(radii)),
            max_radius=float(np.max(radii)),
            max_boundary_violation=boundary,
            max_overlap_violation=overlap,
        )
        valid = (
            math.isclose(actual_sum, reported_sum, abs_tol=1e-7, rel_tol=0.0)
            and boundary <= ATOL
            and overlap <= ATOL
        )
        metrics["valid"] = valid
        feedback = (
            "valid packing"
            if valid
            else f"invalid geometry: boundary={boundary:.3e}, overlap={overlap:.3e}"
        )
        result_writer.succeed(
            correct=valid,
            combined_score=actual_sum if valid else 0.0,
            public_metrics=metrics,
            public_feedback=feedback,
            private_metrics={"reported_sum": reported_sum},
        )
    except Exception as exc:
        metrics["max_boundary_violation"] = 0.0
        metrics["max_overlap_violation"] = 0.0
        result_writer.succeed(
            correct=False,
            combined_score=0.0,
            public_metrics=metrics,
            public_feedback=f"candidate evaluation failed: {exc}"[:2000],
        )
