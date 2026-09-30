"""Construct the agent's source view without tests, fixtures or Git history."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

# These upstream files contain benchmarking/testing code and are needed to link
# the original engine. Restore them only inside the private build copy.
PRIVATE_BUILD_FILES = (
    "src/benchmark.cpp",
    "src/benchmark.h",
    "src/perft.h",
)


def prepare_seed(source: Path, destination: Path, build_context: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "src", destination / "src")
    hidden = {}
    for relative in PRIVATE_BUILD_FILES:
        path = destination / relative
        hidden[relative] = path.read_text()
        path.unlink()
    (destination / "scripts").mkdir()
    for name in ("net.sh", "get_native_properties.sh"):
        shutil.copyfile(source / "scripts" / name, destination / "scripts" / name)
    for name in ("AUTHORS", "Copying.txt", "README.md", ".clang-format"):
        shutil.copyfile(source / name, destination / name)
    build_context.write_text(json.dumps(hidden) + "\n")


def restore_build_context(root: Path, path: Path) -> None:
    hidden = json.loads(path.read_text())
    if set(hidden) != set(PRIVATE_BUILD_FILES):
        raise ValueError("Unexpected private build context")
    for relative, content in hidden.items():
        target = root / relative
        if target.exists() or target.is_symlink() or not isinstance(content, str):
            raise ValueError("Private build context overlaps candidate files")
        target.write_text(content)
