from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from shinka.llm.providers import headless
from shinka.secure.artifacts import ContentAddressedStore

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
    session_home = tmp_path / "session-home"
    session_home.mkdir()
    state_root = tmp_path / "state"
    artifact_store = ContentAddressedStore(state_root / "artifacts")
    parent, _metadata = artifact_store.put_tree(
        worktree,
        kind="candidate",
        excludes=headless.DEFAULT_EXCLUDES,
    )
    calls: list[dict] = []

    def fake_agent(**kwargs):
        calls.append(kwargs)
        counter_path = kwargs["session_home"] / "turns"
        turns = int(counter_path.read_text()) + 1 if counter_path.exists() else 1
        counter_path.write_text(str(turns), encoding="utf-8")
        (kwargs["workspace"] / "generated.txt").write_text(
            f"turn {turns}\n", encoding="utf-8"
        )
        return SimpleNamespace(
            stdout=b'{"usage":{"inputTokens":2,"outputTokens":1}}\n',
            stderr=b"",
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
        "headless_timeout_seconds": 10,
    }

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
            "mutate",
            "system",
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
        assert result.kwargs["headless_secure"] is True
        assert result.kwargs["headless_session_name"] == "proposal-session"

    assert (session_home / "turns").read_text() == "2"
    assert [call["session_name"] for call in calls] == [
        "proposal-session",
        "proposal-session",
    ]
    assert (worktree / "generated.txt").read_text() == "turn 2\n"
