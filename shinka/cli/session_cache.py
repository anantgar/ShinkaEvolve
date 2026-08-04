"""Seed and inspect trusted shared Headless session caches."""

from __future__ import annotations

import argparse
from pathlib import Path

from shinka.secure.session_caches import (
    SharedSessionCacheStore,
    shared_cache_paths,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--image", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage trusted read-only caches for durable Headless sessions."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser(
        "seed",
        help="seed a cache namespace from an operator-trusted session home",
    )
    _common(seed)
    seed.add_argument("--session-home", required=True, type=Path)

    prune = commands.add_parser(
        "prune",
        help="remove shared static-cache paths from a session home",
    )
    _common(prune)
    prune.add_argument("--session-home", required=True, type=Path)
    prune.add_argument("--yes", action="store_true")

    inspect = commands.add_parser("inspect", help="show policy and available mounts")
    _common(inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SharedSessionCacheStore(args.root, agent=args.agent, image=args.image)

    if args.command == "seed":
        seeded = store.seed_from_trusted_home(args.session_home)
        print("seeded=" + (",".join(seeded) if seeded else "none"))
        return 0

    if args.command == "prune":
        if not args.yes:
            raise SystemExit("prune requires --yes")
        removed = store.prune_session_home(args.session_home)
        print("removed=" + (",".join(removed) if removed else "none"))
        return 0

    print("policy=" + (",".join(shared_cache_paths(args.agent)) or "none"))
    mounts = store.mounts()
    print(
        "available="
        + (",".join(mount.relative_path for mount in mounts) if mounts else "none")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
