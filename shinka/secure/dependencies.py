"""Trusted, hash-pinned dependency preparation for offline sandboxes."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import unquote, urlparse

from .archive import ArchiveLimits, create_normalized_archive
from .artifacts import ContentAddressedStore
from .canonical import canonical_json_bytes, digest_json
from .contracts import ArtifactRef, validate_pinned_image
from .errors import ConfigurationError, FailureClass, SecureExecutionError

DEPENDENCY_SCHEMA_VERSION = "shinka-dependencies-v1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")


@dataclass(frozen=True)
class DependencyArtifact:
    name: str
    url: str
    sha256: str
    size: int | None = None
    runtime: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_NAME.fullmatch(self.name):
            raise ConfigurationError("Dependency artifact name is unsafe")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ConfigurationError("Dependency artifact requires a lowercase SHA-256")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"https", "file"}:
            raise ConfigurationError("Dependency URLs must use https or file")
        if self.size is not None and self.size <= 0:
            raise ConfigurationError("Declared dependency size must be > 0")


@dataclass(frozen=True)
class SystemDependency:
    name: str
    version: str
    image: str

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ConfigurationError("System dependency name and version are required")
        validate_pinned_image(self.image)


@dataclass(frozen=True)
class DependencyManifest:
    artifacts: tuple[DependencyArtifact, ...] = ()
    system: tuple[SystemDependency, ...] = ()
    images: tuple[str, ...] = ()
    schema_version: str = DEPENDENCY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEPENDENCY_SCHEMA_VERSION:
            raise ConfigurationError("Unsupported dependency manifest version")
        names = [artifact.name.casefold() for artifact in self.artifacts]
        if len(names) != len(set(names)):
            raise ConfigurationError("Dependency artifact names collide")
        for image in self.images:
            validate_pinned_image(image)

    @property
    def digest(self) -> str:
        return digest_json(asdict(self))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DependencyManifest":
        allowed = {"schema_version", "artifacts", "system", "images"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ConfigurationError(f"Unknown dependency manifest fields: {unknown}")
        artifacts_raw = value.get("artifacts", [])
        system_raw = value.get("system", [])
        images_raw = value.get("images", [])
        if not isinstance(artifacts_raw, list) or not isinstance(system_raw, list):
            raise ConfigurationError("Dependency artifact/system fields must be lists")
        if not isinstance(images_raw, list) or not all(
            isinstance(item, str) for item in images_raw
        ):
            raise ConfigurationError("Dependency images must be a list of strings")
        return cls(
            artifacts=tuple(DependencyArtifact(**item) for item in artifacts_raw),
            system=tuple(SystemDependency(**item) for item in system_raw),
            images=tuple(images_raw),
            schema_version=str(value.get("schema_version", DEPENDENCY_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class DependencyBundle:
    artifact: ArtifactRef
    runtime_artifact: ArtifactRef
    manifest_digest: str
    runtime_names: tuple[str, ...]


class DependencyPreparer:
    """Fetch declared bytes without running package managers or build scripts."""

    def __init__(
        self,
        store: ContentAddressedStore,
        *,
        max_artifact_bytes: int = 2 * 1024 * 1024 * 1024,
        allowed_https_hosts: Sequence[str] = (),
    ) -> None:
        self.store = store
        self.max_artifact_bytes = max_artifact_bytes
        self.allowed_https_hosts = frozenset(
            host.casefold() for host in allowed_https_hosts
        )

    def _open(self, dependency: DependencyArtifact):
        parsed = urlparse(dependency.url)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path)).resolve()
            if not path.is_file() or path.is_symlink():
                raise SecureExecutionError(
                    FailureClass.DEPENDENCY_PREPARATION,
                    f"Declared local dependency is unavailable: {dependency.name}",
                )
            return path.open("rb"), dependency.url
        if self.allowed_https_hosts and (
            not parsed.hostname
            or parsed.hostname.casefold() not in self.allowed_https_hosts
        ):
            raise SecureExecutionError(
                FailureClass.DEPENDENCY_PREPARATION,
                f"Dependency host is not allowlisted: {dependency.name}",
            )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            dependency.url,
            headers={"User-Agent": "ShinkaEvolve-secure-dependency/1"},
        )
        response = opener.open(request, timeout=60.0)
        final = urlparse(response.geturl())
        if final.scheme != "https" or (
            self.allowed_https_hosts
            and (
                not final.hostname
                or final.hostname.casefold() not in self.allowed_https_hosts
            )
        ):
            response.close()
            raise SecureExecutionError(
                FailureClass.DEPENDENCY_PREPARATION,
                f"Dependency redirect left its allowlisted HTTPS origin: {dependency.name}",
            )
        return response, response.geturl()

    def prepare(self, manifest: DependencyManifest) -> DependencyBundle:
        with tempfile.TemporaryDirectory(
            prefix="shinka-dependencies-"
        ) as temporary_name:
            root = Path(temporary_name)
            os.chmod(root, 0o700)
            files = root / "files"
            files.mkdir(mode=0o700)
            resolved: list[dict[str, object]] = []
            for dependency in manifest.artifacts:
                stream, _final_url = self._open(dependency)
                target = files / dependency.name
                digest = hashlib.sha256()
                size = 0
                try:
                    with target.open("xb") as output:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            size += len(chunk)
                            if size > self.max_artifact_bytes:
                                raise SecureExecutionError(
                                    FailureClass.DEPENDENCY_PREPARATION,
                                    f"Dependency exceeds its download limit: {dependency.name}",
                                )
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    stream.close()
                if digest.hexdigest() != dependency.sha256:
                    raise SecureExecutionError(
                        FailureClass.DEPENDENCY_PREPARATION,
                        f"Dependency hash mismatch: {dependency.name}",
                    )
                if dependency.size is not None and size != dependency.size:
                    raise SecureExecutionError(
                        FailureClass.DEPENDENCY_PREPARATION,
                        f"Dependency size mismatch: {dependency.name}",
                    )
                os.chmod(target, 0o400)
                resolved.append(
                    {
                        "name": dependency.name,
                        "sha256": dependency.sha256,
                        "size": size,
                        "runtime": dependency.runtime,
                    }
                )

            resolved_manifest = {
                "schema_version": DEPENDENCY_SCHEMA_VERSION,
                "declared_manifest_digest": manifest.digest,
                "artifacts": resolved,
                "system": [asdict(item) for item in manifest.system],
                "images": list(manifest.images),
            }
            (root / "manifest.json").write_bytes(
                canonical_json_bytes(resolved_manifest)
            )
            archive = root.parent / f"{root.name}.tar"
            metadata = create_normalized_archive(
                root,
                archive,
                excludes=(".git", ".git/**", ".shinka", ".shinka/**"),
                limits=ArchiveLimits(
                    max_entries=max(32, len(manifest.artifacts) * 2 + 4),
                    max_total_bytes=max(
                        1024 * 1024,
                        sum(
                            item.size or self.max_artifact_bytes
                            for item in manifest.artifacts
                        ),
                    ),
                    max_file_bytes=self.max_artifact_bytes,
                ),
            )
            reference = self.store.put_file(
                archive,
                kind="dependency_bundle",
                expected_digest=metadata.digest,
            )
            archive.unlink(missing_ok=True)
            runtime_root = root / "_runtime_bundle"
            runtime_files = runtime_root / "files"
            runtime_files.mkdir(parents=True, mode=0o700)
            runtime_resolved = [item for item in resolved if item["runtime"]]
            for item in runtime_resolved:
                source = files / str(item["name"])
                target = runtime_files / str(item["name"])
                shutil.copyfile(source, target)
                os.chmod(target, 0o400)
            (runtime_root / "manifest.json").write_bytes(
                canonical_json_bytes(
                    {
                        **resolved_manifest,
                        "artifacts": runtime_resolved,
                        "bundle_scope": "runtime",
                    }
                )
            )
            runtime_archive = root / "runtime-dependencies.tar"
            runtime_metadata = create_normalized_archive(
                runtime_root,
                runtime_archive,
                excludes=(".git", ".git/**", ".shinka", ".shinka/**"),
                limits=ArchiveLimits(
                    max_entries=max(32, len(runtime_resolved) * 2 + 4),
                    max_total_bytes=max(
                        1024 * 1024,
                        sum(int(item["size"]) for item in runtime_resolved),
                    ),
                    max_file_bytes=self.max_artifact_bytes,
                ),
            )
            runtime_reference = self.store.put_file(
                runtime_archive,
                kind="runtime_dependency_bundle",
                expected_digest=runtime_metadata.digest,
            )
            runtime_archive.unlink(missing_ok=True)
            return DependencyBundle(
                artifact=reference,
                runtime_artifact=runtime_reference,
                manifest_digest=manifest.digest,
                runtime_names=tuple(
                    item.name for item in manifest.artifacts if item.runtime
                ),
            )
