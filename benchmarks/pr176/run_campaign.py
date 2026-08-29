#!/usr/bin/env python3
"""Build and run the paired PR #176 benchmark campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
TASK_DIR = ROOT / "benchmarks" / "pr176" / "circle_packing"
CONFIG_PATH = TASK_DIR / "benchmark.yaml"
UPSTREAM_BASE = "9912af12d423504b8d580f4179fd15f5f88b8c50"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-2"
WANDB_PROJECT = "shinka-pr176-benchmark"
DEFAULT_SEEDS = (1729, 2718, 3141)
POLICY_TO_ARM = {
    "random": "baseline",
    "embedding_distance": "treatment",
}
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _run(argv: list[str], *, capture: bool = False) -> str:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout.strip() if capture else ""


def _git(*args: str) -> str:
    return _run(["git", *args], capture=True)


def _build_image() -> str:
    tag = f"shinka-pr176-evaluator:{_git('rev-parse', '--short', 'HEAD')}"
    _run(
        [
            "docker",
            "build",
            "--pull=false",
            "--tag",
            tag,
            str(TASK_DIR),
        ]
    )
    image_id = _run(
        ["docker", "image", "inspect", "--format={{.Id}}", tag],
        capture=True,
    )
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise RuntimeError(f"Docker returned an unexpected image ID: {image_id!r}")
    return image_id


def _seed_for_repeat(repeat: int) -> int:
    if repeat <= len(DEFAULT_SEEDS):
        return DEFAULT_SEEDS[repeat - 1]
    return DEFAULT_SEEDS[-1] + 997 * (repeat - len(DEFAULT_SEEDS))


def _run_order(repeat: int, policies: list[str]) -> list[str]:
    if len(policies) == 2 and repeat % 2 == 0:
        return list(reversed(policies))
    return list(policies)


def _command(
    *,
    policy: str,
    repeat: int,
    seed: int,
    model: str,
    image: str,
    generations: int,
    results_root: Path,
) -> tuple[Path, list[str]]:
    arm = POLICY_TO_ARM[policy]
    results_dir = results_root / f"{arm}-r{repeat}"
    argv = [
        sys.executable,
        "-m",
        "shinka.cli.run",
        "--task-dir",
        str(TASK_DIR),
        "--config-fname",
        CONFIG_PATH.name,
        "--results_dir",
        str(results_dir),
        "--num_generations",
        str(generations),
        "--set",
        f"evo.crossover_inspiration_selection={policy}",
        "--set",
        f"evo.random_seed={seed}",
        "--set",
        f"evo.llm_models={json.dumps([model])}",
        "--set",
        f"job.image={image}",
        "--set",
        f"evo.wandb_group={results_root.name}",
    ]
    return results_dir, argv


def _required_keys(model: str) -> list[str]:
    keys = ["GEMINI_API_KEY"]  # Gemini embeddings are fixed for both arms.
    if model.startswith("azure-"):
        keys.extend(["AZURE_OPENAI_API_KEY", "AZURE_API_ENDPOINT"])
    elif model.startswith("gpt-"):
        keys.append("OPENAI_API_KEY")
    elif model.startswith("openrouter/"):
        keys.append("OPENROUTER_API_KEY")
    elif model.startswith("deepseek-"):
        keys.append("DEEPSEEK_API_KEY")
    keys.append("WANDB_API_KEY")
    return keys


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--image", help="Existing immutable sha256:<image-id>")
    parser.add_argument("--proposal-model", default=DEFAULT_MODEL)
    parser.add_argument("--generations", type=int, default=150)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=tuple(POLICY_TO_ARM),
        default=list(POLICY_TO_ARM),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=ROOT / "results" / f"pr176-{UPSTREAM_BASE[:7]}",
    )
    args = parser.parse_args()

    if args.generations < 2:
        parser.error("--generations must be at least 2")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if len(set(args.policies)) != len(args.policies):
        parser.error("--policies cannot contain duplicates")
    if args.image and not IMAGE_ID_RE.fullmatch(args.image):
        parser.error("--image must be an immutable local sha256:<image-id>")

    tracked_changes = _git("status", "--short", "--untracked-files=no")
    if args.execute and tracked_changes:
        parser.error("Refusing to execute from a dirty tracked working tree")
    if args.execute:
        # The campaign is normally launched from this checkout with secrets in
        # its ignored .env. Existing process variables retain precedence.
        load_dotenv(dotenv_path=ROOT / ".env", override=False)
        missing = [
            key for key in _required_keys(args.proposal_model) if not os.getenv(key)
        ]
        if missing:
            parser.error(
                "Missing required environment variables: " + ", ".join(missing)
            )

    image = args.image or ("<built-image-id>" if not args.execute else _build_image())
    schedule: list[dict[str, object]] = []
    commands: list[tuple[Path, list[str]]] = []
    results_root = args.results_root.resolve()

    for repeat in range(1, args.repeats + 1):
        seed = _seed_for_repeat(repeat)
        order = _run_order(repeat, args.policies)
        schedule.append({"repeat": repeat, "seed": seed, "order": order})
        for policy in order:
            results_dir, argv = _command(
                policy=policy,
                repeat=repeat,
                seed=seed,
                model=args.proposal_model,
                image=image,
                generations=args.generations,
                results_root=results_root,
            )
            commands.append((results_dir, argv))

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "upstream_base": UPSTREAM_BASE,
        "benchmark_commit": _git("rev-parse", "HEAD"),
        "tracked_working_tree_clean": not bool(tracked_changes),
        "benchmark_files_sha256": {
            path.name: _sha256(path)
            for path in (
                CONFIG_PATH,
                TASK_DIR / "initial.py",
                TASK_DIR / "evaluate.py",
                TASK_DIR / "Dockerfile",
            )
        },
        "proposal_model": args.proposal_model,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_input_prefix": "task: sentence similarity | query:",
        "novelty_llm_enabled": False,
        "proposal_reasoning_effort": "medium",
        "proposal_temperature": 1.0,
        "wandb": {
            "enabled": True,
            "project": WANDB_PROJECT,
            "group": results_root.name,
        },
        "image": image,
        "generations_per_run": args.generations,
        "schedule": schedule,
    }

    if not args.execute:
        print(json.dumps(manifest, indent=2))
        print("\nCommands (dry run):")
        for _, argv in commands:
            print(shlex.join(argv))
        return 0

    existing = [str(path) for path, _ in commands if path.exists()]
    if existing:
        parser.error(
            "Refusing to resume or overwrite existing runs: " + ", ".join(existing)
        )

    results_root.mkdir(parents=True, exist_ok=True)
    (results_root / "campaign_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for results_dir, argv in commands:
        print(f"\nRunning {results_dir.name}", flush=True)
        _run(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
