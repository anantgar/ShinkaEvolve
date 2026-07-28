"""Fail-closed Docker CLI backend for every untrusted command."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .contracts import NetworkMode, ResourceLimits, validate_pinned_image
from .errors import FailureClass, SecureExecutionError, SecurityPolicyError

_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SENSITIVE_ENV = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE|AUTH|AWS_|AZURE_|GOOGLE_APPLICATION)",
    re.IGNORECASE,
)
_REQUIRED_LABELS = frozenset(
    {"shinka.managed", "shinka.job_id", "shinka.attempt_id", "shinka.role"}
)


def _validate_headless_image_agents(
    image_data: Mapping[str, object], required_agents: Iterable[str]
) -> None:
    config = image_data.get("Config") or {}
    labels = config.get("Labels") if isinstance(config, dict) else {}
    labels = labels if isinstance(labels, dict) else {}
    declared_agents = {
        item.strip()
        for item in str(labels.get("io.shinka.headless.agents", "")).split(",")
        if item.strip()
    }
    required_agent_set = set(required_agents)
    missing_agents = sorted(required_agent_set - declared_agents)
    agent_version_labels = {
        agent: (
            "io.shinka.antigravity.release"
            if agent == "antigravity"
            else f"io.shinka.{agent}.version"
        )
        for agent in required_agent_set
    }
    missing_labels = sorted(
        label
        for label in (
            "io.shinka.headless.version",
            "io.shinka.sandbox-user",
            *agent_version_labels.values(),
        )
        if not str(labels.get(label, "")).strip()
    )
    if missing_agents or missing_labels:
        raise SecureExecutionError(
            FailureClass.CONTAINER_PREFLIGHT,
            "The pinned mutation image does not satisfy the selected agent contract",
            private_diagnostic=(
                f"missing agents: {missing_agents}; missing labels: {missing_labels}"
            ),
        )


@dataclass(frozen=True)
class DockerVersionRange:
    minimum: tuple[int, int] = (24, 0)
    maximum_exclusive: tuple[int, int] = (31, 0)


@dataclass(frozen=True)
class ContainerMount:
    source: Path
    target: str
    read_only: bool = True

    def __post_init__(self) -> None:
        target = PurePosixPath(self.target)
        if not target.is_absolute() or any(part == ".." for part in target.parts):
            raise SecurityPolicyError("Container mount targets must be absolute")
        if (
            self.target == "/"
            or self.target.startswith("/proc")
            or self.target.startswith("/sys")
        ):
            raise SecurityPolicyError("Container mount target is security-sensitive")
        unresolved = self.source.expanduser()
        if unresolved.is_symlink():
            raise SecurityPolicyError("Container mount source cannot be a symlink")
        source = unresolved.resolve()
        if not source.exists():
            raise SecurityPolicyError(
                "Container mount source must be a real existing path"
            )
        home = Path.home().resolve()
        source_contains_home = source == home or source in home.parents
        if (
            source == Path("/")
            or source_contains_home
            or str(source) in {"/var/run", "/run"}
        ):
            raise SecurityPolicyError("Container mount source is too broad")
        if source.name in {"docker.sock", "podman.sock"}:
            raise SecurityPolicyError("Container engine sockets cannot be mounted")
        object.__setattr__(self, "source", source)


@dataclass(frozen=True)
class ContainerPlan:
    name: str
    image: str
    command: tuple[str, ...]
    role: str
    labels: Mapping[str, str]
    limits: ResourceLimits
    mounts: tuple[ContainerMount, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    allowed_environment_names: frozenset[str] = frozenset()
    workdir: str = "/workspace"
    user: str = "65532:65532"
    network: NetworkMode = NetworkMode.DISABLED
    provider_network: str | None = None
    provider_proxy: str | None = None
    read_only_root: bool = True
    tmpfs: Mapping[str, str] = field(
        default_factory=lambda: {
            "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
            "/run": "rw,nosuid,nodev,noexec,size=16m,mode=755",
        }
    )
    stdin_open: bool = False

    def __post_init__(self) -> None:
        validate_pinned_image(self.image)
        if not _CONTAINER_NAME.fullmatch(self.name):
            raise SecurityPolicyError("Invalid secure container name")
        if not self.command or any(
            not isinstance(item, str) or not item for item in self.command
        ):
            raise SecurityPolicyError("Container command must be non-empty argv")
        if not self.role or not self.role.replace("_", "").isalnum():
            raise SecurityPolicyError("Container role must be an identifier")
        if not _REQUIRED_LABELS.issubset(self.labels):
            raise SecurityPolicyError("Secure ownership labels are incomplete")
        if (
            self.labels.get("shinka.managed") != "true"
            or self.labels.get("shinka.role") != self.role
        ):
            raise SecurityPolicyError("Secure ownership labels do not match the plan")
        if not self.user or self.user in {"0", "0:0", "root"}:
            raise SecurityPolicyError("Untrusted containers cannot run as root")
        workdir = PurePosixPath(self.workdir)
        if not workdir.is_absolute() or ".." in workdir.parts:
            raise SecurityPolicyError("Container workdir must be absolute")

        targets: set[str] = set()
        for mount in self.mounts:
            if mount.target in targets:
                raise SecurityPolicyError("Duplicate container mount target")
            targets.add(mount.target)
        for target in self.tmpfs:
            path = PurePosixPath(target)
            if not path.is_absolute() or ".." in path.parts or target == "/":
                raise SecurityPolicyError("Invalid tmpfs target")
            if target in targets:
                raise SecurityPolicyError("A path cannot be both a mount and tmpfs")
            targets.add(target)

        allowed = set(self.allowed_environment_names)
        for name, value in self.environment.items():
            if not _ENV_NAME.fullmatch(name) or "\x00" in value:
                raise SecurityPolicyError("Invalid container environment entry")
            if name not in allowed:
                raise SecurityPolicyError(
                    f"Container environment variable is not allowlisted: {name}"
                )

        if self.network is NetworkMode.DISABLED:
            if self.provider_network or self.provider_proxy:
                raise SecurityPolicyError(
                    "Disabled network cannot declare a provider route"
                )
        elif not self.provider_network or not self.provider_proxy:
            raise SecurityPolicyError(
                "provider_only network requires a dedicated network and egress proxy"
            )
        else:
            proxy = urlparse(self.provider_proxy)
            if proxy.scheme not in {"http", "https"} or not proxy.hostname:
                raise SecurityPolicyError("Provider proxy must be an http(s) URL")

    def docker_create_argv(self, executable: str = "docker") -> list[str]:
        argv = [
            executable,
            "create",
            "--name",
            self.name,
            "--init",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            f"--pids-limit={self.limits.pids}",
            f"--memory={self.limits.memory_bytes}",
            f"--cpus={self.limits.cpus:g}",
            f"--ulimit=nofile={self.limits.open_files}:{self.limits.open_files}",
            f"--user={self.user}",
            f"--workdir={self.workdir}",
        ]
        if self.read_only_root:
            argv.append("--read-only")
        if self.stdin_open:
            argv.append("--interactive")
        argv.append(
            "--network=none"
            if self.network is NetworkMode.DISABLED
            else f"--network={self.provider_network}"
        )
        for key, value in sorted(self.labels.items()):
            argv.extend(["--label", f"{key}={value}"])
        for name, value in sorted(self.environment.items()):
            argv.extend(["--env", f"{name}={value}"])
        if self.network is NetworkMode.PROVIDER_ONLY:
            argv.extend(["--env", f"HTTPS_PROXY={self.provider_proxy}"])
            argv.extend(["--env", f"HTTP_PROXY={self.provider_proxy}"])
            argv.extend(["--env", "NO_PROXY="])
        for target, options in sorted(self.tmpfs.items()):
            argv.extend(["--tmpfs", f"{target}:{options}"])
        for mount in self.mounts:
            definition = f"type=bind,src={mount.source},dst={mount.target}" + (
                ",readonly" if mount.read_only else ""
            )
            argv.extend(["--mount", definition])
        argv.extend([self.image, *self.command])
        return argv


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    name: str
    job_id: str
    attempt_id: str
    role: str


@dataclass(frozen=True)
class ContainerResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False


def prepare_bind_source(path: Path | str, *, writable: bool) -> None:
    """Make a private temporary tree usable by any non-root container UID."""

    root = Path(path)

    def visit(current: Path) -> None:
        try:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return
            if stat.S_ISDIR(metadata.st_mode):
                current.chmod(0o777 if writable else 0o555)
                with os.scandir(current) as entries:
                    children = [Path(entry.path) for entry in entries]
                for child in children:
                    visit(child)
                return
            if stat.S_ISREG(metadata.st_mode):
                executable = bool(stat.S_IMODE(metadata.st_mode) & 0o111)
                if writable:
                    current.chmod(0o777 if executable else 0o666)
                else:
                    current.chmod(0o555 if executable else 0o444)
                return
            raise SecurityPolicyError("Bind source contains a special filesystem entry")
        except SecurityPolicyError:
            raise
        except OSError as exc:
            raise SecurityPolicyError(
                "Bind source permissions could not be prepared",
                private_diagnostic=str(exc),
            ) from exc

    if root.is_symlink() or not root.is_dir():
        raise SecurityPolicyError("Bind source must be a real directory")
    visit(root)


def normalize_candidate_permissions(path: Path | str) -> None:
    """Remove temporary broad write bits while preserving executability."""

    root = Path(path)

    def visit(current: Path) -> None:
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            return
        if stat.S_ISDIR(metadata.st_mode):
            current.chmod(0o755)
            with os.scandir(current) as entries:
                children = [Path(entry.path) for entry in entries]
            for child in children:
                visit(child)
        elif stat.S_ISREG(metadata.st_mode):
            executable = bool(stat.S_IMODE(metadata.st_mode) & 0o111)
            current.chmod(0o755 if executable else 0o644)

    try:
        if root.is_symlink() or not root.is_dir():
            raise SecurityPolicyError("Candidate tree must be a real directory")
        visit(root)
    except SecurityPolicyError:
        raise
    except OSError as exc:
        raise SecurityPolicyError(
            "Candidate permissions could not be normalized",
            private_diagnostic=str(exc),
        ) from exc


class DockerEngine:
    """Small Docker CLI adapter; there is deliberately no subprocess fallback."""

    def __init__(
        self,
        executable: str = "docker",
        *,
        version_range: DockerVersionRange = DockerVersionRange(),
        allow_rootful_dedicated_vm: bool = False,
    ) -> None:
        self.executable = executable
        self.version_range = version_range
        self.allow_rootful_dedicated_vm = allow_rootful_dedicated_vm

    def _run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        input_stream: BinaryIO | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                list(argv),
                stdin=input_stream,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "The secure container engine is unavailable",
                private_diagnostic=str(exc),
            ) from exc
        if completed.returncode != 0:
            diagnostic = completed.stderr.decode("utf-8", errors="replace")[-4000:]
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "The secure container engine rejected an operation",
                private_diagnostic=diagnostic,
            )
        return completed

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, int]:
        match = re.match(r"^(\d+)\.(\d+)", value)
        if not match:
            raise SecurityPolicyError("Container engine returned an invalid version")
        return int(match.group(1)), int(match.group(2))

    def preflight(
        self,
        *,
        images: Iterable[str] = (),
        provider_network: str | None = None,
        required_agents: Iterable[str] = (),
    ) -> dict[str, object]:
        if shutil.which(self.executable) is None:
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "Secure execution requires a Docker-compatible local container engine",
            )
        version_raw = self._run(
            [
                self.executable,
                "version",
                "--format",
                "{{json .Server}}",
            ]
        ).stdout
        info_raw = self._run([self.executable, "info", "--format", "{{json .}}"]).stdout
        try:
            version = json.loads(version_raw)
            info = json.loads(info_raw)
        except json.JSONDecodeError as exc:
            raise SecurityPolicyError(
                "Container engine preflight returned invalid JSON"
            ) from exc
        engine_version = self._version_tuple(str(version.get("Version", "")))
        if not (
            self.version_range.minimum
            <= engine_version
            < self.version_range.maximum_exclusive
        ):
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "Container engine version is outside the supported secure range",
            )
        if str(version.get("Os", "")).lower() != "linux":
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "Secure containers require a Linux container engine",
            )
        security_options = [
            str(item).lower() for item in info.get("SecurityOptions", [])
        ]
        if (
            platform.system() == "Linux"
            and not self.allow_rootful_dedicated_vm
            and not any("rootless" in item for item in security_options)
        ):
            raise SecureExecutionError(
                FailureClass.CONTAINER_PREFLIGHT,
                "Linux secure execution requires rootless mode or an explicitly declared dedicated container VM",
            )

        inspected_images: list[Mapping[str, object]] = []
        for image in images:
            validate_pinned_image(image)
            image_data = self._inspect_image(image)
            inspected_images.append(image_data)
            repo_digests = set(image_data.get("RepoDigests") or [])
            if image not in repo_digests:
                raise SecureExecutionError(
                    FailureClass.CONTAINER_PREFLIGHT,
                    "A required pinned container image is not present locally",
                    private_diagnostic=image,
                )
        required_agent_set = frozenset(required_agents)
        if required_agent_set:
            if len(inspected_images) != 1:
                raise SecurityPolicyError(
                    "Agent image preflight requires exactly one mutation image"
                )
            _validate_headless_image_agents(inspected_images[0], required_agent_set)
        if provider_network:
            network_raw = self._run(
                [self.executable, "network", "inspect", provider_network]
            ).stdout
            try:
                networks = json.loads(network_raw)
                network = networks[0]
            except (json.JSONDecodeError, IndexError, TypeError) as exc:
                raise SecurityPolicyError("Provider network inspection failed") from exc
            labels = network.get("Labels") or {}
            if (
                not network.get("Internal")
                or labels.get("shinka.provider_only") != "true"
            ):
                raise SecurityPolicyError(
                    "Provider network must be internal and labeled shinka.provider_only=true"
                )
        return {
            "version": str(version.get("Version")),
            "os": str(version.get("Os")),
            "security_options": security_options,
            "dedicated_container_vm": self.allow_rootful_dedicated_vm,
        }

    def _inspect_image(self, image: str) -> Mapping[str, object]:
        raw = self._run([self.executable, "image", "inspect", image]).stdout
        try:
            loaded = json.loads(raw)
            return loaded[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise SecurityPolicyError("Pinned image inspection failed") from exc

    def create(self, plan: ContainerPlan) -> ContainerHandle:
        completed = self._run(plan.docker_create_argv(self.executable), timeout=120.0)
        container_id = completed.stdout.decode("utf-8", errors="replace").strip()
        if not container_id:
            raise SecurityPolicyError("Container engine did not return a container id")
        handle = ContainerHandle(
            container_id=container_id,
            name=plan.name,
            job_id=plan.labels["shinka.job_id"],
            attempt_id=plan.labels["shinka.attempt_id"],
            role=plan.role,
        )
        try:
            self.verify_container(handle, plan)
        except Exception:
            # Creation is a side effect even when inspection detects policy
            # drift. Remove the exact labeled container before failing closed.
            try:
                self.remove(handle, force=True)
            except Exception:
                pass
            raise
        return handle

    def inspect(self, identifier: str) -> Mapping[str, object]:
        raw = self._run([self.executable, "inspect", identifier]).stdout
        try:
            loaded = json.loads(raw)
            return loaded[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise SecurityPolicyError("Container inspection failed") from exc

    def verify_container(self, handle: ContainerHandle, plan: ContainerPlan) -> None:
        data = self.inspect(handle.container_id)
        config = data.get("Config") or {}
        host = data.get("HostConfig") or {}
        labels = config.get("Labels") or {}
        if any(labels.get(key) != value for key, value in plan.labels.items()):
            raise SecurityPolicyError(
                "Container ownership labels differ from the launch plan"
            )
        if str(config.get("Image", "")) != plan.image:
            raise SecurityPolicyError(
                "Container image differs from the pinned launch plan"
            )
        if tuple(config.get("Cmd") or ()) != plan.command:
            raise SecurityPolicyError("Container command differs from the launch plan")
        if str(config.get("WorkingDir", "")) != plan.workdir:
            raise SecurityPolicyError(
                "Container working directory differs from the plan"
            )
        if host.get("Privileged"):
            raise SecurityPolicyError("Secure container was created privileged")
        if set(host.get("CapAdd") or []):
            raise SecurityPolicyError("Secure container unexpectedly adds capabilities")
        if "ALL" not in set(host.get("CapDrop") or []):
            raise SecurityPolicyError("Secure container does not drop all capabilities")
        security_options = set(host.get("SecurityOpt") or [])
        if not any(
            option.startswith("no-new-privileges") for option in security_options
        ):
            raise SecurityPolicyError("Secure container lacks no-new-privileges")
        if bool(host.get("ReadonlyRootfs")) != plan.read_only_root:
            raise SecurityPolicyError(
                "Container root filesystem policy differs from the plan"
            )
        if str(config.get("User", "")) != plan.user:
            raise SecurityPolicyError("Container user differs from the launch plan")
        expected_network = (
            "none" if plan.network is NetworkMode.DISABLED else plan.provider_network
        )
        if host.get("NetworkMode") != expected_network:
            raise SecurityPolicyError("Container network differs from the launch plan")
        if host.get("PidMode") == "host" or host.get("IpcMode") == "host":
            raise SecurityPolicyError("Container shares a host process namespace")
        if host.get("Devices") or host.get("DeviceRequests"):
            raise SecurityPolicyError("Secure container unexpectedly has device access")
        if int(host.get("PidsLimit") or 0) != plan.limits.pids:
            raise SecurityPolicyError(
                "Container PID limit differs from the launch plan"
            )
        if int(host.get("Memory") or 0) != plan.limits.memory_bytes:
            raise SecurityPolicyError(
                "Container memory limit differs from the launch plan"
            )
        expected_nano_cpus = int(plan.limits.cpus * 1_000_000_000)
        if int(host.get("NanoCpus") or 0) != expected_nano_cpus:
            raise SecurityPolicyError(
                "Container CPU limit differs from the launch plan"
            )
        expected_ulimit = {("nofile", plan.limits.open_files, plan.limits.open_files)}
        actual_ulimit = {
            (str(item.get("Name")), int(item.get("Soft", 0)), int(item.get("Hard", 0)))
            for item in host.get("Ulimits") or []
        }
        if actual_ulimit != expected_ulimit:
            raise SecurityPolicyError("Container open-file limit differs from the plan")
        if dict(host.get("Tmpfs") or {}) != dict(plan.tmpfs):
            raise SecurityPolicyError("Container tmpfs policy differs from the plan")

        actual_mounts = {
            (
                str(item.get("Source")),
                str(item.get("Destination")),
                not bool(item.get("RW")),
            )
            for item in data.get("Mounts") or []
            if item.get("Type") == "bind"
        }
        expected_mounts = {
            (str(mount.source), mount.target, mount.read_only) for mount in plan.mounts
        }
        if actual_mounts != expected_mounts or len(data.get("Mounts") or []) != len(
            expected_mounts
        ):
            raise SecurityPolicyError(
                "Container bind mounts differ from the launch plan"
            )

        environment_entries = config.get("Env") or []
        environment_names = {
            str(item).partition("=")[0] for item in environment_entries
        }
        allowed_sensitive = set(plan.allowed_environment_names)
        leaked = sorted(
            name
            for name in environment_names
            if _SENSITIVE_ENV.search(name) and name not in allowed_sensitive
        )
        if leaked:
            raise SecurityPolicyError(
                f"Container image or launch plan exposes unexpected credential variables: {leaked}"
            )

    def copy_into(
        self, handle: ContainerHandle, source: Path | str, target: str
    ) -> None:
        source_path = Path(source).resolve()
        if not source_path.exists() or source_path.is_symlink():
            raise SecurityPolicyError("Container copy source is invalid")
        target_path = PurePosixPath(target)
        if not target_path.is_absolute() or ".." in target_path.parts:
            raise SecurityPolicyError("Container copy target is invalid")
        self._run(
            [
                self.executable,
                "cp",
                str(source_path),
                f"{handle.container_id}:{target}",
            ],
            timeout=300.0,
        )

    def copy_archive_from(
        self,
        handle: ContainerHandle,
        source: str,
        destination: Path | str,
        *,
        max_bytes: int,
    ) -> Path:
        source_path = PurePosixPath(source)
        if not source_path.is_absolute() or ".." in source_path.parts:
            raise SecurityPolicyError("Container collection source is invalid")
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                self.executable,
                "cp",
                f"{handle.container_id}:{source}/.",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        written = 0
        try:
            with destination_path.open("xb") as output:
                for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > max_bytes:
                        process.kill()
                        raise SecurityPolicyError(
                            "Container output archive exceeds its limit"
                        )
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            stderr = process.stderr.read() if process.stderr is not None else b""
            if process.wait(timeout=60.0) != 0:
                raise SecureExecutionError(
                    FailureClass.COLLECTION_TIMEOUT,
                    "Container output could not be collected",
                    private_diagnostic=stderr.decode("utf-8", errors="replace")[-4000:],
                )
            return destination_path
        except Exception:
            destination_path.unlink(missing_ok=True)
            if process.poll() is None:
                process.kill()
                process.wait()
            raise

    @staticmethod
    def _bounded_reader(
        stream: BinaryIO,
        output: bytearray,
        *,
        max_bytes: int,
        exceeded: threading.Event,
        total: list[int],
        lock: threading.Lock,
    ) -> None:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            with lock:
                remaining = max_bytes - total[0]
                if remaining <= 0:
                    exceeded.set()
                    break
                accepted = chunk[:remaining]
                output.extend(accepted)
                total[0] += len(accepted)
                if len(chunk) > remaining:
                    exceeded.set()
                    break

    def run_capture(
        self,
        handle: ContainerHandle,
        *,
        timeout_seconds: float | None,
        max_output_bytes: int,
        stdin_data: bytes | None = None,
    ) -> ContainerResult:
        stdout = bytearray()
        stderr = bytearray()
        exceeded = threading.Event()
        total = [0]
        output_lock = threading.Lock()
        start_argv = [self.executable, "start", "--attach"]
        if stdin_data is not None:
            start_argv.append("--interactive")
        start_argv.append(handle.container_id)
        process = subprocess.Popen(
            start_argv,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        threads = [
            threading.Thread(
                target=self._bounded_reader,
                args=(process.stdout, stdout),
                kwargs={
                    "max_bytes": max_output_bytes,
                    "exceeded": exceeded,
                    "total": total,
                    "lock": output_lock,
                },
                daemon=True,
            ),
            threading.Thread(
                target=self._bounded_reader,
                args=(process.stderr, stderr),
                kwargs={
                    "max_bytes": max_output_bytes,
                    "exceeded": exceeded,
                    "total": total,
                    "lock": output_lock,
                },
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        if stdin_data is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_data)
                process.stdin.close()
            except BrokenPipeError:
                pass
        deadline = (
            None if timeout_seconds is None else time.monotonic() + timeout_seconds
        )
        timed_out = False
        while process.poll() is None:
            if exceeded.is_set():
                self.stop(handle, timeout_seconds=10.0)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                self.stop(handle, timeout_seconds=10.0)
                break
            time.sleep(0.05)
        try:
            process.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=2.0)
        state = self.inspect(handle.container_id).get("State") or {}
        exit_code = int(state.get("ExitCode", process.returncode or 0))
        return ContainerResult(
            exit_code=exit_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            output_limited=exceeded.is_set(),
        )

    def stop(self, handle: ContainerHandle, *, timeout_seconds: float = 10.0) -> None:
        subprocess.run(
            [
                self.executable,
                "stop",
                "--time",
                str(max(1, int(timeout_seconds))),
                handle.container_id,
            ],
            capture_output=True,
            timeout=timeout_seconds + 10.0,
            check=False,
        )

    def remove(self, handle: ContainerHandle, *, force: bool = True) -> None:
        data = self.inspect(handle.container_id)
        labels = (data.get("Config") or {}).get("Labels") or {}
        exact = {
            "shinka.managed": "true",
            "shinka.job_id": handle.job_id,
            "shinka.attempt_id": handle.attempt_id,
            "shinka.role": handle.role,
        }
        if any(labels.get(key) != value for key, value in exact.items()):
            raise SecurityPolicyError(
                "Refusing to remove a container with mismatched ownership"
            )
        argv = [self.executable, "rm"]
        if force:
            argv.append("--force")
        argv.append(handle.container_id)
        self._run(argv, timeout=60.0)

    def list_managed(self) -> list[ContainerHandle]:
        completed = self._run(
            [
                self.executable,
                "ps",
                "--all",
                "--filter",
                "label=shinka.managed=true",
                "--format",
                "{{.ID}}",
            ]
        )
        handles: list[ContainerHandle] = []
        for identifier in completed.stdout.decode(
            "utf-8", errors="replace"
        ).splitlines():
            if not identifier.strip():
                continue
            data = self.inspect(identifier.strip())
            labels = (data.get("Config") or {}).get("Labels") or {}
            handles.append(
                ContainerHandle(
                    container_id=str(data.get("Id", identifier)),
                    name=str(data.get("Name", "")).lstrip("/"),
                    job_id=str(labels.get("shinka.job_id", "")),
                    attempt_id=str(labels.get("shinka.attempt_id", "")),
                    role=str(labels.get("shinka.role", "")),
                )
            )
        return handles
