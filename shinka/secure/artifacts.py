"""Owner-private content-addressed artifact storage."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from .archive import (
    ArchiveLimits,
    SnapshotMetadata,
    create_normalized_archive,
    extract_normalized_archive,
    validate_archive,
)
from .canonical import DIGEST_PREFIX, validate_digest
from .contracts import ArtifactRef
from .errors import ArtifactIntegrityError


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ContentAddressedStore:
    """Immutable SHA-256 objects with atomic same-filesystem publication."""

    def __init__(self, root: Path | str) -> None:
        raw_root = Path(root).expanduser()
        if raw_root.exists() and raw_root.is_symlink():
            raise ArtifactIntegrityError("Artifact store root cannot be a symlink")
        raw_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(raw_root, 0o700)
        self.root = raw_root.resolve()
        self.objects = self.root / "sha256"
        self.temporary = self.root / "tmp"
        self.objects.mkdir(mode=0o700, exist_ok=True)
        self.temporary.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.objects, 0o700)
        os.chmod(self.temporary, 0o700)

    def path_for(self, digest: str) -> Path:
        validate_digest(digest)
        value = digest[len(DIGEST_PREFIX) :]
        return self.objects / value[:2] / value

    def _publish(
        self, temporary: Path, digest: str, size: int, kind: str
    ) -> ArtifactRef:
        target = self.path_for(digest)
        target.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(target.parent, 0o700)
        if target.exists():
            temporary.unlink(missing_ok=True)
            self.verify(digest, expected_size=size)
            return ArtifactRef(digest=digest, size=size, kind=kind)
        os.chmod(temporary, 0o400)
        try:
            os.link(temporary, target)
        except FileExistsError:
            self.verify(digest, expected_size=size)
        finally:
            temporary.unlink(missing_ok=True)
        _fsync_directory(target.parent)
        return ArtifactRef(digest=digest, size=size, kind=kind)

    def put_stream(self, stream: BinaryIO, *, kind: str) -> ArtifactRef:
        digest = hashlib.sha256()
        size = 0
        fd, name = tempfile.mkstemp(prefix="object-", dir=self.temporary)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            identity = f"{DIGEST_PREFIX}{digest.hexdigest()}"
            return self._publish(temporary, identity, size, kind)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes, *, kind: str) -> ArtifactRef:
        from io import BytesIO

        return self.put_stream(BytesIO(data), kind=kind)

    def put_file(
        self,
        source: Path | str,
        *,
        kind: str,
        expected_digest: str | None = None,
    ) -> ArtifactRef:
        with Path(source).open("rb") as stream:
            reference = self.put_stream(stream, kind=kind)
        if expected_digest is not None and reference.digest != expected_digest:
            raise ArtifactIntegrityError("Artifact does not match its expected digest")
        return reference

    def put_tree(
        self,
        source: Path | str,
        *,
        kind: str,
        excludes: tuple[str, ...],
        includes: tuple[str, ...] | None = None,
        limits: ArchiveLimits = ArchiveLimits(),
    ) -> tuple[ArtifactRef, SnapshotMetadata]:
        fd, name = tempfile.mkstemp(
            prefix="snapshot-", suffix=".tar", dir=self.temporary
        )
        os.close(fd)
        temporary = Path(name)
        temporary.unlink()
        try:
            metadata = create_normalized_archive(
                source,
                temporary,
                excludes=excludes,
                includes=includes,
                limits=limits,
            )
            reference = self.put_file(
                temporary,
                kind=kind,
                expected_digest=metadata.digest,
            )
            return reference, metadata
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, digest: str, *, expected_size: int | None = None) -> Path:
        path = self.path_for(digest)
        if not path.is_file() or path.is_symlink():
            raise ArtifactIntegrityError(f"Artifact is missing: {digest}")
        actual = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                actual.update(chunk)
                size += len(chunk)
        actual_digest = f"{DIGEST_PREFIX}{actual.hexdigest()}"
        if actual_digest != digest or (
            expected_size is not None and size != expected_size
        ):
            raise ArtifactIntegrityError(f"Artifact verification failed: {digest}")
        return path

    def materialize_archive(
        self,
        reference: ArtifactRef,
        destination: Path | str,
        *,
        limits: ArchiveLimits = ArchiveLimits(),
    ) -> SnapshotMetadata:
        archive = self.verify(reference.digest, expected_size=reference.size)
        validate_archive(archive, limits=limits)
        return extract_normalized_archive(archive, destination, limits=limits)

    def copy_to(self, reference: ArtifactRef, destination: Path | str) -> None:
        source = self.verify(reference.digest, expected_size=reference.size)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        with source.open("rb") as input_stream, temporary.open("xb") as output:
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
