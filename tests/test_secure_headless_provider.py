from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shinka.llm.providers import headless
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.dependencies import (
    DependencyArtifact,
    DependencyManifest,
    DependencyPreparer,
)

IMAGE = "example.invalid/headless@sha256:" + "b" * 64


class _FakeDockerEngine:
    preflights: list[dict] = []

    def __init__(self, executable: str, *, allow_rootful_dedicated_vm: bool):
        self.executable = executable
        self.allow_rootful_dedicated_vm = allow_rootful_dedicated_vm

    def preflight(self, **kwargs):
        self.preflights.append(kwargs)
        return {}


def test_secure_headless_reuses_session_home_without_persisting_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "candidate"
    control = worktree / ".shinka"
    control.mkdir(parents=True)
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / ".codex").mkdir()
    (auth / ".codex" / "auth.json").write_text(
        '{"token":"auth-secret-value"}', encoding="utf-8"
    )
    session_home = tmp_path / "session-home"
    session_home.mkdir()
    state_root = tmp_path / "state"
    artifact_store = ContentAddressedStore(state_root / "artifacts")
    dependency_file = tmp_path / "dependency.whl"
    dependency_file.write_bytes(b"offline dependency")
    dependency_bundle = DependencyPreparer(artifact_store).prepare(
        DependencyManifest(
            artifacts=(
                DependencyArtifact(
                    name="dependency.whl",
                    url=dependency_file.as_uri(),
                    sha256=hashlib.sha256(dependency_file.read_bytes()).hexdigest(),
                    size=dependency_file.stat().st_size,
                ),
            )
        )
    )
    parent, _metadata = artifact_store.put_tree(
        worktree,
        kind="candidate",
        excludes=headless.DEFAULT_EXCLUDES,
    )
    calls: list[dict] = []
    dependency_contents: list[bytes] = []

    def fake_agent(**kwargs):
        calls.append(kwargs)
        dependency_root = kwargs["dependency_root"]
        assert dependency_root is not None
        dependency_contents.append(
            (dependency_root / "files" / "dependency.whl").read_bytes()
        )
        attempt_id = kwargs["attempt_id"]
        store = kwargs["mutation_store"]
        store.prepare_mutation(
            attempt_id=attempt_id,
            job_id=kwargs["job_id"],
            parent_digest=kwargs["parent_digest"],
            prompt_digest="sha256:" + "1" * 64,
            image=kwargs["image"],
            agent=kwargs["agent"].agent,
            model=kwargs["agent"].model,
            container_name=f"fake-{attempt_id}",
        )
        store.record_mutation_launch(
            attempt_id,
            container_id=f"container-{attempt_id}",
        )
        counter_path = kwargs["session_home"] / "turns"
        turns = int(counter_path.read_text()) + 1 if counter_path.exists() else 1
        counter_path.write_text(str(turns), encoding="utf-8")
        (kwargs["workspace"] / "generated.txt").write_text(
            f"turn {turns}\n", encoding="utf-8"
        )
        store.mark_mutation_output_pending(attempt_id)
        store.mark_mutation_cleaned(attempt_id)
        return SimpleNamespace(
            stdout=b'{"usage":{"inputTokens":2,"outputTokens":1},"log":"fake-secret auth-secret-value"}\n',
            stderr=b"stderr fake-secret auth-secret-value",
            container_name=f"fake-{turns}",
        )

    monkeypatch.setattr(headless, "DockerEngine", _FakeDockerEngine)
    monkeypatch.setattr(headless, "run_agent_in_workspace", fake_agent)
    common = {
        "headless_secure": True,
        "headless_work_dir": str(worktree),
        "headless_mutation_image": IMAGE,
        "headless_auth_profiles": {"codex": str(auth)},
        "headless_credentials": {"codex": {"OPENAI_API_KEY": "fake-secret"}},
        "headless_network": "disabled",
        "headless_resource_limits": {
            "cpus": 1,
            "memory_bytes": 128 * 1024 * 1024,
            "pids": 16,
            "open_files": 64,
            "output_bytes": 4096,
        },
        "headless_jobs_db": str(state_root / "jobs.sqlite"),
        "headless_parent_digest": parent.digest,
        "headless_job_id": "proposal",
        "headless_session_name": "proposal-session",
        "headless_session_home": str(session_home),
        "headless_session_key": "public-home-key",
        "headless_shared_cache_root": str(tmp_path / "shared-caches"),
        "headless_timeout_seconds": 10,
        "headless_mutation_dependency_artifact": {
            "digest": dependency_bundle.artifact.digest,
            "size": dependency_bundle.artifact.size,
            "kind": dependency_bundle.artifact.kind,
            "media_type": dependency_bundle.artifact.media_type,
        },
    }

    overlapping_home = state_root / "session-home"
    overlapping_home.mkdir()
    with pytest.raises(headless.LLMProcessError, match="state and session"):
        headless.query_headless(
            None,
            "headless/codex@test",
            "mutate",
            "system",
            [],
            None,
            headless_attempt_id="overlap-attempt",
            **{**common, "headless_session_home": str(overlapping_home)},
        )

    for attempt in ("attempt-1", "attempt-2"):
        parent, _metadata = artifact_store.put_tree(
            worktree,
            kind="candidate",
            excludes=headless.DEFAULT_EXCLUDES,
        )
        common["headless_parent_digest"] = parent.digest
        result = headless.query_headless(
            None,
            "headless/codex@test",
            "mutate fake-secret auth-secret-value",
            "system fake-secret auth-secret-value",
            [],
            None,
            headless_attempt_id=attempt,
            **common,
        )
        serialized = json.dumps(result.to_dict(), sort_keys=True)
        assert "fake-secret" not in serialized
        assert str(auth) not in serialized
        assert str(session_home) not in serialized
        assert "jobs.sqlite" not in serialized
        stdout_path = Path(result.kwargs["headless_stdout_path"])
        stderr_path = Path(result.kwargs["headless_stderr_path"])
        prompt_path = Path(result.kwargs["headless_prompt_path"])
        assert worktree not in stdout_path.parents
        assert worktree not in stderr_path.parents
        assert worktree not in prompt_path.parents
        assert "fake-secret" not in stdout_path.read_text()
        assert "fake-secret" not in stderr_path.read_text()
        assert "auth-secret-value" not in stdout_path.read_text()
        assert "auth-secret-value" not in stderr_path.read_text()
        assert "fake-secret" not in prompt_path.read_text()
        assert "auth-secret-value" not in prompt_path.read_text()
        assert result.kwargs["headless_secure"] is True
        assert result.kwargs["headless_session_name"] == "proposal-session"
        assert result.kwargs["headless_candidate_digest"].startswith("sha256:")

    assert (session_home / "turns").read_text() == "2"
    assert [call["session_name"] for call in calls] == [
        "proposal-session",
        "proposal-session",
    ]
    assert all(str(worktree) not in call["prompt"] for call in calls)
    assert all(
        "The proposal repository is `/workspace`" in call["prompt"] for call in calls
    )
    assert dependency_contents == [b"offline dependency", b"offline dependency"]
    assert all(
        call["shared_cache_root"] == tmp_path / "shared-caches" for call in calls
    )
    assert (worktree / "generated.txt").read_text() == "turn 2\n"
