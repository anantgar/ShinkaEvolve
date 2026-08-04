"""Trusted read-only caches for durable Headless agent sessions.

Durable proposal homes must keep provider conversation state private, but some
agent assets are static and expensive to copy into every home.  This module
describes only caches that are safe to share read-only.  It never promotes
files from an agent session automatically: the shared root must be seeded by
an operator from a trusted source before it is mounted into an untrusted
agent container.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from .errors import SecurityPolicyError

# These paths contain static assets in the current secure Headless images and
# results.  Conversation databases, transcripts, brains, logs, and provider
# configuration remain in each proposal home.
AGENT_SHARED_CACHE_PATHS: dict[str, tuple[str, ...]] = {
    "codex": (
        ".codex/plugins/cache",
        ".codex/cache/codex_apps_tools",
        ".codex/cache/codex_apps_server_info",
    ),
    # Cursor's plugin cache contains versioned, immutable plugin assets; keep
    # project transcripts, snapshots, extensions, and application databases
    # private.
    "cursor": (".cursor/plugins/cache",),
    # Gemini state is currently account/session/conversation state.  Do not
    # share the .gemini tree by default.
    "gemini": (),
    # Antigravity's installed helper binaries are static; its brain,
    # conversations, logs, and settings remain private.
    "antigravity": (".gemini/antigravity-cli/bin",),
}


@dataclass(frozen=True)
class SharedCacheMount:
    """A read-only cache source and its path inside the agent home."""

    relative_path: str
    source: Path

    @property
    def target(self) -> str:
        return f"/headless-home/{self.relative_path}"


def shared_cache_paths(agent: str) -> tuple[str, ...]:
    """Return the conservative static-cache policy for an agent."""

    return AGENT_SHARED_CACHE_PATHS.get(agent, ())


def _validate_relative_path(relative_path: str) -> PurePosixPath:
    parsed = PurePosixPath(relative_path)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise SecurityPolicyError("Shared cache paths must be safe relative paths")
    return parsed


def _validate_tree(root: Path) -> None:
    """Reject links and special files before a tree becomes shared."""

    if root.is_symlink() or not root.is_dir():
        raise SecurityPolicyError("Shared cache source must be a real directory")
    for path in (root, *root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise SecurityPolicyError("Shared cache source cannot contain symlinks")
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise SecurityPolicyError("Shared cache source contains a special file")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class SharedSessionCacheStore:
    """Manage one trusted, image-specific shared cache namespace."""

    def __init__(self, root: Path | str, *, agent: str, image: str) -> None:
        unresolved_root = Path(root).expanduser()
        if unresolved_root.is_symlink():
            raise SecurityPolicyError("Shared cache root cannot be a symlink")
        if unresolved_root.exists():
            if not unresolved_root.is_dir():
                raise SecurityPolicyError(
                    "Shared cache root must be a real directory"
                )
            if stat.S_IMODE(unresolved_root.stat().st_mode) & 0o077:
                raise SecurityPolicyError(
                    "Shared cache root must not be accessible by other users"
                )
        else:
            unresolved_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = unresolved_root.resolve()
        self.agent = agent
        self.image_namespace = hashlib.sha256(image.encode("utf-8")).hexdigest()[:24]
        self.namespace_root = self.root / agent / self.image_namespace
        self.lock_path = self.root / ".lock"

    def _source(self, relative_path: str) -> Path:
        parsed = _validate_relative_path(relative_path)
        return self.namespace_root.joinpath(*parsed.parts)

    @staticmethod
    def _session_path(session_home: Path, relative_path: str) -> Path:
        parsed = _validate_relative_path(relative_path)
        current = session_home
        for part in parsed.parts:
            current = current / part
            if current.is_symlink():
                raise SecurityPolicyError(
                    "Durable session cache path cannot contain symlinks"
                )
        return current

    def mounts(self) -> tuple[SharedCacheMount, ...]:
        """Return only fully existing, validated shared cache directories."""

        mounts: list[SharedCacheMount] = []
        for relative_path in shared_cache_paths(self.agent):
            source = self._source(relative_path)
            if not source.exists():
                continue
            _validate_tree(source)
            mounts.append(SharedCacheMount(relative_path, source))
        return tuple(mounts)

    def seed_from_trusted_home(self, session_home: Path | str) -> tuple[str, ...]:
        """Copy static assets from an operator-trusted home atomically.

        Runtime agent homes must never call this method.  It is intended for a
        one-time migration or a cache prepared outside an untrusted agent.
        Existing namespaces are immutable so a later session cannot replace
        the contents used by other sessions.
        """

        unresolved_home = Path(session_home).expanduser()
        if unresolved_home.is_symlink() or not unresolved_home.is_dir():
            raise SecurityPolicyError("Trusted session home must be a real directory")
        trusted_home = unresolved_home.resolve()
        seeded: list[str] = []
        with _exclusive_lock(self.lock_path):
            for relative_path in shared_cache_paths(self.agent):
                source = self._session_path(trusted_home, relative_path)
                if not source.exists():
                    continue
                _validate_tree(source)
                target = self._source(relative_path)
                if target.exists():
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
                )
                shutil.rmtree(temporary)
                try:
                    shutil.copytree(source, temporary, copy_function=shutil.copy2)
                    os.replace(temporary, target)
                    seeded.append(relative_path)
                except Exception:
                    shutil.rmtree(temporary, ignore_errors=True)
                    raise
        return tuple(seeded)

    def prune_session_home(self, session_home: Path | str) -> tuple[str, ...]:
        """Clear only paths currently backed by the shared read-only store."""

        unresolved_home = Path(session_home).expanduser()
        if unresolved_home.is_symlink() or not unresolved_home.is_dir():
            raise SecurityPolicyError("Session home must be a real directory")
        home = unresolved_home.resolve()
        removed: list[str] = []
        for mount in self.mounts():
            path = self._session_path(home, mount.relative_path)
            if path.is_symlink():
                raise SecurityPolicyError(
                    "Durable session cache path cannot contain symlinks"
                )
            if path.exists() and not path.is_dir():
                raise SecurityPolicyError(
                    "Durable session cache path must be a directory"
                )
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            for child in path.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    raise SecurityPolicyError(
                        "Durable session cache contains a special filesystem entry"
                    )
            removed.append(mount.relative_path)
        return tuple(removed)
