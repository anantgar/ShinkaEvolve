---
name: shinka-setup
description: Create a new repo-backed ShinkaEvolve task from a task description, including a candidate `seed_repo/`, an external `evaluate.py` that accepts `--repo_path`, and optional run configuration.
---

# Shinka repo task setup

Create a new ShinkaEvolve task from a natural-language objective. Each evolved
individual is a repository snapshot, not a source string or one
`initial.<ext>` file.

## When to use

Use this skill when the user wants a new Shinka optimization task and no
repo-mode task exists yet. For an existing codebase, use `shinka-convert`.

## Inputs

Infer what is safe to infer, and ask only for missing choices that materially
change the task:

- objective and success criteria;
- required language, runtime, dependencies, and assets;
- correctness constraints;
- primary score and why higher is better;
- evaluation seeds, repetitions, time, and memory limits;
- intended mutation scope, if narrower than the whole candidate repository.

## Task contract

Create this shape in the requested task directory:

```text
task/
  evaluate.py
  seed_repo/
    ...candidate files...
  shinka.yaml       # optional
  run_evo.py        # optional
```

Requirements:

- `seed_repo/` is a runnable candidate directory. It may be a plain directory;
  Shinka initializes and commits its Git baseline when evolution starts.
- If `seed_repo/` is already an independent Git repository with a `HEAD`, its
  working tree must be clean.
- `evaluate.py` stays outside `seed_repo/`.
- The evaluator accepts `--repo_path` and `--results_dir`.
- It writes `metrics.json` and `correct.json` into `results_dir`.
- `metrics.json` contains numeric `combined_score`, plus `public`, `private`,
  and `text_feedback` fields where applicable.
- `correct.json` contains boolean `correct` and string `error`.
- Higher `combined_score` is always better.
- The seed is a runnable repository. It may contain one file or many files; do
  not add EVOLVE blocks or flatten it into a synthetic single-file interface.

An empty or omitted `mutable_paths` list means the normal repository contents
are mutable. Use a non-empty allow-list only when the user explicitly wants a
narrow boundary. Shinka protects `.git` and its `.shinka` control files and
requires every coding-agent proposal to complete `.shinka/individual.md`.

## Workflow

1. Inspect the target directory and avoid overwriting existing task files
   without consent.
2. Design the candidate repository around the real runtime contract.
   Preserve useful build, test, package, and CLI structure.
3. Put scoring logic and authoritative fixtures in the task directory, outside
   `seed_repo/`.
4. Implement `evaluate.py` so it evaluates the repository passed by
   `--repo_path`, not a hardcoded source path.
5. Leave a new `seed_repo/` as a normal directory. Do not require users to
   initialize it manually; startup creates the Git baseline. Preserve an
   existing Git seed and ensure it is clean.
6. If configuration is requested, copy and tailor the bundled
   `scripts/shinka.yaml` and `scripts/run_evo.py`.
7. Validate the baseline:

```bash
smoke_dir=$(mktemp -d)
python3 evaluate.py --repo_path seed_repo --results_dir "$smoke_dir"
python3 -m json.tool "$smoke_dir/metrics.json"
python3 -m json.tool "$smoke_dir/correct.json"
```

8. Confirm the baseline is correct, deterministic enough for selection, and
   produces a finite score.
9. Hand off to `shinka-run` if the user wants to launch evolution.

## Automatic Git initialization

At evolution startup, Shinka treats a plain `seed_repo/`—including one whose
files belong to an enclosing repository—as candidate input. It initializes a
nested Git repository and creates the baseline commit using an invocation-local
identity. An independent unborn Git repository is committed the same way.
Existing repositories with a `HEAD` are preserved and must be clean.

The generated `seed_repo/.git/` metadata is runtime state and need not be stored
by a parent repository. A fresh checkout can start from the candidate files and
will be initialized again on its first run.

## Evaluator outline

Adapt this outline to the candidate runtime:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(repo_path: Path) -> tuple[dict, bool, str]:
    # Import, compile, or invoke the candidate from repo_path.
    metrics = {
        "combined_score": 0.0,
        "public": {},
        "private": {},
        "text_feedback": "",
    }
    return metrics, True, ""


def main(repo_path: str, results_dir: str) -> None:
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    metrics, correct, error = evaluate(Path(repo_path).resolve())
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (output / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(args.repo_path, args.results_dir)
```

## Safety boundary

Current mainline repo evaluation is trusted-local. Hidden or immutable paths and
git worktrees are policy controls, not confidentiality boundaries. Do not put
secrets, hidden-test answers, or adversarial private data on the same host and
assume the coding agent cannot reach them.
