#!/usr/bin/env python3
"""Small task-local wrapper around the canonical repo-mode Shinka CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from shinka.cli.run import main as shinka_run_main


def main() -> int:
    parser = argparse.ArgumentParser(description="Run this converted Shinka task.")
    parser.add_argument("--config_path", default="shinka.yaml")
    parser.add_argument("--results_dir", default="results/run")
    parser.add_argument("--num_generations", type=int, default=10)
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    args = parser.parse_args()
    if args.num_generations <= 0:
        parser.error("--num_generations must be positive")

    task_dir = Path(__file__).resolve().parent
    config_path = Path(args.config_path).expanduser()
    if not config_path.is_absolute():
        config_path = task_dir / config_path
    results_path = Path(args.results_dir).expanduser()
    if not results_path.is_absolute():
        results_path = task_dir / results_path

    cli_args = [
        "--task-dir",
        str(task_dir),
        "--config-fname",
        str(config_path.resolve()),
        "--results_dir",
        str(results_path.resolve()),
        "--num_generations",
        str(args.num_generations),
    ]
    for override in args.overrides:
        cli_args.extend(["--set", override])
    return shinka_run_main(cli_args)


if __name__ == "__main__":
    raise SystemExit(main())
