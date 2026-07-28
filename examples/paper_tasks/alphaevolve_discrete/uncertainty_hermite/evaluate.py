"""Evaluator for AlphaEvolve's Hermite uncertainty construction.

The Hermite construction and root/sign-change check follow section B.4 of
AlphaEvolve's released ``mathematical_results.ipynb`` without requiring a
symbolic-math runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.polynomial.hermite import herm2poly
from numpy.polynomial.polynomial import polyroots, polyval


def _hermite_power_coefficients(degree: int) -> np.ndarray:
    coefficients = np.zeros(degree + 1)
    coefficients[degree] = 1.0
    return herm2poly(coefficients)


def _find_hermite_combination(coefficients: np.ndarray) -> np.ndarray:
    """Construct the Hermite combination as ascending power coefficients."""
    degrees = range(0, 4 * len(coefficients) + 4, 4)
    hermite_polynomials = [_hermite_power_coefficients(degree) for degree in degrees]
    constant_term = sum(
        coefficients[index] * hermite_polynomials[index][0]
        for index in range(len(coefficients))
    )
    last_coefficient = -constant_term / hermite_polynomials[-1][0]
    all_coefficients = np.append(coefficients, last_coefficient)
    expression = np.zeros(len(hermite_polynomials[-1]))
    for coefficient, polynomial in zip(all_coefficients, hermite_polynomials):
        expression[: len(polynomial)] += coefficient * polynomial
    if not np.all(np.isfinite(expression)):
        raise ValueError("the derived Hermite combination is not finite")
    if expression[-1] < 0:
        expression *= -1
    scale = float(np.max(np.abs(expression)))
    if scale == 0:
        raise ValueError("the derived Hermite combination is identically zero")
    expression /= scale
    if abs(expression[0]) > 1e-12:
        raise ValueError("the derived Hermite combination does not vanish at zero")
    expression[0] = 0.0
    return expression


def _sign_changes_at_root(polynomial: np.ndarray, root: float) -> bool:
    scale = max(1.0, abs(root))
    for relative_step in (1e-8, 1e-7, 1e-6, 1e-5):
        step = relative_step * scale
        left = float(polyval(root - step, polynomial))
        right = float(polyval(root + step, polynomial))
        if np.isfinite(left) and np.isfinite(right) and left * right < 0:
            return True
    return False


def _upper_bound(expression: np.ndarray) -> tuple[float, float]:
    """Compute the largest numerically isolated sign-changing-root certificate."""
    if expression[-1] <= 0:
        raise ValueError("the derived Hermite combination is not positive at infinity")
    quotient = expression[2:]
    roots = polyroots(quotient)
    positive_sign_changes = [
        float(root.real)
        for root in roots
        if np.isfinite(root)
        and root.real > 0
        and abs(root.imag) <= 1e-7 * max(1.0, abs(root.real))
        and _sign_changes_at_root(quotient, float(root.real))
    ]
    if not positive_sign_changes:
        raise ValueError("P/x^2 has no positive sign-changing root")
    largest_root = max(positive_sign_changes)
    bound = largest_root**2 / (2.0 * np.pi)
    return bound, largest_root


def assess(value: Any) -> tuple[float, dict[str, float]]:
    coefficients = np.asarray(value, dtype=float)
    if coefficients.shape != (3,) or not np.all(np.isfinite(coefficients)):
        raise ValueError("construct() must return H_0, H_4, and H_8 coefficients")
    expression = _find_hermite_combination(coefficients)
    bound, largest_root = _upper_bound(expression)
    return -bound, {"upper_bound_c4": bound, "largest_sign_changing_root": largest_root}


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
