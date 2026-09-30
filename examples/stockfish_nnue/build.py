"""Immutable build entrypoint, run inside the secure build container."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from policy import load_manifest, sha256, validate_source
from seed import restore_build_context


def build(
    source: Path,
    dependencies: Path,
    output: Path,
    scratch: Path,
    target: str,
    jobs: int = 2,
    sanitize: bool = False,
) -> None:
    manifest = load_manifest(dependencies / "task-manifest.json")
    expected = json.loads((dependencies / "seed-fingerprints.json").read_text())
    validate_source(source, expected, manifest["mutable_paths"])
    network = dependencies / "network.nnue"
    if sha256(network) != manifest["network_sha256"]:
        raise ValueError("Network digest mismatch")
    root = scratch / "source"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", ".shinka"))
    # Artifacts were mounted read-only; the private build copy must be writable.
    for path in [root, *root.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    restore_build_context(root, dependencies / "stockfish-build-context.json")
    here = Path(__file__).resolve().parent
    full_search = manifest["schema"] == "stockfish-inference-v3"
    if not full_search:
        shutil.copyfile(here / "harness/nnue_replay.cpp", root / "src/main.cpp")
    shutil.copyfile(network, root / "src" / manifest["network_filename"])
    settings = manifest["targets"][target]
    # Never accept compiler flags or Makefile fragments from a candidate.
    flags = "-DNNUE_EMBEDDING_OFF"
    extra = []
    if sanitize:
        flags += (
            " -fsanitize=address,undefined -fno-sanitize-recover=all"
            " -fno-omit-frame-pointer"
        )
        extra = ["optimize=no", "debug=yes", "LDFLAGS=-fsanitize=address,undefined"]
    command = [
        "make",
        f"-j{max(1, min(jobs, 32))}",
        "build",
        f"ARCH={settings['arch']}",
        f"COMP={settings['compiler']}",
        f"EXTRACXXFLAGS={flags}",
        *extra,
    ]
    build_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(scratch),
        "LANG": "C",
        "LC_ALL": "C",
    }
    subprocess.run(
        command,
        cwd=root / "src",
        check=True,
        timeout=1800,
        env=build_env,
    )
    output.mkdir(parents=True, exist_ok=True)
    if full_search:
        shutil.copyfile(root / "src/stockfish", output / "stockfish")
        (output / "stockfish").chmod(0o755)
        # Both executables link the same NNUE object files. Only the trusted
        # entrypoint changes for the independent exact-output checks.
        shutil.copyfile(here / "harness/nnue_replay.cpp", root / "src/main.cpp")
        (root / "src/main.o").unlink()
        (root / "src/stockfish").unlink()
        subprocess.run(
            command,
            cwd=root / "src",
            check=True,
            timeout=1800,
            env=build_env,
        )
    shutil.copyfile(root / "src/stockfish", output / "nnue_replay")
    (output / "nnue_replay").chmod(0o755)
    if full_search:
        shutil.copyfile(here / "harness/service.py", output / "replay_service.py")
        shutil.copyfile(here / "harness/search_service.py", output / "service.py")
    else:
        shutil.copyfile(here / "harness/service.py", output / "service.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/candidate"))
    parser.add_argument(
        "--dependencies", type=Path, default=Path("/dependencies/files")
    )
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--scratch", type=Path, default=Path("/build"))
    parser.add_argument(
        "--target", choices=("graviton", "avx2", "apple"), required=True
    )
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    build(
        args.source,
        args.dependencies,
        args.output,
        args.scratch,
        args.target,
        args.jobs,
        args.sanitize,
    )


if __name__ == "__main__":
    main()
