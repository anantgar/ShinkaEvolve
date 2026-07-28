"""Persistent, networkless candidate service running outside the evaluator."""

from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .archive import ArchiveLimits
from .artifacts import ContentAddressedStore
from .canonical import canonical_json_bytes
from .contracts import ArtifactRef, NetworkMode, ResourceLimits, validate_pinned_image
from .containers import (
    ContainerHandle,
    ContainerMount,
    ContainerPlan,
    DockerEngine,
    prepare_bind_source,
)
from .errors import FailureClass, SecureExecutionError, SecurityPolicyError
from .protocol import (
    CANDIDATE_PROTOCOL_VERSION,
    DEFAULT_MAX_FRAME_BYTES,
    read_frame,
    write_frame,
)


@dataclass(frozen=True)
class RunnerResponse:
    request_id: str
    output: Any
    elapsed_seconds: float
    response_bytes: int


class ContainerCandidateRunner:
    """The evaluator's only capability for invoking generated candidate code."""

    def __init__(
        self,
        *,
        engine: DockerEngine,
        artifacts: ContentAddressedStore,
        runtime_artifact: ArtifactRef,
        dependency_artifact: ArtifactRef,
        image: str,
        command: tuple[str, ...],
        limits: ResourceLimits,
        job_id: str,
        attempt_id: str,
        startup_timeout_seconds: float,
        request_timeout_seconds: float | None,
        sandbox_user: str | None = None,
        archive_limits: ArchiveLimits = ArchiveLimits(),
    ) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.runtime_artifact = runtime_artifact
        self.dependency_artifact = dependency_artifact
        self.image = validate_pinned_image(image)
        self.command = command
        self.limits = limits
        self.job_id = job_id
        self.attempt_id = attempt_id
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.sandbox_user = sandbox_user or f"{os.getuid()}:{os.getgid()}"
        if self.sandbox_user in {"0", "0:0"}:
            self.sandbox_user = "65532:65532"
        self.archive_limits = archive_limits
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._handle: ContainerHandle | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._messages: queue.Queue[Mapping[str, Any] | BaseException] = queue.Queue(
            maxsize=16
        )
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._stderr = bytearray()
        self._stderr_exceeded = threading.Event()
        self._started_at: float | None = None

    @property
    def stderr(self) -> bytes:
        return bytes(self._stderr)

    @property
    def container_name(self) -> str | None:
        return self._handle.name if self._handle else None

    def _read_messages(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                self._messages.put(
                    read_frame(
                        self._process.stdout,
                        max_bytes=min(
                            DEFAULT_MAX_FRAME_BYTES, self.limits.output_bytes
                        ),
                    )
                )
        except BaseException as exc:
            self._messages.put(exc)

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for chunk in iter(lambda: self._process.stderr.read(64 * 1024), b""):
            remaining = self.limits.output_bytes - len(self._stderr)
            if remaining <= 0:
                self._stderr_exceeded.set()
                return
            self._stderr.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self._stderr_exceeded.set()
                return

    def _next_message(self, timeout: float | None) -> Mapping[str, Any]:
        try:
            item = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise SecureExecutionError(
                FailureClass.REQUEST_TIMEOUT,
                "Candidate did not respond before its explicit timeout",
            ) from exc
        if isinstance(item, BaseException):
            if isinstance(item, SecureExecutionError):
                raise item
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate protocol stream terminated",
                private_diagnostic=str(item),
            ) from item
        return item

    def start(self) -> None:
        if self._handle is not None:
            raise SecurityPolicyError("Candidate runner has already started")
        temporary = tempfile.TemporaryDirectory(prefix="shinka-runtime-")
        self._temporary = temporary
        root = Path(temporary.name)
        os.chmod(root, 0o700)
        runtime = root / "candidate"
        dependencies = root / "dependencies"
        self.artifacts.materialize_archive(
            self.runtime_artifact, runtime, limits=self.archive_limits
        )
        self.artifacts.materialize_archive(
            self.dependency_artifact, dependencies, limits=self.archive_limits
        )
        prepare_bind_source(runtime, writable=False)
        prepare_bind_source(dependencies, writable=False)
        short = self.attempt_id.replace("-", "")[:16]
        name = f"shinka-runtime-{short}"
        plan = ContainerPlan(
            name=name,
            image=self.image,
            command=self.command,
            role="runtime",
            labels={
                "shinka.managed": "true",
                "shinka.job_id": self.job_id,
                "shinka.attempt_id": self.attempt_id,
                "shinka.role": "runtime",
            },
            limits=self.limits,
            mounts=(
                ContainerMount(runtime, "/candidate", read_only=True),
                ContainerMount(dependencies, "/dependencies", read_only=True),
            ),
            environment={
                "HOME": "/tmp/home",
                "SHINKA_CANDIDATE_ROOT": "/candidate",
                "SHINKA_DEPENDENCY_ROOT": "/dependencies",
                "SHINKA_PROTOCOL": CANDIDATE_PROTOCOL_VERSION,
            },
            allowed_environment_names=frozenset(
                {
                    "HOME",
                    "SHINKA_CANDIDATE_ROOT",
                    "SHINKA_DEPENDENCY_ROOT",
                    "SHINKA_PROTOCOL",
                }
            ),
            workdir="/candidate",
            user=self.sandbox_user,
            network=NetworkMode.DISABLED,
            read_only_root=True,
            tmpfs={
                "/tmp": "rw,nosuid,nodev,noexec,size=512m,mode=1777",
                "/run": "rw,nosuid,nodev,noexec,size=16m,mode=755",
            },
            stdin_open=True,
        )
        self._handle = self.engine.create(plan)
        self._process = subprocess.Popen(
            [
                self.engine.executable,
                "start",
                "--attach",
                "--interactive",
                self._handle.container_id,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._reader = threading.Thread(target=self._read_messages, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        try:
            ready = self._next_message(self.startup_timeout_seconds)
            if (
                ready.get("type") != "ready"
                or ready.get("protocol") != CANDIDATE_PROTOCOL_VERSION
            ):
                raise SecureExecutionError(
                    FailureClass.PROTOCOL_FAILED,
                    "Candidate did not emit the required ready handshake",
                )
        except Exception:
            self.stop()
            raise
        self._started_at = time.monotonic()

    def request(
        self,
        value: Any,
        *,
        request_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> RunnerResponse:
        if self._process is None or self._process.stdin is None:
            raise SecurityPolicyError("Candidate runner is not started")
        if self._process.poll() is not None:
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate process exited before the request",
            )
        if self._stderr_exceeded.is_set():
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate exceeded its diagnostic output limit",
            )
        request_id = request_id or str(uuid.uuid4())
        timeout = (
            self.request_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        with self._request_lock:
            started = time.monotonic()
            with self._write_lock:
                write_frame(
                    self._process.stdin,
                    {"type": "request", "id": request_id, "input": value},
                )
            response = self._next_message(timeout)
            elapsed = time.monotonic() - started
        if response.get("id") != request_id:
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate response id does not match its request",
            )
        if response.get("type") == "error":
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate reported a request error",
                private_diagnostic=str(response.get("error", ""))[:4000],
            )
        if response.get("type") != "response" or "output" not in response:
            raise SecureExecutionError(
                FailureClass.PROTOCOL_FAILED,
                "Candidate emitted an invalid response message",
            )
        return RunnerResponse(
            request_id=request_id,
            output=response["output"],
            elapsed_seconds=elapsed,
            response_bytes=len(canonical_json_bytes(response)),
        )

    def stop(self) -> None:
        handle = self._handle
        process = self._process
        self._handle = None
        self._process = None
        cleanup_error: Exception | None = None
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if handle is not None:
            try:
                self.engine.stop(handle, timeout_seconds=5.0)
                self.engine.remove(handle, force=True)
            except Exception as exc:
                cleanup_error = exc
        if process is not None:
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for thread in (self._reader, self._stderr_reader):
            if thread is not None:
                thread.join(timeout=2.0)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if cleanup_error is not None:
            raise SecureExecutionError(
                FailureClass.CLEANUP_FAILED,
                "Candidate container cleanup failed",
                private_diagnostic=str(cleanup_error),
            ) from cleanup_error

    def __enter__(self) -> "ContainerCandidateRunner":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
