from __future__ import annotations

import json
from pathlib import Path

import pytest

from shinka.secure.errors import ConfigurationError, SecurityPolicyError
from shinka.secure.sessions import ProposalSessionStore


def test_proposal_session_survives_store_restart_with_minimal_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sessions"
    first_store = ProposalSessionStore(root)
    first = first_store.get_or_create(
        "proposal-123",
        session_name="shinka-gen-1-proposal123",
    )
    home = first_store.home_path(first)
    (home / "opaque-agent-state").write_text("turn-1", encoding="utf-8")

    second_store = ProposalSessionStore(root)
    second = second_store.get_or_create(
        "proposal-123",
        session_name="shinka-gen-1-proposal123",
    )

    assert second == first
    assert (
        second_store.home_path(second) / "opaque-agent-state"
    ).read_text() == "turn-1"
    metadata = json.loads(next((root / "metadata").glob("*.json")).read_text())
    assert set(metadata) == {
        "created_at",
        "home_key",
        "proposal_id",
        "schema_version",
        "session_name",
    }
    assert "token" not in json.dumps(metadata).lower()
    assert "credential" not in json.dumps(metadata).lower()


def test_proposal_session_rejects_conflicting_name(tmp_path: Path) -> None:
    store = ProposalSessionStore(tmp_path / "sessions")
    store.get_or_create("proposal", session_name="stable-name")
    with pytest.raises(ConfigurationError, match="conflicts"):
        store.get_or_create("proposal", session_name="different-name")


def test_proposal_session_rejects_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SecurityPolicyError, match="symlink"):
        ProposalSessionStore(link)
