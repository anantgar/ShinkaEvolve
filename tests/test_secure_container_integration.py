from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from shinka.secure.containers import ContainerMount, ContainerPlan, DockerEngine
from shinka.secure.contracts import NetworkMode, ResourceLimits
from shinka.secure.jobs import EvaluationJobStore
from shinka.secure.mutation import AgentSpec, run_agent_in_workspace


_UNIVERSAL_HEADLESS_AGENTS = (
    "antigravity",
    "claude",
    "codex",
    "cursor",
    "gemini",
    "opencode",
    "pi",
)


@pytest.mark.integration
def test_live_container_enforces_candidate_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qualify the exact runtime policy against an explicitly supplied image."""

    image = os.environ.get("SHINKA_SECURE_QUALIFICATION_IMAGE")
    if not image:
        pytest.skip(
            "set SHINKA_SECURE_QUALIFICATION_IMAGE to a pinned universal Headless image"
        )
    executable = os.environ.get("SHINKA_CONTAINER_EXECUTABLE", "docker")
    if shutil.which(executable) is None:
        pytest.skip("Docker-compatible CLI is unavailable")
    allow_rootful_dedicated_vm = os.environ.get(
        "SHINKA_SECURE_ALLOW_ROOTFUL_DEDICATED_VM", ""
    ).lower() in {"1", "true", "yes"}

    candidate = tmp_path / "candidate"
    evaluator = tmp_path / "evaluator-private"
    results = tmp_path / "results-private"
    state = tmp_path / "state-private"
    candidate.mkdir()
    evaluator.mkdir()
    results.mkdir()
    state.mkdir()
    (candidate / "public.txt").write_text("public\n", encoding="utf-8")
    sentinel = "SHINKA_HOST_SECRET_9e5c5f8c"
    (evaluator / sentinel).write_text(sentinel, encoding="utf-8")
    (results / sentinel).write_text(sentinel, encoding="utf-8")
    (state / sentinel).write_text(sentinel, encoding="utf-8")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", sentinel)

    suffix = uuid.uuid4().hex[:16]
    plan = ContainerPlan(
        name=f"shinka-qualification-{suffix}",
        image=image,
        command=(
            "/bin/sh",
            "-ceu",
            """
test "$(cat /candidate/public.txt)" = public
test ! -e "$1"
test ! -e "$2"
test ! -e "$3"
test ! -e /var/run/docker.sock
test ! -e /sys/class/net/eth0
test -z "${AWS_SECRET_ACCESS_KEY+x}"
! touch /candidate/forbidden
! touch /root-filesystem-forbidden
touch /tmp/allowed
sleep 300 &
printf secure-ok
""",
            "shinka-qualification",
            str(evaluator / sentinel),
            str(results / sentinel),
            str(state / sentinel),
        ),
        role="runtime",
        labels={
            "shinka.managed": "true",
            "shinka.job_id": f"qualification-{suffix}",
            "shinka.attempt_id": suffix,
            "shinka.role": "runtime",
        },
        limits=ResourceLimits(
            cpus=0.5,
            memory_bytes=64 * 1024 * 1024,
            pids=16,
            open_files=64,
        ),
        mounts=(ContainerMount(candidate, "/candidate", read_only=True),),
        workdir="/candidate",
    )

    engine = DockerEngine(
        executable,
        allow_rootful_dedicated_vm=allow_rootful_dedicated_vm,
    )
    engine.preflight(images=(image,), required_agents=_UNIVERSAL_HEADLESS_AGENTS)
    handle = engine.create(plan)
    try:
        result = engine.run_capture(
            handle,
            timeout_seconds=20.0,
            max_output_bytes=4096,
        )
        assert result.exit_code == 0
        assert result.stdout == b"secure-ok"
        assert sentinel.encode() not in result.stdout + result.stderr
        assert not (engine.inspect(handle.container_id).get("State") or {}).get(
            "Running"
        )
    finally:
        engine.remove(handle, force=True)

    assert handle.container_id not in {
        item.container_id for item in engine.list_managed()
    }


@pytest.mark.integration
def test_live_headless_mutation_hides_external_agent_tooling(
    tmp_path: Path,
) -> None:
    """Run the Shinka mutation adapter against the pinned harness image."""

    image = os.environ.get("SHINKA_SECURE_QUALIFICATION_IMAGE")
    if not image:
        pytest.skip(
            "set SHINKA_SECURE_QUALIFICATION_IMAGE to a pinned universal Headless image"
        )
    if shutil.which("docker") is None:
        pytest.skip("Docker-compatible CLI is unavailable")

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "input.txt").write_text("input\n", encoding="utf-8")
    auth = tmp_path / "auth" / ".codex"
    auth.mkdir(parents=True)
    (auth / "auth.json").write_text('{"token":"auth-secret"}', encoding="utf-8")
    session_home = tmp_path / "session-home"
    (session_home / ".codex" / "plugins" / "cache").mkdir(parents=True)
    (session_home / ".codex" / "plugins" / "cache" / "cloudflare.mcp.json").write_text(
        '{"command":"cloudflare-mcp"}', encoding="utf-8"
    )
    (session_home / ".codex" / "skills" / "github").mkdir(parents=True)
    (session_home / ".codex" / "skills" / "github" / "SKILL.md").write_text(
        "external skill", encoding="utf-8"
    )
    (session_home / ".codex" / "cache" / "codex_apps_tools").mkdir(parents=True)
    (session_home / ".codex" / "config.toml").write_text(
        "[mcp_servers.github]\ncommand = 'github-mcp'\n", encoding="utf-8"
    )
    harness_bin = session_home / ".local" / "bin"
    harness_bin.mkdir(parents=True)
    fake_headless = harness_bin / "headless"
    fake_headless.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "test \"$1\" = codex\n"
        "test -x /bin/sh\n"
        "test ! -e \"$HOME/.codex/plugins\"\n"
        "test ! -e \"$HOME/.codex/skills\"\n"
        "test ! -e \"$HOME/.codex/cache\"\n"
        "test ! -e \"$HOME/.codex/config.toml\"\n"
        "printf 'harness-terminal-ok\\n' > /workspace/harness-proof.txt\n"
        "printf '{\"usage\":{\"inputTokens\":1,\"outputTokens\":1}}\\n'\n",
        encoding="utf-8",
    )
    fake_headless.chmod(0o755)
    state = tmp_path / "state"
    store = EvaluationJobStore(state / "jobs.sqlite")
    engine = DockerEngine("docker", allow_rootful_dedicated_vm=False)
    engine.preflight(images=(image,), required_agents=("codex",))

    run_agent_in_workspace(
        engine=engine,
        workspace=workspace,
        image=image,
        limits=ResourceLimits(
            cpus=0.5,
            memory_bytes=128 * 1024 * 1024,
            pids=16,
            open_files=64,
            output_bytes=4096,
        ),
        network=NetworkMode.DISABLED,
        provider_network=None,
        provider_proxy=None,
        sandbox_user="65532:65532",
        prompt="write a harness proof",
        agent=AgentSpec(agent="codex"),
        auth_profile=auth.parent,
        credential_environment={},
        job_id="integration-job",
        attempt_id="integration-headless-tooling",
        parent_digest="sha256:" + "3" * 64,
        mutation_store=store,
        timeout_seconds=20,
        session_home=session_home,
        session_name="integration-session",
        shared_cache_root=tmp_path / "shared-caches",
    )

    assert (workspace / "harness-proof.txt").read_text(encoding="utf-8") == (
        "harness-terminal-ok\n"
    )
    assert not (session_home / ".codex" / "plugins").exists()
    assert not (session_home / ".codex" / "skills").exists()
    assert not (session_home / ".codex" / "cache").exists()
    assert not (session_home / ".codex" / "config.toml").exists()
