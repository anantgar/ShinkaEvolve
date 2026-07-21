"""Networkless candidate build boundary and bounded output collection."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .archive import ArchiveLimits, create_normalized_archive
from .artifacts import ContentAddressedStore
from .contracts import ArtifactRef, NetworkMode, ResourceLimits, validate_pinned_image
from .containers import (
    ContainerHandle,
    ContainerMount,
    ContainerPlan,
    DockerEngine,
    prepare_bind_source,
)
from .errors import FailureClass, SecureExecutionError, SecurityPolicyError


@dataclass(frozen=True)
class BuildResult:
    runtime_artifact: ArtifactRef
    stdout: bytes
    stderr: bytes
    container_name: str | None = None


class SecureBuildBackend:
    def __init__(
        self,
        *,
        engine: DockerEngine,
        artifacts: ContentAddressedStore,
        image: str,
        limits: ResourceLimits,
        sandbox_user: str | None = None,
        archive_limits: ArchiveLimits = ArchiveLimits(),
    ) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.image = validate_pinned_image(image)
        self.limits = limits
        self.sandbox_user = sandbox_user or f"{os.getuid()}:{os.getgid()}"
        if self.sandbox_user in {"0", "0:0"}:
            self.sandbox_user = "65532:65532"
        self.archive_limits = archive_limits

    def build(
        self,
        *,
        candidate: ArtifactRef,
        dependencies: ArtifactRef,
        command: tuple[str, ...],
        job_id: str,
        attempt_id: str,
        timeout_seconds: float,
    ) -> BuildResult:
        if not command:
            source = self.artifacts.verify(
                candidate.digest, expected_size=candidate.size
            )
            runtime = self.artifacts.put_file(
                source,
                kind="runtime",
                expected_digest=candidate.digest,
            )
            return BuildResult(runtime_artifact=runtime, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory(prefix="shinka-build-") as temporary_name:
            temporary = Path(temporary_name)
            os.chmod(temporary, 0o700)
            source = temporary / "candidate"
            dependency_root = temporary / "dependencies"
            scratch = temporary / "scratch"
            output = temporary / "output"
            self.artifacts.materialize_archive(
                candidate, source, limits=self.archive_limits
            )
            self.artifacts.materialize_archive(
                dependencies, dependency_root, limits=self.archive_limits
            )
            scratch.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            prepare_bind_source(source, writable=False)
            prepare_bind_source(dependency_root, writable=False)
            prepare_bind_source(scratch, writable=True)
            prepare_bind_source(output, writable=True)
            short = attempt_id.replace("-", "")[:16]
            name = f"shinka-build-{short}"
            plan = ContainerPlan(
                name=name,
                image=self.image,
                command=command,
                role="build",
                labels={
                    "shinka.managed": "true",
                    "shinka.job_id": job_id,
                    "shinka.attempt_id": attempt_id,
                    "shinka.role": "build",
                },
                limits=self.limits,
                mounts=(
                    ContainerMount(source, "/candidate", read_only=True),
                    ContainerMount(dependency_root, "/dependencies", read_only=True),
                    ContainerMount(scratch, "/build", read_only=False),
                    ContainerMount(output, "/output", read_only=False),
                ),
                environment={
                    "HOME": "/tmp/home",
                    "SHINKA_CANDIDATE_ROOT": "/candidate",
                    "SHINKA_DEPENDENCY_ROOT": "/dependencies",
                    "SHINKA_OUTPUT_ROOT": "/output",
                },
                allowed_environment_names=frozenset(
                    {
                        "HOME",
                        "SHINKA_CANDIDATE_ROOT",
                        "SHINKA_DEPENDENCY_ROOT",
                        "SHINKA_OUTPUT_ROOT",
                    }
                ),
                workdir="/build",
                user=self.sandbox_user,
                network=NetworkMode.DISABLED,
                read_only_root=True,
                tmpfs={
                    "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
                    "/run": "rw,nosuid,nodev,noexec,size=16m,mode=755",
                },
            )
            handle: ContainerHandle | None = None
            try:
                handle = self.engine.create(plan)
                result = self.engine.run_capture(
                    handle,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=self.limits.output_bytes,
                )
                if result.timed_out:
                    raise SecureExecutionError(
                        FailureClass.STARTUP_TIMEOUT,
                        "Candidate build exceeded its explicit timeout",
                    )
                if result.output_limited:
                    raise SecureExecutionError(
                        FailureClass.BUILD_FAILED,
                        "Candidate build exceeded its output limit",
                    )
                if result.exit_code != 0:
                    raise SecureExecutionError(
                        FailureClass.BUILD_FAILED,
                        "Candidate build failed",
                        private_diagnostic=result.stderr.decode(
                            "utf-8", errors="replace"
                        )[-4000:],
                    )
                archive = temporary / "runtime.tar"
                metadata = create_normalized_archive(
                    output,
                    archive,
                    excludes=(".git", ".git/**", ".shinka", ".shinka/**"),
                    limits=self.archive_limits,
                )
                if metadata.entry_count == 0:
                    raise SecurityPolicyError(
                        "Candidate build produced no runtime artifact"
                    )
                runtime = self.artifacts.put_file(
                    archive,
                    kind="runtime",
                    expected_digest=metadata.digest,
                )
                return BuildResult(
                    runtime_artifact=runtime,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    container_name=name,
                )
            finally:
                if handle is not None:
                    self.engine.remove(handle, force=True)
