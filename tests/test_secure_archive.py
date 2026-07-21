from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from shinka.secure.archive import (
    create_normalized_archive,
    extract_normalized_archive,
)
from shinka.secure.errors import SnapshotError


def test_normalized_archive_is_deterministic_and_excludes_git(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "b.txt").write_text("b", encoding="utf-8")
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "secret").write_text("not exported", encoding="utf-8")
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"

    first_meta = create_normalized_archive(source, first)
    os.utime(source / "a.txt", (2_000_000_000, 2_000_000_000))
    second_meta = create_normalized_archive(source, second)

    assert first_meta.digest == second_meta.digest
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        assert archive.getnames() == ["a.txt", "b.txt"]


def test_normalized_archive_preserves_only_executable_mode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    regular = source / "regular.txt"
    executable = source / "tool.sh"
    regular.write_text("regular", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    regular.chmod(0o600)
    executable.chmod(0o700)

    first = tmp_path / "first.tar"
    first_meta = create_normalized_archive(source, first)
    regular.chmod(0o666)
    executable.chmod(0o777)
    second = tmp_path / "second.tar"
    second_meta = create_normalized_archive(source, second)

    assert first_meta.digest == second_meta.digest
    with tarfile.open(first) as archive:
        assert archive.getmember("regular.txt").mode == 0o644
        assert archive.getmember("tool.sh").mode == 0o755


@pytest.mark.parametrize(
    ("name", "kind", "target"),
    [
        ("../escape", "file", None),
        ("/absolute", "file", None),
        ("link", "symlink", "../outside"),
        ("link", "symlink", ".git/config"),
        ("device", "device", None),
    ],
)
def test_hostile_archive_entries_are_rejected(
    tmp_path: Path, name: str, kind: str, target: str | None
) -> None:
    archive_path = tmp_path / "hostile.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = target or ""
            archive.addfile(member)
        elif kind == "device":
            member.type = tarfile.CHRTYPE
            archive.addfile(member)
        else:
            payload = b"x"
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(SnapshotError):
        extract_normalized_archive(archive_path, tmp_path / "output")
    assert not (tmp_path / "escape").exists()


def test_archive_rejects_casefold_and_prefix_collisions(tmp_path: Path) -> None:
    archive_path = tmp_path / "collision.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name in ("Case", "case"):
            member = tarfile.TarInfo(name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(SnapshotError, match="collision"):
        extract_normalized_archive(archive_path, tmp_path / "output")


def test_snapshot_rejects_hardlinks_and_special_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "original"
    original.write_bytes(b"x")
    os.link(original, source / "alias")
    with pytest.raises(SnapshotError, match="Hard-linked"):
        create_normalized_archive(source, tmp_path / "output.tar")
