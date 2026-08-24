"""Compare two repository snapshots with a read-only Headless agent.

This is intentionally a one-shot harness for source-aware novelty checks. It
copies both inputs into an ephemeral workspace, excludes Git metadata, and
invokes the provider-native read-only mode without a session or sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


DECISION_RE = re.compile(r"^\s*(NOT_NOVEL|NOVEL|UNCERTAIN)\b", re.IGNORECASE)
IGNORED_NAMES = {".git", "__pycache__", ".mypy_cache", ".pytest_cache"}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_NAMES}


def _copy_snapshot(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise ValueError(f"Repository snapshot is not a directory: {source}")
    shutil.copytree(
        source,
        destination,
        copy_function=shutil.copy2,
        ignore=_ignore,
        symlinks=False,
    )


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if path.is_dir():
            os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        else:
            os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    mode = root.stat().st_mode
    os.chmod(root, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    state: dict[str, tuple[str, int]] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state[str(path.relative_to(root))] = (digest, path.stat().st_size)
    return state


def _prompt() -> str:
    return """You are a source-aware repository novelty judge.

The current working directory contains exactly two read-only repository
snapshots:

- existing/: the earlier individual
- proposed/: the candidate individual

Read both snapshots directly. In particular, inspect the implementation files
and each snapshot's .shinka/individual.md. The snapshots intentionally contain
no .git directory, commit history, or commit hashes. Do not use Git, infer
history, execute source code, run tests, install dependencies, or create,
modify, or delete any files. You may use read-only terminal commands such as
pwd, find, rg, sed, and head if useful.

Judge implementation-level novelty, not merely whether the prose differs. A
different algorithm, data structure, control flow, or meaningful behavior is
NOVEL. A parameter tweak, numerical continuation, formatting change, or
reworded summary with the same substantive implementation is NOT_NOVEL. Use
UNCERTAIN only when the available source is genuinely insufficient to decide.

Your first line must be exactly one of: NOVEL, NOT_NOVEL, or UNCERTAIN. Follow
it with a concise explanation naming the decisive files and implementation
details. Do not stop after describing your inspection or emit progress-only
text: you must finish with the decision and explanation in the final response.
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("existing", type=Path)
    parser.add_argument("proposed", type=Path)
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--headless-cli",
        default=os.getenv("SHINKA_HEADLESS_CLI", "npx -y @roberttlange/headless"),
    )
    parser.add_argument("--expected", choices=("NOVEL", "NOT_NOVEL", "UNCERTAIN"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    temp_parent = Path(os.getenv("SHINKA_NOVELTY_TMPDIR", "/private/tmp"))
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="shinka_headless_novelty_", dir=temp_parent
    ) as temp_dir:
        root = Path(temp_dir)
        work_dir = root / "comparison"
        work_dir.mkdir()
        _copy_snapshot(args.existing.resolve(), work_dir / "existing")
        _copy_snapshot(args.proposed.resolve(), work_dir / "proposed")
        _make_read_only(work_dir)
        before = _snapshot(work_dir)

        prompt_path = root / "prompt.md"
        prompt_path.write_text(_prompt(), encoding="utf-8")
        command = [
            *shlex.split(args.headless_cli),
            "cursor",
            "--model",
            args.model,
            "--allow",
            "read-only",
            "--prompt-file",
            str(prompt_path),
            "--work-dir",
            str(work_dir),
            "--timeout",
            str(args.timeout),
            "--usage",
        ]

        completed = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=args.timeout + 120,
        )
        after = _snapshot(work_dir)
        changed = sorted(
            set(before) | set(after),
            key=str,
        )
        changed = [path for path in changed if before.get(path) != after.get(path)]

        print(f"COMMAND: {' '.join(command)}")
        print(f"EXIT_CODE: {completed.returncode}")
        print("--- STDOUT ---")
        print(completed.stdout.rstrip())
        if completed.stderr.strip():
            print("--- STDERR ---")
            print(completed.stderr.rstrip())
        print("--- WRITE CHECK ---")
        print("UNCHANGED" if not changed else "CHANGED: " + ", ".join(changed))

        if completed.returncode != 0 or changed:
            return 1

        decision = None
        for line in completed.stdout.splitlines():
            match = DECISION_RE.match(line)
            if match:
                decision = match.group(1).upper()
                break
        print(f"DECISION: {decision or 'UNPARSEABLE'}")
        if decision is None:
            return 1
        if args.expected and decision != args.expected:
            print(f"EXPECTED: {args.expected}")
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(main())
