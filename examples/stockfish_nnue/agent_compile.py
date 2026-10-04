"""Compile NNUE objects in scratch space without exposing private engine tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

TARGETS = {
    "graviton": ("armv8-dotprod", "gcc"),
    "avx2": ("x86-64-avx2", "gcc"),
    "apple": ("apple-silicon", "clang"),
    "scalar": ("general-64", "clang"),
}


def compile_nnue(source: Path, target: str, jobs: int = 2) -> None:
    arch, compiler = TARGETS[target]
    for tool in ("make", "g++" if compiler == "gcc" else "clang++"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Missing {tool}; use the NNUE AgentDockerfile")
    with tempfile.TemporaryDirectory(prefix="nnue-compile-") as temporary:
        root = Path(temporary) / "source"
        shutil.copytree(
            source,
            root,
            ignore=shutil.ignore_patterns(
                ".git", ".shinka", "*.o", "*.nnue", "stockfish"
            ),
        )
        # These objects instantiate the network layers and accumulator paths.
        # No executable, hidden benchmark, fixture or network is needed.
        objects = [
            "nnue_accumulator.o",
            "network.o",
            "nnue_misc.o",
            "half_ka_v2_hm.o",
            "full_threats.o",
            "pp_3wide.o",
        ]
        subprocess.run(
            [
                "make",
                f"-j{max(1, min(jobs, 8))}",
                f"ARCH={arch}",
                f"COMP={compiler}",
                "EXTRACXXFLAGS=-DNNUE_EMBEDDING_OFF",
                *objects,
            ],
            cwd=root / "src",
            check=True,
            timeout=600,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": temporary,
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    print(
        "NNUE objects compiled. This is a build check, not correctness or speed evidence."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    compile_nnue(args.source.resolve(), args.target, args.jobs)


if __name__ == "__main__":
    main()
