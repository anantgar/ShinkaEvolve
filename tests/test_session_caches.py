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


def test_policy_shares_only_static_codex_and_antigravity_assets() -> None:
    assert ".codex/plugins/cache" in shared_cache_paths("codex")
    assert ".codex/cache/codex_apps_tools" in shared_cache_paths("codex")
    assert shared_cache_paths("cursor") == (".cursor/plugins/cache",)
    assert shared_cache_paths("gemini") == ()
    assert shared_cache_paths("antigravity") == (
        ".gemini/antigravity-cli/bin",
    )


def test_seed_mounts_and_prunes_only_shared_paths(tmp_path: Path) -> None:
    source_home = _codex_home(tmp_path / "source")
    cache_root = tmp_path / "shared"
    store = SharedSessionCacheStore(cache_root, agent="codex", image=IMAGE)

    assert store.seed_from_trusted_home(source_home) == (
        ".codex/plugins/cache",
        ".codex/cache/codex_apps_tools",
    )
    mounts = store.mounts()
    assert [mount.relative_path for mount in mounts] == [
        ".codex/plugins/cache",
        ".codex/cache/codex_apps_tools",
    ]
    assert all(mount.source.is_dir() for mount in mounts)
    assert all(mount.target.startswith("/headless-home/") for mount in mounts)

    session_home = _codex_home(tmp_path / "session")
    removed = store.prune_session_home(session_home)
    assert removed == (
        ".codex/plugins/cache",
        ".codex/cache/codex_apps_tools",
    )
    assert (session_home / ".codex" / "plugins" / "cache").is_dir()
    assert not any((session_home / ".codex" / "plugins" / "cache").iterdir())
    assert (session_home / ".codex" / "state.sqlite").exists()


def test_seed_rejects_symlinks_in_trusted_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = home / ".codex" / "plugins" / "cache"
    cache.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("must not be shared", encoding="utf-8")
    (cache / "link").symlink_to(outside)

    store = SharedSessionCacheStore(tmp_path / "shared", agent="codex", image=IMAGE)
    with pytest.raises(SecurityPolicyError, match="symlinks"):
        store.seed_from_trusted_home(home)


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


def test_image_namespaces_are_isolated(tmp_path: Path) -> None:
    home = _codex_home(tmp_path / "source")
    first = SharedSessionCacheStore(tmp_path / "shared", agent="codex", image=IMAGE)
    second = SharedSessionCacheStore(
        tmp_path / "shared",
        agent="codex",
        image=IMAGE.replace("a" * 64, "b" * 64),
    )

    first.seed_from_trusted_home(home)
    assert first.mounts()
    assert not second.mounts()
