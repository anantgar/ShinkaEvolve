import importlib.util
import stat
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "headless_novelty_compare.py"
SPEC = importlib.util.spec_from_file_location("headless_novelty_compare", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_copy_snapshot_scrubs_metadata_and_ignores_control_directories(tmp_path: Path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / ".git").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / ".shinka").mkdir()
    (source / "solution.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".git" / "HEAD").write_text("secret\n", encoding="utf-8")
    (source / "__pycache__" / "solution.pyc").write_bytes(b"cache")
    (source / ".shinka" / "individual.md").write_text(
        "Commit: sha256:0123456789abcdef\nCore idea: catalog layout\n",
        encoding="utf-8",
    )

    MODULE._copy_snapshot(source, destination)

    assert (destination / "solution.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (destination / ".git").exists()
    assert not (destination / "__pycache__").exists()
    summary = (destination / ".shinka" / "individual.md").read_text(
        encoding="utf-8"
    )
    assert "0123456789abcdef" not in summary
    assert "catalog layout" in summary


def test_copy_snapshot_rejects_internal_symlinks(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be copied\n", encoding="utf-8")
    (source / "escape.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        MODULE._copy_snapshot(source, tmp_path / "destination")


def test_make_read_only_removes_write_bits(tmp_path: Path):
    root = tmp_path / "comparison"
    nested = root / "existing"
    nested.mkdir(parents=True)
    file_path = nested / "solution.py"
    file_path.write_text("VALUE = 1\n", encoding="utf-8")

    MODULE._make_read_only(root)

    assert not stat.S_IMODE(root.stat().st_mode) & 0o222
    assert not stat.S_IMODE(nested.stat().st_mode) & 0o222
    assert not stat.S_IMODE(file_path.stat().st_mode) & 0o222


def test_parse_args_accepts_codex_agent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT_PATH),
            "existing",
            "proposed",
            "--agent",
            "codex",
            "--model",
            "gpt-5.6-luna",
            "--reasoning-effort",
            "xhigh",
        ],
    )

    args = MODULE._parse_args()

    assert args.agent == "codex"
    assert args.model == "gpt-5.6-luna"
    assert args.reasoning_effort == "xhigh"
