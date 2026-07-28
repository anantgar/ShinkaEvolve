"""Disposable candidate-only synthetic Git repositories.

Git is retained only for agent ergonomics and diagnostics. Immutable candidate
archives in the content-addressed store are the execution identity.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from shinka.secure.archive import (
    ArchiveLimits,
    DEFAULT_EXCLUDES,
    create_normalized_archive,
    validate_archive,
)
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.contracts import ArtifactRef
from shinka.secure.errors import SecurityPolicyError
from shinka.secure.mutation import initialize_synthetic_repository


class MutabilityViolation(ValueError):
    """Raised when a candidate artifact violates its public path policy."""


@dataclass
class RepoWorktree:
    individual_id: str
    generation: int
    branch_name: str
    path: Path
    parent_commit: str
    parent_digest: str
    policy_fingerprints: dict[str, str] = field(default_factory=dict)
    omitted_paths: list[str] = field(default_factory=list)


@dataclass
class WorktreeSnapshot:
    commit_sha: Optional[str]
    parent_commit: str
    diff: str
    changed_files: list[str] = field(default_factory=list)
    status: str = ""
    diff_stat: str = ""
    candidate_digest: Optional[str] = None
    artifact_uri: Optional[str] = None


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _validated_patterns(patterns: Iterable[str], *, label: str) -> list[str]:
    validated: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            raise SecurityPolicyError(f"{label} entries must be strings")
        raw = pattern.replace("\\", "/")
        normalized = raw.strip("/")
        if not normalized:
            continue
        path = PurePosixPath(normalized)
        if (
            "\x00" in raw
            or raw.startswith("/")
            or path.is_absolute()
            or any(part in {".", ".."} for part in path.parts)
        ):
            raise SecurityPolicyError(
                f"{label} entries must stay within the candidate root: {pattern!r}"
            )
        validated.append(normalized)
    return validated


def _matches(path: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize(path)
    for pattern in patterns:
        candidate = _normalize(pattern)
        if not candidate:
            continue
        if (
            normalized == candidate
            or normalized.startswith(f"{candidate}/")
            or fnmatch.fnmatchcase(normalized, candidate)
        ):
            return True
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _copy(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        _remove(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        os.symlink(os.readlink(source), destination)
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _readonly(path: Path) -> None:
    if path.is_symlink() or not path.exists():
        return
    if path.is_dir():
        for child in path.iterdir():
            _readonly(child)
    path.chmod(path.stat().st_mode & ~0o222)


def _archive_manifest(archive_path: Path) -> dict[str, tuple[object, ...]]:
    """Read a validated archive into a non-executing comparison manifest."""

    validate_archive(archive_path)
    manifest: dict[str, tuple[object, ...]] = {}
    with tarfile.open(archive_path, mode="r:*") as archive:
        for member in archive:
            mode = member.mode & 0o777
            if member.isdir():
                manifest[member.name] = ("directory", mode)
            elif member.issym():
                manifest[member.name] = ("symlink", mode, member.linkname)
            else:
                source = archive.extractfile(member)
                if source is None:
                    raise SecurityPolicyError("Candidate archive file has no content")
                digest = hashlib.sha256()
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                manifest[member.name] = ("file", mode, member.size, digest.hexdigest())
    return manifest


class WorktreeManager:
    """Candidate artifact manager with API compatibility for the async runner."""

    LOCKFILE_NAMES = {
        "Cargo.lock",
        "Pipfile.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }

    def __init__(
        self,
        *,
        seed_repo_path: str,
        worktree_root: str,
        mutable_paths: Optional[list[str]] = None,
        immutable_paths: Optional[list[str]] = None,
        omitted_paths: Optional[list[str]] = None,
        hidden_paths: Optional[list[str]] = None,
        ignore_paths: Optional[list[str]] = None,
        base_ref: str = "HEAD",
        max_file_bytes: Optional[int] = None,
        allow_binary_files: bool = True,
        allow_deletions: bool = True,
        allow_lockfile_changes: bool = True,
        artifact_store: ContentAddressedStore | None = None,
        archive_limits: ArchiveLimits | None = None,
    ) -> None:
        if hidden_paths:
            raise SecurityPolicyError(
                "hidden_paths was removed: evaluator/private files must be outside the candidate snapshot"
            )
        if base_ref != "HEAD":
            raise SecurityPolicyError(
                "Historical Git refs cannot seed secure execution; snapshot the desired tree first"
            )
        self.seed_repo_path = Path(seed_repo_path).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.mutable_paths = _validated_patterns(
            mutable_paths or [], label="mutable_paths"
        )
        self.immutable_paths = _validated_patterns(
            immutable_paths or [], label="immutable_paths"
        )
        self.omitted_paths = _validated_patterns(
            omitted_paths or [], label="omitted_paths"
        )
        self.ignore_paths = _validated_patterns(
            ignore_paths or [".git", ".shinka"], label="ignore_paths"
        )
        self.max_file_bytes = max_file_bytes
        self.allow_binary_files = allow_binary_files
        self.allow_deletions = allow_deletions
        self.allow_lockfile_changes = allow_lockfile_changes
        state_root = self.worktree_root.parent / "candidate-artifacts"
        self.artifacts = artifact_store or ContentAddressedStore(state_root)
        self.archive_limits = archive_limits or ArchiveLimits(
            max_file_bytes=max_file_bytes or ArchiveLimits().max_file_bytes
        )
        self._references: dict[str, ArtifactRef] = {}

    def _reference(self, digest: str) -> ArtifactRef:
        if digest in self._references:
            return self._references[digest]
        path = self.artifacts.verify(digest)
        reference = ArtifactRef(
            digest=digest,
            size=path.stat().st_size,
            kind="candidate",
        )
        self._references[digest] = reference
        return reference

    def snapshot_candidate(self, worktree: RepoWorktree) -> ArtifactRef:
        reference, _metadata = self.artifacts.put_tree(
            worktree.path,
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.archive_limits,
        )
        self._references[reference.digest] = reference
        return reference

    def initialize_seed_repo(self) -> str:
        if not self.seed_repo_path.is_dir() or self.seed_repo_path.is_symlink():
            raise FileNotFoundError(
                f"Seed candidate does not exist: {self.seed_repo_path}"
            )
        self.worktree_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.worktree_root, 0o700)
        reference, _metadata = self.artifacts.put_tree(
            self.seed_repo_path,
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.archive_limits,
        )
        self._references[reference.digest] = reference
        return reference.digest

    def resolve_ref(self, ref: str) -> str:
        if ref == "HEAD":
            return self.initialize_seed_repo()
        self._reference(ref)
        return ref

    def create_child_worktree(
        self,
        *,
        parent_commit: str,
        generation: int,
        individual_id: str,
    ) -> RepoWorktree:
        short_id = individual_id.replace("-", "")[:12]
        path = self.worktree_root / f"gen_{generation}_{short_id}"
        if path.exists():
            raise FileExistsError(f"Candidate workspace already exists: {path}")
        reference = self._reference(parent_commit)
        self.artifacts.materialize_archive(reference, path, limits=self.archive_limits)
        diagnostic_parent = initialize_synthetic_repository(path)
        return RepoWorktree(
            individual_id=individual_id,
            generation=generation,
            branch_name=f"shinka/gen-{generation}-{short_id}",
            path=path,
            parent_commit=diagnostic_parent,
            parent_digest=parent_commit,
        )

    def create_agent_worktree_view(
        self,
        worktree: RepoWorktree,
        *,
        hidden_paths: Optional[list[str]] = None,
        omitted_paths: Optional[list[str]] = None,
    ) -> RepoWorktree:
        if hidden_paths:
            raise SecurityPolicyError(
                "Hidden evaluator paths are not supported; partition evaluator content at import"
            )
        agent_path = worktree.path.parent / f"{worktree.path.name}__agent"
        if agent_path.exists():
            raise FileExistsError(f"Agent candidate view already exists: {agent_path}")
        reference = self._reference(worktree.parent_digest)
        self.artifacts.materialize_archive(
            reference, agent_path, limits=self.archive_limits
        )
        requested_omissions = _validated_patterns(
            omitted_paths or [], label="omitted_paths"
        )
        view_omitted = list(dict.fromkeys([*self.omitted_paths, *requested_omissions]))
        for relative in view_omitted:
            normalized = _normalize(relative)
            if normalized and not any(char in normalized for char in "*?["):
                _remove(agent_path / normalized)
        view_reference, _metadata = self.artifacts.put_tree(
            agent_path,
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.archive_limits,
        )
        self._references[view_reference.digest] = view_reference
        diagnostic_parent = initialize_synthetic_repository(agent_path)
        agent_view = RepoWorktree(
            individual_id=worktree.individual_id,
            generation=worktree.generation,
            branch_name=f"{worktree.branch_name}-agent",
            path=agent_path,
            parent_commit=diagnostic_parent,
            parent_digest=view_reference.digest,
            omitted_paths=view_omitted,
        )
        self.freeze_immutable_paths(agent_view)
        return agent_view

    def write_policy_files(
        self,
        worktree: RepoWorktree,
        *,
        prompt_text: Optional[str] = None,
    ) -> Path:
        control = worktree.path / ".shinka"
        control.mkdir(parents=True, mode=0o700, exist_ok=True)
        (control / "mutable_paths.txt").write_text(
            "\n".join(self.mutable_paths) + ("\n" if self.mutable_paths else ""),
            encoding="utf-8",
        )
        (control / "immutable_paths.txt").write_text(
            "\n".join(self.immutable_paths) + ("\n" if self.immutable_paths else ""),
            encoding="utf-8",
        )
        prompt_path = control / "goal.md"
        if prompt_text is not None:
            prompt_path.write_text(prompt_text, encoding="utf-8")
        worktree.policy_fingerprints = {
            path.relative_to(worktree.path).as_posix(): _sha256(path)
            for path in (
                control / "mutable_paths.txt",
                control / "immutable_paths.txt",
                prompt_path,
            )
            if path.exists()
        }
        return prompt_path

    def freeze_immutable_paths(self, worktree: RepoWorktree) -> None:
        for pattern in self.immutable_paths:
            normalized = _normalize(pattern)
            if not normalized:
                continue
            paths = (
                worktree.path.glob(normalized)
                if any(char in normalized for char in "*?[")
                else [worktree.path / normalized]
            )
            for path in paths:
                if path.exists() and ".git" not in path.parts:
                    _readonly(path)

    def enforce_hidden_paths_absent(self, worktree: RepoWorktree) -> None:
        visible: list[str] = []
        for pattern in worktree.omitted_paths:
            normalized = _normalize(pattern)
            if any(char in normalized for char in "*?["):
                if any(worktree.path.glob(normalized)):
                    visible.append(pattern)
            elif (worktree.path / normalized).exists():
                visible.append(pattern)
        if visible:
            raise MutabilityViolation(
                f"Agent recreated omitted presentation paths: {visible}"
            )

    def write_parent_id(self, worktree: RepoWorktree, parent_id: Optional[str]) -> None:
        control = worktree.path / ".shinka"
        control.mkdir(parents=True, mode=0o700, exist_ok=True)
        (control / "parent_id").write_text(parent_id or "", encoding="utf-8")

    @staticmethod
    def read_parent_id(worktree_path: Path) -> Optional[str]:
        path = worktree_path / ".shinka" / "parent_id"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip() or None

    def changed_files(self, worktree_path: Path, parent_commit: str) -> list[str]:
        if not parent_commit.startswith("sha256:"):
            raise SecurityPolicyError("Candidate comparison requires a content digest")
        parent_archive = self.artifacts.verify(parent_commit)
        with tempfile.TemporaryDirectory(prefix="shinka-candidate-diff-") as temporary:
            current_archive = Path(temporary) / "candidate.tar"
            create_normalized_archive(
                worktree_path,
                current_archive,
                excludes=DEFAULT_EXCLUDES,
                limits=self.archive_limits,
            )
            parent_manifest = _archive_manifest(parent_archive)
            current_manifest = _archive_manifest(current_archive)
        paths = {
            path
            for path in set(parent_manifest) | set(current_manifest)
            if parent_manifest.get(path) != current_manifest.get(path)
        }
        paths = {
            path
            for path in paths
            if not (
                any(
                    value is not None and value[0] == "directory"
                    for value in (
                        parent_manifest.get(path),
                        current_manifest.get(path),
                    )
                )
                and any(other.startswith(f"{path}/") for other in paths)
            )
        }
        return sorted(path for path in paths if not _matches(path, self.ignore_paths))

    def diff_parent(self, worktree_path: Path, parent_commit: str) -> WorktreeSnapshot:
        changed = self.changed_files(worktree_path, parent_commit)
        statuses = []
        with tempfile.TemporaryDirectory(
            prefix="shinka-candidate-status-"
        ) as temporary:
            current_archive = Path(temporary) / "candidate.tar"
            create_normalized_archive(
                worktree_path,
                current_archive,
                excludes=DEFAULT_EXCLUDES,
                limits=self.archive_limits,
            )
            parent_names = set(_archive_manifest(self.artifacts.verify(parent_commit)))
            current_names = set(_archive_manifest(current_archive))
        for path in changed:
            marker = (
                "A"
                if path not in parent_names
                else "D" if path not in current_names else "M"
            )
            statuses.append(f"{marker} {path}")
        return WorktreeSnapshot(
            commit_sha=None,
            parent_commit=parent_commit,
            diff="\n".join(statuses),
            changed_files=changed,
            status="\n".join(statuses),
            diff_stat=f"{len(changed)} path(s) changed",
        )

    def enforce_mutability(self, changed_files: Iterable[str]) -> None:
        violations: list[str] = []
        for path in changed_files:
            normalized = _normalize(path)
            if (
                Path(path).is_absolute()
                or normalized == ".."
                or normalized.startswith("../")
            ):
                violations.append(f"{path}: path traversal")
            elif _matches(normalized, [".git", ".shinka"]):
                violations.append(f"{path}: protected control path")
            elif self.immutable_paths and _matches(normalized, self.immutable_paths):
                violations.append(f"{path}: immutable path")
            elif self.mutable_paths and not _matches(normalized, self.mutable_paths):
                violations.append(f"{path}: outside mutable paths")
        if violations:
            raise MutabilityViolation("; ".join(violations))

    def verify_policy_files(self, worktree: RepoWorktree) -> None:
        violations: list[str] = []
        control = worktree.path / ".shinka"
        if worktree.policy_fingerprints and (
            not control.is_dir() or control.is_symlink()
        ):
            violations.append(".shinka: control directory changed")
        for relative, expected in worktree.policy_fingerprints.items():
            path = worktree.path / relative
            if not path.is_file() or _sha256(path) != expected:
                violations.append(f"{relative}: policy file changed")
        if violations:
            raise MutabilityViolation("; ".join(violations))

    def validate_snapshot(
        self, worktree: RepoWorktree, snapshot: WorktreeSnapshot
    ) -> None:
        self.verify_policy_files(worktree)
        self.enforce_hidden_paths_absent(worktree)
        self.enforce_mutability(snapshot.changed_files)
        with tempfile.TemporaryDirectory(
            prefix="shinka-candidate-validate-"
        ) as temporary:
            current_archive = Path(temporary) / "candidate.tar"
            create_normalized_archive(
                worktree.path,
                current_archive,
                excludes=DEFAULT_EXCLUDES,
                limits=self.archive_limits,
            )
            current_names = set(_archive_manifest(current_archive))
        deleted = set(snapshot.changed_files) - current_names
        violations: list[str] = []
        for relative in snapshot.changed_files:
            normalized = _normalize(relative)
            path = worktree.path / normalized
            if normalized in deleted and not self.allow_deletions:
                violations.append(f"{relative}: deletions are not allowed")
                continue
            if (
                Path(normalized).name in self.LOCKFILE_NAMES
                and not self.allow_lockfile_changes
            ):
                violations.append(f"{relative}: lockfile changes are not allowed")
                continue
            if path.is_symlink():
                target = os.readlink(path)
                target_path = Path(target)
                resolved = (
                    target_path
                    if target_path.is_absolute()
                    else path.parent / target_path
                ).resolve()
                try:
                    resolved.relative_to(worktree.path.resolve())
                except ValueError:
                    violations.append(f"{relative}: symlink escapes candidate root")
                continue
            if not path.exists() or path.is_dir():
                continue
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                violations.append(f"{relative}: special files are not allowed")
                continue
            size = path.stat().st_size
            if self.max_file_bytes is not None and size > self.max_file_bytes:
                violations.append(f"{relative}: file exceeds size limit")
            if not self.allow_binary_files:
                with path.open("rb") as handle:
                    if b"\0" in handle.read(8192):
                        violations.append(f"{relative}: binary files are not allowed")
        if violations:
            raise MutabilityViolation("; ".join(violations))

    def apply_agent_view_changes(
        self,
        agent_view: RepoWorktree,
        target_worktree: RepoWorktree,
    ) -> WorktreeSnapshot:
        snapshot = self.diff_parent(agent_view.path, agent_view.parent_digest)
        self.enforce_hidden_paths_absent(agent_view)
        snapshot.changed_files = [
            path
            for path in snapshot.changed_files
            if not _matches(path, agent_view.omitted_paths)
        ]
        statuses = [
            line
            for line in snapshot.status.splitlines()
            if not _matches(line[2:], agent_view.omitted_paths)
        ]
        snapshot.status = "\n".join(statuses)
        snapshot.diff = snapshot.status
        snapshot.diff_stat = f"{len(snapshot.changed_files)} path(s) changed"
        self.validate_snapshot(agent_view, snapshot)
        for relative in snapshot.changed_files:
            source = agent_view.path / relative
            destination = target_worktree.path / relative
            if source.exists() or source.is_symlink():
                _copy(source, destination)
            elif self.allow_deletions:
                _remove(destination)
            else:
                raise MutabilityViolation(f"{relative}: deletions are not allowed")
        return snapshot

    def commit_child(
        self,
        worktree: RepoWorktree,
        *,
        message: Optional[str] = None,
    ) -> WorktreeSnapshot:
        snapshot = self.diff_parent(worktree.path, worktree.parent_digest)
        self.validate_snapshot(worktree, snapshot)
        reference, _metadata = self.artifacts.put_tree(
            worktree.path,
            kind="candidate",
            excludes=DEFAULT_EXCLUDES,
            limits=self.archive_limits,
        )
        self._references[reference.digest] = reference
        snapshot.commit_sha = reference.digest
        snapshot.candidate_digest = reference.digest
        snapshot.artifact_uri = f"cas:{reference.digest}"
        return snapshot

    def cleanup_worktree(self, worktree: RepoWorktree, *, remove: bool = True) -> None:
        if remove:
            shutil.rmtree(worktree.path, ignore_errors=True)
