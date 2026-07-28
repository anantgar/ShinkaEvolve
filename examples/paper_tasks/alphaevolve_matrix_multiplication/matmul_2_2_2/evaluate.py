"""Exact verifier for a fixed 2x2x2 matrix-multiplication tensor.

The tensor construction and strict equality check follow the generic verifier
released in AlphaEvolve's ``mathematical_results.ipynb``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np


def assess(value: Any) -> tuple[float, dict[str, Any]]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("construct() must return three factor matrices")
    factors = tuple(np.asarray(factor) for factor in value)
    if any(factor.ndim != 2 for factor in factors):
        raise ValueError("all factors must be matrices")
    if any(
        not np.issubdtype(factor.dtype, np.number) or not np.all(np.isfinite(factor))
        for factor in factors
    ):
        raise ValueError("all factor entries must be finite numbers")
    ranks = {factor.shape[1] for factor in factors}
    if len(ranks) != 1:
        raise ValueError("all factors must have the same column count")
    rank = ranks.pop()
    if tuple(factor.shape for factor in factors) != ((4, rank), (4, rank), (4, rank)):
        raise ValueError("all factors must have shape (4, rank)")
    if not 1 <= rank <= 8:
        raise ValueError("rank must be between 1 and the schoolbook rank 8")
    target = np.zeros((4, 4, 4), dtype=np.int32)
    for i in range(2):
        for j in range(2):
            for k in range(2):
                target[2 * i + j, 2 * j + k, 2 * k + i] = 1.0
    constructed = np.einsum("ir,jr,kr->ijk", *factors, optimize=True)
    if not np.array_equal(constructed, target):
        error = float(np.max(np.abs(constructed - target)))
        raise ValueError(f"decomposition is not exact; maximum error {error}")
    entries = sorted({str(entry) for factor in factors for entry in np.unique(factor)})
    return -float(rank), {"rank": rank, "factor_entries": entries}


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
        module = _load(repo_path)
        assessed = []
        trial_errors = []
        for seed in range(3):
            try:
                assessed.append(assess(module.construct(seed)))
            except Exception as exc:
                trial_errors.append(f"seed {seed}: {exc}")
        if not assessed:
            raise ValueError("all three trials failed: " + "; ".join(trial_errors))
        best_score, best_public = max(assessed, key=lambda item: item[0])
        best_rank = int(-best_score)
        success_fraction = (
            sum(public["rank"] == best_rank for _, public in assessed) / 3
        )
        metrics = {
            "combined_score": best_score + success_fraction / 1000.0,
            "public": {
                "target": [2, 2, 2],
                "best_rank": best_rank,
                "best_rank_success_fraction": success_fraction,
                "successful_trials": len(assessed),
                **best_public,
            },
        }
        return metrics, True, ""
    except Exception as exc:
        return (
            {"combined_score": -9.0, "public": {}, "text_feedback": str(exc)},
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
