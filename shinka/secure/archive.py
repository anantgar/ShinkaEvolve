"""Deterministic tree snapshots and hostile archive extraction."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .canonical import DIGEST_PREFIX
from .errors import SnapshotError

DEFAULT_EXCLUDES = (
    ".git",
    ".git/**",
    ".shinka",
    ".shinka/**",
    "__pycache__",
    "**/__pycache__",
    "**/__pycache__/**",
)


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 20_000
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 512 * 1024 * 1024
    max_path_bytes: int = 4096

    def __post_init__(self) -> None:
        if (
            min(
                self.max_entries,
                self.max_total_bytes,
                self.max_file_bytes,
                self.max_path_bytes,
            )
            <= 0
        ):
            raise SnapshotError("Archive limits must be positive")


@dataclass(frozen=True)
class SnapshotMetadata:
    digest: str
    archive_size: int
    entry_count: int
    content_bytes: int
    source_commit: str | None = None
    source_dirty: bool | None = None


@dataclass(frozen=True)
class _Entry:
    relative_path: str
    source_path: Path
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    inode: int
    device: int
    link_target: str | None = None

    @property
    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.relative_path,
            self.mode,
            self.size,
            self.mtime_ns,
            self.ctime_ns,
            self.inode,
            self.device,
            self.link_target,
        )


def _matches(path: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip("/")
        if not normalized:
            continue
        if fnmatch.fnmatchcase(path, normalized):
            return True
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def _included(path: str, includes: Sequence[str] | None) -> bool:
    if not includes:
        return True
    for pattern in includes:
        normalized = pattern.replace("\\", "/").strip("/")
        if not normalized:
            continue
        if (
            fnmatch.fnmatchcase(path, normalized)
            or path == normalized
            or path.startswith(f"{normalized}/")
            or normalized.startswith(f"{path}/")
        ):
            return True
    return False


def _validate_member_path(name: str, *, limits: ArchiveLimits) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise SnapshotError("Archive contains an invalid path")
    if len(name.encode("utf-8")) > limits.max_path_bytes:
        raise SnapshotError("Archive path exceeds the configured size limit")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotError("Archive path is absolute or traverses its extraction root")
    if any(part.casefold() in {".git", ".shinka"} for part in path.parts):
        raise SnapshotError("Archive contains a protected control directory")
    return path.as_posix()


def _validate_link_target(member_name: str, target: str) -> str:
    if not target or "\x00" in target or "\\" in target:
        raise SnapshotError(f"Unsafe symlink target in {member_name}")
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        raise SnapshotError(f"Absolute symlink target in {member_name}")
    parts: list[str] = list(PurePosixPath(member_name).parent.parts)
    for part in target_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise SnapshotError(f"Symlink escapes snapshot root: {member_name}")
            parts.pop()
        else:
            parts.append(part)
    if any(part.casefold() in {".git", ".shinka"} for part in parts):
        raise SnapshotError(f"Symlink targets a protected control path: {member_name}")
    return target


def _scan_tree(
    root: Path,
    *,
    excludes: Sequence[str],
    includes: Sequence[str] | None,
    limits: ArchiveLimits,
) -> list[_Entry]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise SnapshotError(
            "Snapshot source cannot be read", private_diagnostic=str(exc)
        ) from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        raise SnapshotError("Snapshot source must be a real directory")

    entries: list[_Entry] = []
    content_bytes = 0

    def visit(directory: Path, relative_dir: PurePosixPath) -> None:
        nonlocal content_bytes
        try:
            children = sorted(
                os.scandir(directory), key=lambda item: item.name.encode("utf-8")
            )
        except OSError as exc:
            raise SnapshotError(
                "Snapshot source changed or became unreadable",
                private_diagnostic=str(exc),
            ) from exc
        for child in children:
            relative = (relative_dir / child.name).as_posix()
            if _matches(relative, excludes):
                continue
            _validate_member_path(relative, limits=limits)
            if not _included(relative, includes):
                continue
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotError(
                    "Snapshot source changed during traversal",
                    private_diagnostic=str(exc),
                ) from exc
            mode = child_stat.st_mode
            target: str | None = None
            if stat.S_ISLNK(mode):
                target = os.readlink(child.path)
                _validate_link_target(relative, target)
            elif stat.S_ISREG(mode):
                if child_stat.st_nlink != 1:
                    raise SnapshotError(f"Hard-linked file is not allowed: {relative}")
                if child_stat.st_size > limits.max_file_bytes:
                    raise SnapshotError(f"File exceeds snapshot limit: {relative}")
                content_bytes += child_stat.st_size
                if content_bytes > limits.max_total_bytes:
                    raise SnapshotError("Snapshot exceeds its total content size limit")
            elif not stat.S_ISDIR(mode):
                raise SnapshotError(
                    f"Special filesystem entry is not allowed: {relative}"
                )

            entries.append(
                _Entry(
                    relative_path=relative,
                    source_path=Path(child.path),
                    mode=mode,
                    size=child_stat.st_size,
                    mtime_ns=child_stat.st_mtime_ns,
                    ctime_ns=child_stat.st_ctime_ns,
                    inode=child_stat.st_ino,
                    device=child_stat.st_dev,
                    link_target=target,
                )
            )
            if len(entries) > limits.max_entries:
                raise SnapshotError("Snapshot exceeds its entry count limit")
            if stat.S_ISDIR(mode):
                visit(Path(child.path), PurePosixPath(relative))

    visit(root, PurePosixPath())
    return entries


class _VerifiedFile(io.RawIOBase):
    """File object that proves a scanned file did not change while read."""

    def __init__(self, entry: _Entry) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._fd = os.open(entry.source_path, flags)
        except OSError as exc:
            raise SnapshotError(
                f"Could not open snapshot file: {entry.relative_path}",
                private_diagnostic=str(exc),
            ) from exc
        current = os.fstat(self._fd)
        current_identity = (
            current.st_mode,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
            current.st_ino,
            current.st_dev,
        )
        expected_identity = (
            entry.mode,
            entry.size,
            entry.mtime_ns,
            entry.ctime_ns,
            entry.inode,
            entry.device,
        )
        if current_identity != expected_identity:
            os.close(self._fd)
            raise SnapshotError("Snapshot source changed before it could be copied")
        self._entry = entry
        self._read_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        data = os.read(self._fd, len(buffer))
        size = len(data)
        buffer[:size] = data
        self._read_bytes += size
        return size

    def close(self) -> None:
        if not self.closed:
            try:
                current = os.fstat(self._fd)
                if self._read_bytes != self._entry.size or (
                    current.st_size != self._entry.size
                    or current.st_mtime_ns != self._entry.mtime_ns
                    or current.st_ctime_ns != self._entry.ctime_ns
                ):
                    raise SnapshotError("Snapshot source changed while it was copied")
            finally:
                os.close(self._fd)
        super().close()


def _tar_info(entry: _Entry) -> tarfile.TarInfo:
    info = tarfile.TarInfo(entry.relative_path)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if stat.S_ISDIR(entry.mode):
        info.type = tarfile.DIRTYPE
        info.size = 0
        info.mode = 0o755
    elif stat.S_ISLNK(entry.mode):
        info.type = tarfile.SYMTYPE
        info.size = 0
        info.linkname = entry.link_target or ""
        info.mode = 0o777
    else:
        info.type = tarfile.REGTYPE
        info.size = entry.size
        # Host umasks and rootless UID mappings vary. Executability is the only
        # permission bit that is meaningful candidate content.
        info.mode = 0o755 if stat.S_IMODE(entry.mode) & 0o111 else 0o644
    return info


def _git_provenance(source: Path) -> tuple[str | None, bool | None]:
    """Read a diagnostic HEAD without executing Git against an untrusted tree."""

    git_dir = source / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        return None, None
    try:
        head_path = git_dir / "HEAD"
        if head_path.is_symlink() or head_path.stat().st_size > 4096:
            return None, None
        head = head_path.read_text(encoding="ascii").strip()
        commit = head
        if head.startswith("ref: "):
            relative = head[5:]
            pure = PurePosixPath(relative)
            if (
                not relative.startswith("refs/")
                or pure.is_absolute()
                or ".." in pure.parts
            ):
                return None, None
            ref_path = git_dir / pure.as_posix()
            if (
                not ref_path.is_file()
                or ref_path.is_symlink()
                or ref_path.stat().st_size > 256
            ):
                return None, None
            commit = ref_path.read_text(encoding="ascii").strip()
        if not all(character in "0123456789abcdef" for character in commit) or len(
            commit
        ) not in {40, 64}:
            return None, None
        # Dirty state is deliberately unknown: computing it with Git would make
        # repository-controlled Git configuration part of the trusted host path.
        return commit, None
    except (OSError, UnicodeError):
        return None, None


def create_normalized_archive(
    source: Path | str,
    destination: Path | str,
    *,
    excludes: Sequence[str] = DEFAULT_EXCLUDES,
    includes: Sequence[str] | None = None,
    limits: ArchiveLimits = ArchiveLimits(),
    retries: int = 2,
) -> SnapshotMetadata:
    """Snapshot a tree into a deterministic uncompressed PAX tar archive."""

    source_path = Path(source).resolve()
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: SnapshotError | None = None

    for _attempt in range(max(1, retries + 1)):
        temporary = destination_path.with_name(
            f".{destination_path.name}.{next(tempfile._get_candidate_names())}.tmp"
        )
        try:
            before = _scan_tree(
                source_path,
                excludes=tuple(excludes),
                includes=includes,
                limits=limits,
            )
            with temporary.open("xb") as raw:
                with tarfile.open(
                    fileobj=raw, mode="w", format=tarfile.PAX_FORMAT
                ) as archive:
                    for entry in before:
                        info = _tar_info(entry)
                        if info.isreg():
                            with _VerifiedFile(entry) as verified:
                                archive.addfile(info, verified)
                        else:
                            archive.addfile(info)
                raw.flush()
                os.fsync(raw.fileno())

            after = _scan_tree(
                source_path,
                excludes=tuple(excludes),
                includes=includes,
                limits=limits,
            )
            if [entry.fingerprint for entry in before] != [
                entry.fingerprint for entry in after
            ]:
                raise SnapshotError("Snapshot source changed while being copied")

            digest = hashlib.sha256()
            archive_size = 0
            with temporary.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    archive_size += len(chunk)
            os.replace(temporary, destination_path)
            commit, dirty = _git_provenance(source_path)
            return SnapshotMetadata(
                digest=f"{DIGEST_PREFIX}{digest.hexdigest()}",
                archive_size=archive_size,
                entry_count=len(before),
                content_bytes=sum(
                    entry.size for entry in before if stat.S_ISREG(entry.mode)
                ),
                source_commit=commit,
                source_dirty=dirty,
            )
        except SnapshotError as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
        except (OSError, tarfile.TarError) as exc:
            temporary.unlink(missing_ok=True)
            last_error = SnapshotError(
                "Could not create normalized snapshot",
                private_diagnostic=str(exc),
            )

    assert last_error is not None
    raise last_error


def _validated_members(
    archive: tarfile.TarFile,
    *,
    limits: ArchiveLimits,
) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    folded_names: set[str] = set()
    symlinks: set[str] = set()
    entry_types: dict[str, str] = {}
    total_bytes = 0

    for member in archive:
        name = _validate_member_path(member.name, limits=limits)
        if name in names or name.casefold() in folded_names:
            raise SnapshotError(f"Archive path collision: {name}")
        parent = PurePosixPath(name).parent
        ancestors = [parent, *parent.parents]
        for ancestor in ancestors:
            ancestor_name = ancestor.as_posix()
            if ancestor_name in symlinks:
                raise SnapshotError(f"Archive member is nested under a symlink: {name}")
            if ancestor_name not in {"", "."} and entry_types.get(
                ancestor_name
            ) not in {
                None,
                "directory",
            }:
                raise SnapshotError(f"Archive member is nested under a file: {name}")
        for existing in names:
            if existing.startswith(f"{name}/") and not member.isdir():
                raise SnapshotError(f"Archive file/directory collision: {name}")

        if member.islnk() or member.isdev() or member.isfifo():
            raise SnapshotError(f"Unsupported archive entry type: {name}")
        if not (member.isfile() or member.isdir() or member.issym()):
            raise SnapshotError(f"Unsupported archive entry type: {name}")
        if member.isfile():
            if member.size < 0 or member.size > limits.max_file_bytes:
                raise SnapshotError(f"Archive file exceeds size limit: {name}")
            total_bytes += member.size
            if total_bytes > limits.max_total_bytes:
                raise SnapshotError("Archive exceeds total extraction size limit")
        if member.issym():
            _validate_link_target(name, member.linkname)
            symlinks.add(name)

        member.name = name
        names.add(name)
        folded_names.add(name.casefold())
        entry_types[name] = (
            "directory" if member.isdir() else "symlink" if member.issym() else "file"
        )
        members.append(member)
        if len(members) > limits.max_entries:
            raise SnapshotError("Archive exceeds entry count limit")

    return members


def validate_archive(
    archive_path: Path | str,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> tuple[int, int]:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = _validated_members(archive, limits=limits)
            return len(members), sum(
                member.size for member in members if member.isfile()
            )
    except SnapshotError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise SnapshotError(
            "Invalid snapshot archive", private_diagnostic=str(exc)
        ) from exc


def _ensure_real_parents(root: Path, destination: Path) -> None:
    relative = destination.relative_to(root)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise SnapshotError("Archive extraction encountered a symlink parent")
        current.mkdir(mode=0o700, exist_ok=True)


def extract_normalized_archive(
    archive_path: Path | str,
    destination: Path | str,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> SnapshotMetadata:
    """Extract a validated archive without ``tarfile.extractall``."""

    archive_path = Path(archive_path)
    destination_path = Path(destination)
    if destination_path.is_symlink():
        raise SnapshotError("Snapshot extraction destination cannot be a symlink")
    if destination_path.exists():
        if not destination_path.is_dir() or any(destination_path.iterdir()):
            raise SnapshotError(
                "Snapshot extraction destination must be an empty directory"
            )
    destination_path.mkdir(parents=True, mode=0o700, exist_ok=True)

    digest = hashlib.sha256()
    archive_size = 0
    with archive_path.open("rb") as raw:
        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
            digest.update(chunk)
            archive_size += len(chunk)

    directory_modes: list[tuple[Path, int]] = []
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = _validated_members(archive, limits=limits)
            for member in sorted(
                (item for item in members if item.isdir()),
                key=lambda item: len(PurePosixPath(item.name).parts),
            ):
                target = destination_path / member.name
                _ensure_real_parents(destination_path, target)
                target.mkdir(mode=0o700, exist_ok=True)
                directory_modes.append((target, member.mode & 0o777))
            for member in (item for item in members if not item.isdir()):
                target = destination_path / member.name
                _ensure_real_parents(destination_path, target)
                mode = member.mode & 0o777
                if member.issym():
                    os.symlink(member.linkname, target)
                else:
                    source = archive.extractfile(member)
                    if source is None:
                        raise SnapshotError(
                            f"Archive file has no content: {member.name}"
                        )
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    fd = os.open(target, flags, 0o600)
                    written = 0
                    try:
                        with os.fdopen(fd, "wb", closefd=True) as output:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                written += len(chunk)
                                if written > member.size:
                                    raise SnapshotError(
                                        f"Archive file grew while extracting: {member.name}"
                                    )
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                    finally:
                        source.close()
                    if written != member.size:
                        raise SnapshotError(f"Archive file is truncated: {member.name}")
                    os.chmod(target, mode)
            for directory, mode in reversed(directory_modes):
                os.chmod(directory, mode)
    except Exception:
        shutil.rmtree(destination_path, ignore_errors=True)
        raise

    return SnapshotMetadata(
        digest=f"{DIGEST_PREFIX}{digest.hexdigest()}",
        archive_size=archive_size,
        entry_count=len(members),
        content_bytes=sum(member.size for member in members if member.isfile()),
    )
