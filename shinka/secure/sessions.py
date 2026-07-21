"""Durable proposal-scoped Headless session homes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConfigurationError, SecurityPolicyError

SESSION_SCHEMA_VERSION = "shinka-headless-session-v1"
_SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ProposalSession:
    proposal_id: str
    session_name: str
    home_key: str
    created_at: str
    schema_version: str = SESSION_SCHEMA_VERSION


class ProposalSessionStore:
    """Persist only the identity needed to reopen an opaque session home."""

    def __init__(self, root: Path | str) -> None:
        unresolved = Path(root).expanduser()
        if unresolved.exists() and unresolved.is_symlink():
            raise SecurityPolicyError("Headless session root cannot be a symlink")
        unresolved.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(unresolved, 0o700)
        self.root = unresolved.resolve()
        self.metadata_root = self.root / "metadata"
        self.homes_root = self.root / "homes"
        for path in (self.metadata_root, self.homes_root):
            path.mkdir(mode=0o700, exist_ok=True)
            if path.is_symlink():
                raise SecurityPolicyError(
                    "Headless session storage cannot use symlinks"
                )
            os.chmod(path, 0o700)

    @staticmethod
    def _key(proposal_id: str) -> str:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ConfigurationError("Headless proposal_id must be non-empty")
        return hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()

    def _metadata_path(self, key: str) -> Path:
        return self.metadata_root / f"{key}.json"

    def home_path(self, record: ProposalSession) -> Path:
        if record.home_key != self._key(record.proposal_id):
            raise SecurityPolicyError(
                "Headless session metadata has an invalid home key"
            )
        path = self.homes_root / record.home_key
        if path.is_symlink() or not path.is_dir():
            raise SecurityPolicyError("Headless session home is missing or unsafe")
        return path

    def _load(self, proposal_id: str, key: str) -> ProposalSession | None:
        path = self._metadata_path(key)
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise SecurityPolicyError("Headless session metadata is unsafe")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            record = ProposalSession(**loaded)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SecurityPolicyError("Headless session metadata is invalid") from exc
        if (
            record.schema_version != SESSION_SCHEMA_VERSION
            or record.proposal_id != proposal_id
            or record.home_key != key
            or not _SESSION_NAME.fullmatch(record.session_name)
        ):
            raise SecurityPolicyError(
                "Headless session metadata does not match the proposal"
            )
        self.home_path(record)
        return record

    def get_or_create(
        self,
        proposal_id: str,
        *,
        session_name: str | None = None,
    ) -> ProposalSession:
        key = self._key(proposal_id)
        existing = self._load(proposal_id, key)
        if existing is not None:
            if session_name is not None and session_name != existing.session_name:
                raise ConfigurationError(
                    "Headless session name conflicts with durable proposal metadata"
                )
            return existing

        chosen_name = session_name or f"shinka-{key[:24]}"
        if not _SESSION_NAME.fullmatch(chosen_name):
            raise ConfigurationError(
                "Headless session name must contain only letters, digits, '.', '_', or '-'"
            )
        home = self.homes_root / key
        try:
            home.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError as exc:
            raise SecurityPolicyError(
                "Headless session home exists without durable metadata"
            ) from exc
        record = ProposalSession(
            proposal_id=proposal_id,
            session_name=chosen_name,
            home_key=key,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
        metadata_path = self._metadata_path(key)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.metadata_root,
                prefix=f".{key}.",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(temporary_path, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, metadata_path)
            directory_fd = os.open(self.metadata_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            shutil.rmtree(home, ignore_errors=True)
            raise
        return record

    def metadata_view(self, record: ProposalSession) -> dict[str, str]:
        """Return the non-secret fields safe to persist with a proposal."""

        return {
            "schema_version": record.schema_version,
            "proposal_id": record.proposal_id,
            "session_name": record.session_name,
            "home_key": record.home_key,
        }

    def cleanup(self, proposal_id: str) -> None:
        key = self._key(proposal_id)
        record = self._load(proposal_id, key)
        if record is None:
            return
        shutil.rmtree(self.home_path(record))
        self._metadata_path(key).unlink()
