"""Adapter for the paper-scale ShinkaEvolve MoE training evaluator."""

from __future__ import annotations

import argparse
import ast
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from shinka.core.wrap_eval import save_json_results


def _validate_source(program_path: str) -> None:
    source = Path(program_path).read_text()
    tree = ast.parse(source, filename=program_path)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "load_balancing_loss" not in names:
        raise ValueError("candidate must define load_balancing_loss")


def _load_training_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    cross_entropy = float(data["final_cross_entropy_last_10m_tokens"])
    imbalance = float(data["load_imbalance_l1"])
    if not math.isfinite(cross_entropy) or not math.isfinite(imbalance):
        raise ValueError("training metrics must be finite")
    if cross_entropy <= 0.0 or not 0.0 <= imbalance <= 1.0:
        raise ValueError("training metrics are outside their valid ranges")
    return {**data, "cross_entropy": cross_entropy, "imbalance": imbalance}


def main(
    repo_path: str,
    results_dir: str,
    trainer: str | None,
    config: str,
    validate_only: bool,
) -> None:
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    program_path = str(Path(repo_path).resolve() / "loss.py")
    try:
        _validate_source(program_path)
        if validate_only:
            metrics = {
                "combined_score": 0.0,
                "public": {
                    "mode": "interface-validation-only",
                    "warning": "This is not a fitness evaluation.",
                },
            }
            save_json_results(results_dir, metrics, True)
            return
        if trainer is None:
            raise ValueError(
                "paper-faithful evaluation requires --trainer pointing to the external MoE training harness"
            )
        summary_path = output / "training_metrics.json"
        summary_path.unlink(missing_ok=True)
        command = [
            trainer,
            "--program_path",
            str(Path(program_path).resolve()),
            "--config",
            str(Path(config).resolve()),
            "--output",
            str(summary_path.resolve()),
        ]
        subprocess.run(command, check=True)
        summary = _load_training_summary(summary_path)
        # Equation (5) in the ShinkaEvolve paper.
        score = -(summary["cross_entropy"] + summary["imbalance"])
        metrics = {
            "combined_score": float(score),
            "public": {
                "final_cross_entropy_last_10m_tokens": summary["cross_entropy"],
                "load_imbalance_l1": summary["imbalance"],
            },
            "private": {
                key: value
                for key, value in summary.items()
                if key not in {"cross_entropy", "imbalance"}
            },
        }
        save_json_results(results_dir, metrics, True)
    except Exception as exc:
        save_json_results(
            results_dir,
            {"combined_score": 0.0, "public": {}, "private": {}},
            False,
            str(exc),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    parser.add_argument("--trainer")
    parser.add_argument(
        "--config", default=str(Path(__file__).with_name("small_moe_556m.json"))
    )
    parser.add_argument("--validate_only", action="store_true")
    args = parser.parse_args()
    main(
        args.repo_path,
        args.results_dir,
        args.trainer,
        args.config,
        args.validate_only,
    )
