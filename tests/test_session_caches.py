from __future__ import annotations

from pathlib import Path

import pytest

from shinka.secure.errors import SecurityPolicyError
from shinka.secure.session_caches import (
    SharedSessionCacheStore,
    shared_cache_paths,
)


IMAGE = "example.invalid/headless@sha256:" + "a" * 64


def _codex_home(root: Path) -> Path:
    home = root / "home"
    (home / ".codex" / "plugins" / "cache").mkdir(parents=True)
    (home / ".codex" / "plugins" / "cache" / "plugin.js").write_text(
        "trusted plugin asset\n", encoding="utf-8"
    )
    (home / ".codex" / "cache" / "codex_apps_tools").mkdir(parents=True)
    (home / ".codex" / "cache" / "codex_apps_tools" / "tools.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (home / ".codex" / "state.sqlite").write_text(
        "private session state\n", encoding="utf-8"
    )
    return home


def test_secure_policy_shares_no_user_agent_tooling() -> None:
    assert shared_cache_paths("codex") == ()
    assert shared_cache_paths("cursor") == ()
    assert shared_cache_paths("gemini") == ()
    assert shared_cache_paths("antigravity") == ()


def test_seed_mounts_and_prunes_only_shared_paths(tmp_path: Path) -> None:
    source_home = _codex_home(tmp_path / "source")
    cache_root = tmp_path / "shared"
    store = SharedSessionCacheStore(cache_root, agent="codex", image=IMAGE)

    assert store.seed_from_trusted_home(source_home) == ()
    mounts = store.mounts()
    assert mounts == ()

    session_home = _codex_home(tmp_path / "session")
    removed = store.prune_session_home(session_home)
    assert removed == ()
    assert (session_home / ".codex" / "plugins" / "cache").exists()
    assert (session_home / ".codex" / "state.sqlite").exists()


def test_store_rejects_insecure_root_and_symlinked_home(tmp_path: Path) -> None:
    insecure_root = tmp_path / "insecure"
    insecure_root.mkdir(mode=0o755)
    with pytest.raises(SecurityPolicyError, match="other users"):
        SharedSessionCacheStore(insecure_root, agent="codex", image=IMAGE)

    source_home = _codex_home(tmp_path / "source")
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(source_home, target_is_directory=True)
    store = SharedSessionCacheStore(tmp_path / "shared", agent="codex", image=IMAGE)
    with pytest.raises(SecurityPolicyError, match="real directory"):
        store.seed_from_trusted_home(linked_home)
