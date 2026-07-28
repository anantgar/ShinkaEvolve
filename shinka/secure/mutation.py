"""Candidate-only synthetic repositories and hardened agent mutation."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .archive import ArchiveLimits, DEFAULT_EXCLUDES, create_normalized_archive
from .artifacts import ContentAddressedStore
from .canonical import canonical_json_bytes, sha256_bytes
from .contracts import (
    ArtifactRef,
    NetworkMode,
    PublicTaskContract,
    ResourceLimits,
    validate_pinned_image,
)
from .containers import (
    ContainerHandle,
    ContainerMount,
    ContainerPlan,
    DockerEngine,
    normalize_candidate_permissions,
    prepare_bind_source,
)
from .errors import (
    FailureClass,
    SecureExecutionError,
    SecurityPolicyError,
    SnapshotError,
)
from .jobs import EvaluationJobStore

_AGENT_AUTH_PATHS: dict[str, tuple[str, ...]] = {
    "antigravity": (
        ".gemini/antigravity-cli/antigravity-oauth-token",
        ".gemini/antigravity-cli/settings.json",
        ".gemini/antigravity-cli/installation_id",
        ".gemini/antigravity-cli/jetski_state.pbtxt",
        ".gemini/antigravity-cli/cache/onboarding.json",
    ),
    "claude": (
        ".claude.json",
        ".claude/settings.json",
        ".claude/.credentials.json",
        ".claude/auth.json",
    ),
    "codex": (".codex/auth.json", ".codex/config.toml"),
    "cursor": (".cursor/cli-config.json",),
    "gemini": (
        ".gemini/google_accounts.json",
        ".gemini/settings.json",
        ".gemini/state.json",
        ".gemini/trustedFolders.json",
        ".gemini/installation_id",
    ),
    "opencode": (".config/opencode",),
    "pi": (".pi/agent/auth.json", ".pi/agent/settings.json"),
}

_AGENT_CREDENTIAL_ENV: dict[str, frozenset[str]] = {
    "antigravity": frozenset(),
    "claude": frozenset({"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "codex": frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"}),
    "cursor": frozenset({"CURSOR_API_KEY"}),
    "gemini": frozenset({"GOOGLE_API_KEY", "GEMINI_API_KEY"}),
    "opencode": frozenset(
        {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
        }
    ),
    "pi": frozenset(
        {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "PI_CODING_AGENT_API_KEY",
            "PI_CODING_AGENT_PROVIDER",
            "PI_CODING_AGENT_MODEL",
            "PI_CODING_AGENT_MODELS",
        }
    ),
}

_AGENT_REQUIRED_AUTH_PATHS: dict[str, tuple[str, ...]] = {
    "antigravity": (".gemini/antigravity-cli/antigravity-oauth-token",),
}

# These files are copied into the container's HOME so the selected CLI can
# authenticate. They must not remain in a durable proposal session home after
# the turn; the auth profile is recopied on the next turn instead.
_AGENT_PERSISTED_CREDENTIAL_PATHS: dict[str, tuple[str, ...]] = {
    "antigravity": (".gemini/antigravity-cli/antigravity-oauth-token",),
    "claude": (".claude.json", ".claude/.credentials.json", ".claude/auth.json"),
    "codex": (".codex/auth.json",),
    "cursor": (".cursor/cli-config.json",),
    "gemini": (".gemini/google_accounts.json",),
    "opencode": (".config/opencode",),
    "pi": (".pi/agent/auth.json",),
}

_OPAQUE_JSON_AUTH_FILES = frozenset({"antigravity-oauth-token"})
_SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class AgentSpec:
    agent: str
    model: str | None = None
    effort: str | None = None

    def __post_init__(self) -> None:
        if self.agent not in _AGENT_AUTH_PATHS:
            raise SecurityPolicyError(
                f"Unsupported secure mutation agent: {self.agent}"
            )
        if self.effort is not None and self.effort not in {
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise SecurityPolicyError("Unsupported agent reasoning effort")


@dataclass(frozen=True)
class MutationResult:
    candidate: ArtifactRef
    changed_files: tuple[str, ...]
    stdout: bytes
    stderr: bytes
    container_name: str


@dataclass(frozen=True)
class WorkspaceAgentResult:
    stdout: bytes
    stderr: bytes
    container_name: str


def _git(
    workspace: Path, args: Sequence[str], *, env: Mapping[str, str] | None = None
) -> str:
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "HOME": "/nonexistent-shinka-home",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    safe_env.update(env or {})
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        env=safe_env,
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if completed.returncode != 0:
        raise SnapshotError(
            "Synthetic candidate repository could not be prepared",
            private_diagnostic=(completed.stderr or completed.stdout)[-4000:],
        )
    return completed.stdout


def initialize_synthetic_repository(workspace: Path) -> str:
    """Create a one-commit repository with no remotes, alternates, or history."""

    if (workspace / ".git").exists():
        raise SnapshotError("Candidate snapshot unexpectedly contains Git internals")
    _git(workspace, ["init", "--initial-branch=shinka"])
    _git(workspace, ["config", "core.hooksPath", "/dev/null"])
    _git(workspace, ["config", "protocol.file.allow", "never"])
    _git(workspace, ["config", "fetch.recurseSubmodules", "false"])
    info = workspace / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "exclude").write_text(".shinka/\n", encoding="utf-8")
    _git(workspace, ["add", "-A", "--"])
    commit_env = {
        "GIT_AUTHOR_NAME": "Shinka",
        "GIT_AUTHOR_EMAIL": "shinka@example.invalid",
        "GIT_COMMITTER_NAME": "Shinka",
        "GIT_COMMITTER_EMAIL": "shinka@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    }
    _git(
        workspace,
        [
            "commit",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            "sanitized candidate root",
        ],
        env=commit_env,
    )
    return _git(workspace, ["rev-parse", "HEAD"]).strip()


def _archive_manifest(archive_path: Path) -> dict[str, tuple[str, int, str]]:
    import tarfile

    manifest: dict[str, tuple[str, int, str]] = {}
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            mode = member.mode & 0o777
            if member.isdir():
                manifest[member.name] = ("directory", mode, "")
            elif member.issym():
                manifest[member.name] = ("symlink", mode, member.linkname)
            elif member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise SnapshotError("Candidate archive contains a truncated file")
                digest = hashlib.sha256()
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                manifest[member.name] = ("file", mode, digest.hexdigest())
            else:
                raise SnapshotError("Candidate archive contains an unsupported entry")
    return manifest


def _matches_mutable(path: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip("/")
        if (
            path == normalized
            or path.startswith(f"{normalized}/")
            or fnmatch.fnmatchcase(path, normalized)
        ):
            return True
    return False


def validate_candidate_change(
    parent_archive: Path,
    candidate_archive: Path,
    contract: PublicTaskContract,
) -> tuple[str, ...]:
    parent = _archive_manifest(parent_archive)
    candidate = _archive_manifest(candidate_archive)
    changed = sorted(
        path
        for path in set(parent) | set(candidate)
        if parent.get(path) != candidate.get(path)
    )
    violations = [
        path for path in changed if not _matches_mutable(path, contract.mutable_paths)
    ]
    if violations:
        raise SecurityPolicyError(
            f"Candidate changed paths outside the public mutation policy: {violations}"
        )
    missing = [
        path
        for path in contract.required_paths
        if path not in candidate
        and not any(item.startswith(f"{path}/") for item in candidate)
    ]
    if missing:
        raise SecurityPolicyError(f"Candidate is missing required paths: {missing}")
    return tuple(changed)


def _agent_command(
    spec: AgentSpec,
    prompt: str,
    *,
    session_name: str | None = None,
) -> tuple[tuple[str, ...], bytes | None]:
    args = ["headless", spec.agent]
    if spec.model:
        args.extend(["--model", spec.model])
    if spec.effort:
        args.extend(["--reasoning-effort", spec.effort])
    if session_name:
        if not _SESSION_NAME.fullmatch(session_name):
            raise SecurityPolicyError("Invalid Headless session name")
        args.extend(["--session", session_name])
    args.extend(["--allow", "yolo", "--json"])
    return tuple(args), prompt.encode("utf-8")


def _copy_minimal_auth_profile(source: Path, destination: Path, agent: str) -> None:
    unresolved = source.expanduser()
    if unresolved.is_symlink():
        raise SecurityPolicyError("Agent auth profile cannot be a symlink")
    source = unresolved.resolve()
    if not source.is_dir():
        raise SecurityPolicyError("Agent auth profile must be a real directory")
    destination.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="shinka-auth-snapshot-") as temporary:
        archive = Path(temporary) / "auth.tar"
        create_normalized_archive(
            source,
            archive,
            excludes=(".git", ".git/**", ".shinka", ".shinka/**"),
            includes=_AGENT_AUTH_PATHS[agent],
            limits=ArchiveLimits(
                max_entries=256,
                max_total_bytes=16 * 1024 * 1024,
                max_file_bytes=8 * 1024 * 1024,
            ),
        )
        from .archive import extract_normalized_archive

        extract_normalized_archive(archive, destination)
    for relative in _AGENT_REQUIRED_AUTH_PATHS.get(agent, ()):
        required = destination / relative
        if required.is_symlink() or not required.is_file():
            raise SecurityPolicyError(
                f"Minimal {agent} auth profile is missing {relative}"
            )


def _redact(data: bytes, secrets: Sequence[str]) -> bytes:
    redacted = data
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret.encode("utf-8"), b"[REDACTED]")
    return redacted


def _auth_secret_values(root: Path) -> list[str]:
    """Collect exact auth values for transcript redaction (not as a DLP boundary)."""

    values: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, str) and len(value) >= 8:
            values.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for path in root.rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > 8 * 1024 * 1024
        ):
            continue
        try:
            if path.name in _OPAQUE_JSON_AUTH_FILES:
                raw = path.read_text(encoding="utf-8").strip()
                if len(raw) >= 8:
                    values.add(raw)
            elif path.suffix.lower() == ".json":
                import json

                visit(json.loads(path.read_text(encoding="utf-8")))
            elif path.suffix.lower() == ".toml":
                import tomllib

                visit(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError):
            continue
    return sorted(values, key=len, reverse=True)


_SECRET_FIELD = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|credential|password|secret)",
    re.IGNORECASE,
)


def _auth_sensitive_values(root: Path) -> list[str]:
    """Collect exact credential-like values for a bounded post-run leak scan."""

    values: set[str] = set()

    def visit(value: object, key: str = "") -> None:
        if isinstance(value, str):
            if len(value) >= 12 and _SECRET_FIELD.search(key):
                values.add(value)
        elif isinstance(value, dict):
            for child_key, item in value.items():
                visit(item, str(child_key))
        elif isinstance(value, list):
            for item in value:
                visit(item, key)

    for path in root.rglob("*"):
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 8 * 1024 * 1024
            ):
                continue
            if path.name in _OPAQUE_JSON_AUTH_FILES:
                raw = path.read_text(encoding="utf-8").strip()
                if len(raw) >= 8:
                    values.add(raw)
            elif path.suffix.lower() == ".json":
                import json

                visit(json.loads(path.read_text(encoding="utf-8")))
            elif path.suffix.lower() == ".toml":
                import tomllib

                visit(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError):
            continue
    return sorted(values, key=len, reverse=True)


def _reject_exact_secret_copies(
    workspace: Path,
    *,
    credential_environment: Mapping[str, str],
    auth_root: Path,
    label: str = "Mutation output",
    include_all_credential_values: bool = False,
) -> None:
    if include_all_credential_values:
        credential_values = (
            value for value in credential_environment.values() if len(value) >= 8
        )
        auth_values = _auth_secret_values(auth_root)
    else:
        credential_values = (
            value
            for name, value in credential_environment.items()
            if len(value) >= 12 and _SECRET_FIELD.search(name)
        )
        auth_values = _auth_sensitive_values(auth_root)
    secrets = {value.encode("utf-8") for value in credential_values}
    secrets.update(value.encode("utf-8") for value in auth_values)
    if not secrets:
        return
    for path in workspace.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as source:
                carry = b""
                max_secret = max(len(secret) for secret in secrets)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    data = carry + chunk
                    if any(secret in data for secret in secrets):
                        relative = path.relative_to(workspace).as_posix()
                        raise SecurityPolicyError(
                            f"{label} copied an authentication secret into {relative}"
                        )
                    carry = data[-max_secret:]
        except SecurityPolicyError:
            raise
        except OSError as exc:
            raise SecurityPolicyError(
                "Mutation output could not be scanned for credential copies"
            ) from exc


def _purge_directory_contents(root: Path) -> None:
    """Delete all contents of a proposal-scoped directory after a secret leak."""

    if root.is_symlink() or not root.is_dir():
        raise SecurityPolicyError("Secret-leak cleanup target must be a real directory")
    for child in root.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise SecurityPolicyError(
                "Secret-leak cleanup found an unsafe filesystem entry"
            )


def _remove_persisted_credentials(home: Path, agent: str | None = None) -> None:
    """Remove copied credential material while retaining other session state."""

    agents = (agent,) if agent is not None else _AGENT_PERSISTED_CREDENTIAL_PATHS
    paths: set[Path] = set()
    for selected_agent in agents:
        paths.update(
            home / relative
            for relative in _AGENT_PERSISTED_CREDENTIAL_PATHS.get(selected_agent, ())
        )
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)


def run_agent_in_workspace(
    *,
    engine: DockerEngine,
    workspace: Path,
    image: str,
    limits: ResourceLimits,
    network: NetworkMode,
    provider_network: str | None,
    provider_proxy: str | None,
    sandbox_user: str,
    prompt: str,
    agent: AgentSpec,
    auth_profile: Path,
    credential_environment: Mapping[str, str],
    job_id: str,
    attempt_id: str,
    parent_digest: str,
    mutation_store: EvaluationJobStore,
    timeout_seconds: float,
    session_home: Path | None = None,
    session_name: str | None = None,
) -> WorkspaceAgentResult:
    """Run one agent against an already-sanitized synthetic repository."""

    workspace = workspace.resolve()
    if sandbox_user in {"0", "0:0", "root"}:
        sandbox_user = "65532:65532"
    if not (workspace / ".git").is_dir():
        raise SecurityPolicyError(
            "Mutation workspace must be a synthetic Git repository"
        )
    auth_source = Path(auth_profile).expanduser()
    if auth_source.is_symlink() or not auth_source.is_dir():
        raise SecurityPolicyError("Agent auth profile must be a real directory")
    auth_source = auth_source.resolve()

    def _overlaps(left: Path, right: Path) -> bool:
        try:
            left.relative_to(right)
            return True
        except ValueError:
            pass
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False

    if _overlaps(auth_source, workspace):
        raise SecurityPolicyError(
            "Agent auth profile must be outside the mutation workspace"
        )
    resolved_session_home: Path | None = None
    if session_home is not None:
        unresolved_session_home = Path(session_home).expanduser()
        if unresolved_session_home.is_symlink() or not unresolved_session_home.is_dir():
            raise SecurityPolicyError("Headless session home must be a real directory")
        resolved_session_home = unresolved_session_home.resolve()
        if _overlaps(resolved_session_home, workspace):
            raise SecurityPolicyError(
                "Headless session home must be outside the mutation workspace"
            )
        if _overlaps(auth_source, resolved_session_home):
            raise SecurityPolicyError(
                "Agent auth profile must be outside the Headless session home"
            )
        mutation_state_root = Path(mutation_store.path).expanduser().resolve().parent
        if _overlaps(mutation_state_root, resolved_session_home):
            raise SecurityPolicyError(
                "Headless session home must be outside secure mutation state"
            )
    permitted_credentials = _AGENT_CREDENTIAL_ENV[agent.agent]
    unexpected = sorted(set(credential_environment) - permitted_credentials)
    if unexpected:
        raise SecurityPolicyError(
            f"Credential environment contains variables unrelated to {agent.agent}: {unexpected}"
        )
    name = f"shinka-mutation-{attempt_id.replace('-', '')[:16]}"
    pinned_image = validate_pinned_image(image)
    prompt_digest = sha256_bytes(prompt.encode("utf-8"))
    mutation_store.prepare_mutation(
        attempt_id=attempt_id,
        job_id=job_id,
        parent_digest=parent_digest,
        prompt_digest=prompt_digest,
        image=pinned_image,
        agent=agent.agent,
        model=agent.model,
        container_name=name,
    )
    handle: ContainerHandle | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="shinka-agent-auth-") as auth_temp:
            auth = Path(auth_temp) / "profile"
            _copy_minimal_auth_profile(auth_profile, auth, agent.agent)
            prepare_bind_source(workspace, writable=True)
            prepare_bind_source(auth, writable=False)
            if resolved_session_home is not None:
                # Remove known auth files and reject exact copies left by an
                # earlier turn before the durable home is exposed to the agent.
                _remove_persisted_credentials(resolved_session_home)
                try:
                    _reject_exact_secret_copies(
                        resolved_session_home,
                        credential_environment=credential_environment,
                        auth_root=auth,
                        label="Headless session home",
                        include_all_credential_values=True,
                    )
                except SecurityPolicyError:
                    _purge_directory_contents(resolved_session_home)
                    raise
                prepare_bind_source(resolved_session_home, writable=True)
            redaction_values = [
                *credential_environment.values(),
                *_auth_secret_values(auth),
            ]
            agent_argv, stdin_data = _agent_command(
                agent,
                prompt,
                session_name=session_name,
            )
            bootstrap = (
                'set -eu; mkdir -p "$HOME"; '
                'if [ -d /auth-seed ]; then cp -R /auth-seed/. "$HOME"/; '
                'find "$HOME" -mindepth 1 -exec chmod u+rwX {} +; fi; '
                'exec "$@"'
            )
            command = ("sh", "-c", bootstrap, "sh", *agent_argv)
            environment = {"HOME": "/headless-home", **credential_environment}
            if agent.agent == "antigravity":
                environment["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
            mounts = [
                ContainerMount(workspace, "/workspace", read_only=False),
                ContainerMount(auth, "/auth-seed", read_only=True),
            ]
            if resolved_session_home is not None:
                mounts.append(
                    ContainerMount(
                        resolved_session_home,
                        "/headless-home",
                        read_only=False,
                    )
                )
            tmpfs = {
                "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
                "/run": "rw,nosuid,nodev,noexec,size=16m,mode=755",
            }
            if resolved_session_home is None:
                tmpfs["/headless-home"] = "rw,nosuid,nodev,size=64m,mode=1777"
            plan = ContainerPlan(
                name=name,
                image=pinned_image,
                command=command,
                role="mutation",
                labels={
                    "shinka.managed": "true",
                    "shinka.job_id": job_id,
                    "shinka.attempt_id": attempt_id,
                    "shinka.role": "mutation",
                },
                limits=limits,
                mounts=tuple(mounts),
                environment=environment,
                allowed_environment_names=frozenset(environment),
                workdir="/workspace",
                user=sandbox_user,
                network=network,
                provider_network=provider_network,
                provider_proxy=provider_proxy,
                read_only_root=True,
                tmpfs=tmpfs,
                stdin_open=stdin_data is not None,
            )
            handle = engine.create(plan)
            mutation_store.record_mutation_launch(
                attempt_id, container_id=handle.container_id
            )
            result = engine.run_capture(
                handle,
                timeout_seconds=timeout_seconds,
                max_output_bytes=limits.output_bytes,
                stdin_data=stdin_data,
            )
            if result.timed_out:
                raise SecureExecutionError(
                    FailureClass.WALL_TIMEOUT,
                    "Mutation exceeded its explicit wall timeout",
                )
            if result.output_limited:
                raise SecureExecutionError(
                    FailureClass.MUTATION_FAILED,
                    "Mutation exceeded its output limit",
                )
            if result.exit_code != 0:
                raise SecureExecutionError(
                    FailureClass.MUTATION_FAILED,
                    "Mutation agent did not complete successfully",
                    private_diagnostic=result.stderr.decode("utf-8", errors="replace")[
                        -4000:
                    ],
                )
            leak_error: SecurityPolicyError | None = None
            try:
                _reject_exact_secret_copies(
                    workspace,
                    credential_environment=credential_environment,
                    auth_root=auth,
                )
            except SecurityPolicyError as exc:
                leak_error = exc
            if resolved_session_home is not None:
                try:
                    _reject_exact_secret_copies(
                        resolved_session_home,
                        credential_environment=credential_environment,
                        auth_root=auth,
                        label="Headless session home",
                        include_all_credential_values=True,
                    )
                except SecurityPolicyError as exc:
                    # A durable session home is reused by later agent turns. If
                    # any credential value was copied there, discard all of its
                    # contents rather than preserving an unknown stolen copy.
                    _purge_directory_contents(resolved_session_home)
                    leak_error = leak_error or exc
            if leak_error is not None:
                raise leak_error
            mutation_store.mark_mutation_output_pending(attempt_id)
            return WorkspaceAgentResult(
                stdout=_redact(result.stdout, redaction_values),
                stderr=_redact(result.stderr, redaction_values),
                container_name=name,
            )
    except SecureExecutionError as exc:
        mutation_store.fail_mutation(attempt_id, failure_class=exc.failure_class)
        raise
    except Exception:
        mutation_store.fail_mutation(
            attempt_id, failure_class=FailureClass.MUTATION_FAILED
        )
        raise
    finally:
        cleanup_error: Exception | None = None
        try:
            if handle is not None:
                engine.remove(handle, force=True)
        except Exception as exc:
            cleanup_error = exc
        try:
            mutation_store.mark_mutation_cleaned(attempt_id)
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        try:
            if resolved_session_home is not None:
                _remove_persisted_credentials(
                    resolved_session_home
                )
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        try:
            normalize_candidate_permissions(workspace)
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            try:
                mutation_store.fail_mutation(
                    attempt_id, failure_class=FailureClass.CLEANUP_FAILED
                )
            except Exception:
                pass
            raise SecureExecutionError(
                FailureClass.CLEANUP_FAILED,
                "Mutation cleanup failed",
                private_diagnostic=str(cleanup_error),
            ) from cleanup_error


class SecureMutationBackend:
    """Run a Headless-compatible agent image against only a candidate snapshot."""

    def __init__(
        self,
        *,
        engine: DockerEngine,
        artifacts: ContentAddressedStore,
        mutation_store: EvaluationJobStore,
        image: str,
        limits: ResourceLimits,
        network: NetworkMode,
        provider_network: str | None = None,
        provider_proxy: str | None = None,
        sandbox_user: str | None = None,
        archive_limits: ArchiveLimits = ArchiveLimits(),
    ) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.mutation_store = mutation_store
        self.image = validate_pinned_image(image)
        self.limits = limits
        self.network = network
        self.provider_network = provider_network
        self.provider_proxy = provider_proxy
        self.sandbox_user = sandbox_user or f"{os.getuid()}:{os.getgid()}"
        if self.sandbox_user in {"0", "0:0"}:
            self.sandbox_user = "65532:65532"
        self.archive_limits = archive_limits

    def mutate(
        self,
        *,
        parent: ArtifactRef,
        contract: PublicTaskContract,
        prompt: str,
        agent: AgentSpec,
        auth_profile: Path | str,
        credential_environment: Mapping[str, str],
        job_id: str,
        attempt_id: str,
        timeout_seconds: float,
        session_home: Path | str | None = None,
        session_name: str | None = None,
    ) -> MutationResult:
        if not prompt.strip():
            raise SecurityPolicyError("Mutation prompt cannot be empty")
        permitted_credentials = _AGENT_CREDENTIAL_ENV[agent.agent]
        unexpected = sorted(set(credential_environment) - permitted_credentials)
        if unexpected:
            raise SecurityPolicyError(
                f"Credential environment contains variables unrelated to {agent.agent}: {unexpected}"
            )

        with tempfile.TemporaryDirectory(prefix="shinka-mutation-") as temporary_name:
            temporary = Path(temporary_name)
            os.chmod(temporary, 0o700)
            workspace = temporary / "candidate"
            self.artifacts.materialize_archive(
                parent, workspace, limits=self.archive_limits
            )
            initialize_synthetic_repository(workspace)
            control = workspace / ".shinka"
            control.mkdir(mode=0o700, exist_ok=True)
            (control / "public-contract.json").write_bytes(
                canonical_json_bytes(
                    {
                        "schema_version": contract.schema_version,
                        "task_id": contract.task_id,
                        "objective": contract.objective,
                        "candidate_protocol": contract.candidate_protocol,
                        "mutable_paths": list(contract.mutable_paths),
                        "required_paths": list(contract.required_paths),
                    }
                )
            )
            (control / "mutation-policy.md").write_text(prompt, encoding="utf-8")
            agent_result = run_agent_in_workspace(
                engine=self.engine,
                workspace=workspace,
                image=self.image,
                limits=self.limits,
                network=self.network,
                provider_network=self.provider_network,
                provider_proxy=self.provider_proxy,
                sandbox_user=self.sandbox_user,
                prompt=prompt,
                agent=agent,
                auth_profile=Path(auth_profile),
                credential_environment=credential_environment,
                job_id=job_id,
                attempt_id=attempt_id,
                parent_digest=parent.digest,
                mutation_store=self.mutation_store,
                timeout_seconds=timeout_seconds,
                session_home=(Path(session_home) if session_home is not None else None),
                session_name=session_name,
            )
            try:
                candidate_archive = temporary / "candidate-output.tar"
                metadata = create_normalized_archive(
                    workspace,
                    candidate_archive,
                    excludes=DEFAULT_EXCLUDES,
                    limits=self.archive_limits,
                )
                parent_path = self.artifacts.verify(
                    parent.digest, expected_size=parent.size
                )
                changed = validate_candidate_change(
                    parent_path, candidate_archive, contract
                )
                reference = self.artifacts.put_file(
                    candidate_archive,
                    kind="candidate",
                    expected_digest=metadata.digest,
                )
                self.mutation_store.complete_mutation(
                    attempt_id, candidate_digest=reference.digest
                )
            except SecureExecutionError as exc:
                self.mutation_store.fail_mutation(
                    attempt_id, failure_class=exc.failure_class
                )
                raise
            except Exception:
                self.mutation_store.fail_mutation(
                    attempt_id, failure_class=FailureClass.MUTATION_FAILED
                )
                raise
            return MutationResult(
                candidate=reference,
                changed_files=changed,
                stdout=agent_result.stdout,
                stderr=agent_result.stderr,
                container_name=agent_result.container_name,
            )
