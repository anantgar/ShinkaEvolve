"""
Evaluator for circle packing example (n=26) with improved timeout handling
"""

import os
import argparse
import numpy as np
from typing import Tuple, Optional, List, Dict, Any

from shinka.core import run_shinka_eval


def format_centers_string(centers: np.ndarray) -> str:
    """Formats circle centers into a multi-line string for display."""
    return "\n".join(
        [
            f"  centers[{i}] = ({x_coord:.4f}, {y_coord:.4f})"
            for i, (x_coord, y_coord) in enumerate(centers)
        ]
    )


def adapted_validate_packing(
    run_output: Tuple[np.ndarray, np.ndarray, float],
    atol=1e-6,
) -> Tuple[bool, Optional[str]]:
    """
    Validates circle packing results based on the output of 'run_packing'.

    Args:
        run_output: Tuple (centers, radii, reported_sum) from run_packing.

    Returns:
        (is_valid: bool, error_message: Optional[str])
    """
    try:
        centers, radii, reported_sum = run_output
    except (TypeError, ValueError):
        return False, "run_packing must return (centers, radii, reported_sum)"
    msg = "The circles are placed correctly. There are no overlaps or any circles outside the unit square."
    try:
        centers = np.asarray(centers, dtype=float)
        radii = np.asarray(radii, dtype=float)
        reported_sum = float(reported_sum)
    except (TypeError, ValueError, OverflowError) as exc:
        return False, f"Could not coerce packing output to numeric arrays: {exc}"

    n_expected = 26
    if centers.shape != (n_expected, 2):
        msg = (
            f"Centers shape incorrect. Expected ({n_expected}, 2), got {centers.shape}"
        )
        return False, msg
    if radii.shape != (n_expected,):
        msg = f"Radii shape incorrect. Expected ({n_expected},), got {radii.shape}"
        return False, msg

    if (
        not np.all(np.isfinite(centers))
        or not np.all(np.isfinite(radii))
        or not np.isfinite(reported_sum)
    ):
        msg = "Non-finite values found in centers, radii, or reported_sum."
        return False, msg

    if np.any(radii < 0):
        negative_indices = np.where(radii < 0)[0]
        msg = f"Negative radii found for circles at indices: {negative_indices}"
        return False, msg

    actual_sum = float(np.sum(radii))
    if not np.isclose(actual_sum, reported_sum, atol=atol, rtol=1e-12):
        msg = (
            f"Sum of radii ({actual_sum:.12f}) does not match "
            f"reported ({reported_sum:.6f})"
        )
        return False, msg

    for i in range(n_expected):
        x, y = centers[i]
        r = radii[i]
        is_outside = (
            x - r < -atol or x + r > 1 + atol or y - r < -atol or y + r > 1 + atol
        )
        if is_outside:
            msg = (
                f"Circle {i} (x={x:.4f}, y={y:.4f}, r={r:.4f}) is outside unit square."
            )
            return False, msg

    for i in range(n_expected):
        for j in range(i + 1, n_expected):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))
            if dist < radii[i] + radii[j] - atol:
                msg = (
                    f"Circles {i} & {j} overlap. Dist: {dist:.4f}, "
                    f"Sum Radii: {(radii[i] + radii[j]):.4f}"
                )
                return False, msg
    return True, msg


def get_circle_packing_kwargs(run_index: int) -> Dict[str, Any]:
    """Provides keyword arguments for circle packing runs (none needed)."""
    return {}


def aggregate_circle_packing_metrics(
    results: List[Tuple[np.ndarray, np.ndarray, float]], results_dir: str
) -> Dict[str, Any]:
    """
    Aggregates metrics for circle packing. Assumes num_runs=1.
    Saves extra.npz with detailed packing information.
    """
    if not results:
        return {"combined_score": 0.0, "error": "No results to aggregate"}

    centers, radii, reported_sum = results[0]
    centers = np.asarray(centers, dtype=float)
    radii = np.asarray(radii, dtype=float)
    reported_sum = float(reported_sum)
    actual_sum = float(np.sum(radii))

    public_metrics = {
        "centers_str": format_centers_string(centers),
        "num_circles": centers.shape[0],
    }
    private_metrics = {
        "reported_sum_of_radii": float(reported_sum),
        "verified_sum_of_radii": actual_sum,
    }
    metrics = {
        # Never score the candidate's self-reported value. The verifier computes
        # the objective directly from the radii.
        "combined_score": actual_sum,
        "public": public_metrics,
        "private": private_metrics,
    }

    extra_file = os.path.join(results_dir, "extra.npz")
    try:
        np.savez(
            extra_file,
            centers=centers,
            radii=radii,
            reported_sum=reported_sum,
        )
        print(f"Detailed packing data saved to {extra_file}")
    except Exception as e:
        print(f"Error saving extra.npz: {e}")
        metrics["extra_npz_save_error"] = str(e)

    return metrics


def main(program_path: str, results_dir: str, validation_atol: float = 1e-6):
    """Runs the circle packing evaluation using shinka.eval."""
    if validation_atol < 0 or not np.isfinite(validation_atol):
        raise ValueError("validation_atol must be finite and non-negative")
    print(f"Evaluating program: {program_path}")
    print(f"Saving results to: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)

    num_experiment_runs = 1

    # Define a nested function to pass results_dir to the aggregator
    def _aggregator_with_context(
        r: List[Tuple[np.ndarray, np.ndarray, float]],
    ) -> Dict[str, Any]:
        return aggregate_circle_packing_metrics(r, results_dir)

    metrics, correct, error_msg = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_packing",
        num_runs=num_experiment_runs,
        get_experiment_kwargs=get_circle_packing_kwargs,
        validate_fn=lambda output: adapted_validate_packing(
            output, atol=validation_atol
        ),
        aggregate_metrics_fn=_aggregator_with_context,
    )

    if correct:
        print("Evaluation and Validation completed successfully.")
    else:
        print(f"Evaluation or Validation failed: {error_msg}")

    print("Metrics:")
    for key, value in metrics.items():
        if isinstance(value, str) and len(value) > 100:
            print(f"  {key}: <string_too_long_to_display>")
        else:
            print(f"  {key}: {value}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Circle packing evaluator using shinka.eval"
    )
    parser.add_argument(
        "--program_path",
        type=str,
        default="initial.py",
        help="Path to program to evaluate (must contain 'run_packing')",
    )
    parser.add_argument(
        "--results_dir",
        type=str,
        default="results",
        help="Dir to save results (metrics.json, correct.json, extra.npz)",
    )
    parser.add_argument(
        "--atol",
        type=float,
        default=1e-6,
        help="Validation tolerance; use 0 for the paper's exact condition",
    )
    parsed_args = parser.parse_args()
    main(parsed_args.program_path, parsed_args.results_dir, parsed_args.atol)
