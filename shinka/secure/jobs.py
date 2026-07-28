"""Durable SQLite state machine for long-running secure jobs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .canonical import canonical_json_bytes, validate_digest
from .contracts import JobSpec
from .errors import ConfigurationError, FailureClass, SecurityPolicyError


class JobPhase(str, Enum):
    PREPARED = "PREPARED"
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    RESULT_VALIDATED = "RESULT_VALIDATED"
    PERSISTED = "PERSISTED"
    FAILED = "FAILED"
    CLEANED = "CLEANED"


class MutationPhase(str, Enum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    OUTPUT_PENDING = "OUTPUT_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


_MUTATION_TRANSITIONS: dict[MutationPhase, frozenset[MutationPhase]] = {
    MutationPhase.PREPARED: frozenset({MutationPhase.RUNNING, MutationPhase.FAILED}),
    MutationPhase.RUNNING: frozenset(
        {MutationPhase.OUTPUT_PENDING, MutationPhase.FAILED}
    ),
    MutationPhase.OUTPUT_PENDING: frozenset(
        {MutationPhase.SUCCEEDED, MutationPhase.FAILED}
    ),
    MutationPhase.SUCCEEDED: frozenset(),
    MutationPhase.FAILED: frozenset(),
}


_TRANSITIONS: dict[JobPhase, frozenset[JobPhase]] = {
    JobPhase.PREPARED: frozenset({JobPhase.QUEUED, JobPhase.FAILED}),
    JobPhase.QUEUED: frozenset({JobPhase.STARTING, JobPhase.FAILED}),
    JobPhase.STARTING: frozenset({JobPhase.RUNNING, JobPhase.FAILED}),
    JobPhase.RUNNING: frozenset({JobPhase.COLLECTING, JobPhase.FAILED}),
    JobPhase.COLLECTING: frozenset({JobPhase.RESULT_VALIDATED, JobPhase.FAILED}),
    JobPhase.RESULT_VALIDATED: frozenset({JobPhase.PERSISTED, JobPhase.FAILED}),
    JobPhase.PERSISTED: frozenset({JobPhase.CLEANED}),
    JobPhase.FAILED: frozenset({JobPhase.CLEANED}),
    JobPhase.CLEANED: frozenset(),
}


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    attempt_id: str
    idempotency_key: str
    kind: str
    state: JobPhase
    event_sequence: int
    spec_digest: str
    spec: Mapping[str, Any]
    candidate_digest: str
    runtime_artifact_digest: str
    evaluator_digest: str
    dependency_digest: str
    environment_digest: str
    backend: str
    backend_id: str | None
    worker_pid: int | None
    requested_resources: Mapping[str, Any]
    created_at: float
    queued_at: float | None
    started_at: float | None
    heartbeat_at: float | None
    checkpoint_at: float | None
    terminal_at: float | None
    persisted_at: float | None
    cleanup_at: float | None
    checkpoint_digest: str | None
    staging_path: str | None
    result_digest: str | None
    failure_class: FailureClass | None
    diagnostic_digest: str | None


@dataclass(frozen=True)
class MutationAttemptRecord:
    attempt_id: str
    job_id: str
    state: MutationPhase
    event_sequence: int
    parent_digest: str
    prompt_digest: str
    image: str
    agent: str
    model: str | None
    container_name: str
    container_id: str | None
    candidate_digest: str | None
    failure_class: FailureClass | None
    created_at: float
    started_at: float | None
    agent_completed_at: float | None
    terminal_at: float | None
    cleanup_at: float | None


def _json_load(value: str | None) -> Mapping[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise SecurityPolicyError("Persisted job JSON is not an object")
    return loaded


class EvaluationJobStore:
    """Transactional job/event state with conditional idempotent transitions."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS evaluation_jobs (
                    job_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL DEFAULT 0,
                    spec_digest TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    runtime_artifact_digest TEXT NOT NULL,
                    evaluator_digest TEXT NOT NULL,
                    dependency_digest TEXT NOT NULL,
                    environment_digest TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    backend_id TEXT,
                    worker_pid INTEGER,
                    requested_resources_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    queued_at REAL,
                    started_at REAL,
                    heartbeat_at REAL,
                    checkpoint_at REAL,
                    terminal_at REAL,
                    persisted_at REAL,
                    cleanup_at REAL,
                    checkpoint_digest TEXT,
                    staging_path TEXT,
                    result_digest TEXT,
                    failure_class TEXT,
                    diagnostic_digest TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_evaluation_jobs_state
                    ON evaluation_jobs(state);
                CREATE INDEX IF NOT EXISTS idx_evaluation_jobs_backend_id
                    ON evaluation_jobs(backend_id);
                CREATE TABLE IF NOT EXISTS evaluation_job_events (
                    job_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (job_id, sequence),
                    FOREIGN KEY (job_id) REFERENCES evaluation_jobs(job_id)
                );
                CREATE TABLE IF NOT EXISTS mutation_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL DEFAULT 0,
                    parent_digest TEXT NOT NULL,
                    prompt_digest TEXT NOT NULL,
                    image TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    model TEXT,
                    container_name TEXT NOT NULL UNIQUE,
                    container_id TEXT,
                    candidate_digest TEXT,
                    failure_class TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    agent_completed_at REAL,
                    terminal_at REAL,
                    cleanup_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_mutation_attempts_state
                    ON mutation_attempts(state);
                CREATE TABLE IF NOT EXISTS mutation_attempt_events (
                    attempt_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (attempt_id, sequence),
                    FOREIGN KEY (attempt_id) REFERENCES mutation_attempts(attempt_id)
                );
                """)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(evaluation_jobs)")
            }
            if "runtime_artifact_digest" not in columns:
                connection.execute(
                    "ALTER TABLE evaluation_jobs ADD COLUMN runtime_artifact_digest TEXT"
                )
                connection.execute(
                    "UPDATE evaluation_jobs SET runtime_artifact_digest = candidate_digest "
                    "WHERE runtime_artifact_digest IS NULL"
                )
        os.chmod(self.path, 0o600)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        failure = row["failure_class"]
        return JobRecord(
            job_id=row["job_id"],
            attempt_id=row["attempt_id"],
            idempotency_key=row["idempotency_key"],
            kind=row["kind"],
            state=JobPhase(row["state"]),
            event_sequence=int(row["event_sequence"]),
            spec_digest=row["spec_digest"],
            spec=_json_load(row["spec_json"]),
            candidate_digest=row["candidate_digest"],
            runtime_artifact_digest=row["runtime_artifact_digest"],
            evaluator_digest=row["evaluator_digest"],
            dependency_digest=row["dependency_digest"],
            environment_digest=row["environment_digest"],
            backend=row["backend"],
            backend_id=row["backend_id"],
            worker_pid=row["worker_pid"],
            requested_resources=_json_load(row["requested_resources_json"]),
            created_at=float(row["created_at"]),
            queued_at=row["queued_at"],
            started_at=row["started_at"],
            heartbeat_at=row["heartbeat_at"],
            checkpoint_at=row["checkpoint_at"],
            terminal_at=row["terminal_at"],
            persisted_at=row["persisted_at"],
            cleanup_at=row["cleanup_at"],
            checkpoint_digest=row["checkpoint_digest"],
            staging_path=row["staging_path"],
            result_digest=row["result_digest"],
            failure_class=FailureClass(failure) if failure else None,
            diagnostic_digest=row["diagnostic_digest"],
        )

    @staticmethod
    def _mutation_record(row: sqlite3.Row) -> MutationAttemptRecord:
        failure = row["failure_class"]
        return MutationAttemptRecord(
            attempt_id=row["attempt_id"],
            job_id=row["job_id"],
            state=MutationPhase(row["state"]),
            event_sequence=int(row["event_sequence"]),
            parent_digest=row["parent_digest"],
            prompt_digest=row["prompt_digest"],
            image=row["image"],
            agent=row["agent"],
            model=row["model"],
            container_name=row["container_name"],
            container_id=row["container_id"],
            candidate_digest=row["candidate_digest"],
            failure_class=FailureClass(failure) if failure else None,
            created_at=float(row["created_at"]),
            started_at=row["started_at"],
            agent_completed_at=row["agent_completed_at"],
            terminal_at=row["terminal_at"],
            cleanup_at=row["cleanup_at"],
        )

    @staticmethod
    def _append_mutation_event(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event_type: str,
        state: MutationPhase,
        now: float,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        sequence = int(row["event_sequence"]) + 1
        connection.execute(
            """
            INSERT INTO mutation_attempt_events (
                attempt_id, sequence, event_type, state, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["attempt_id"],
                sequence,
                event_type,
                state.value,
                now,
                canonical_json_bytes(dict(payload or {})).decode("utf-8"),
            ),
        )
        return sequence

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event_type: str,
        state: JobPhase,
        now: float,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        sequence = int(row["event_sequence"]) + 1
        connection.execute(
            """
            INSERT INTO evaluation_job_events (
                job_id, attempt_id, sequence, event_type, state, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["job_id"],
                row["attempt_id"],
                sequence,
                event_type,
                state.value,
                now,
                canonical_json_bytes(dict(payload or {})).decode("utf-8"),
            ),
        )
        return sequence

    def prepare(
        self,
        spec: JobSpec,
        *,
        idempotency_key: str,
        kind: str = "evaluation",
        backend: str = "local_docker",
        staging_path: str | None = None,
        now: float | None = None,
    ) -> JobRecord:
        if not idempotency_key or not kind:
            raise ConfigurationError("Job idempotency key and kind are required")
        timestamp = time.time() if now is None else now
        spec_json = spec.to_json_bytes().decode("utf-8")
        resources_json = canonical_json_bytes(asdict(spec.resources)).decode("utf-8")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["spec_digest"] != spec.digest:
                    raise SecurityPolicyError(
                        "Idempotency key was reused for a different immutable job"
                    )
                return self._record(existing)

            connection.execute(
                """
                INSERT INTO evaluation_jobs (
                    job_id, attempt_id, idempotency_key, kind, state, event_sequence,
                    spec_digest, spec_json, candidate_digest, runtime_artifact_digest, evaluator_digest,
                    dependency_digest, environment_digest, backend,
                    requested_resources_json, created_at, staging_path
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.job_id,
                    spec.attempt_id,
                    idempotency_key,
                    kind,
                    JobPhase.PREPARED.value,
                    spec.digest,
                    spec_json,
                    spec.candidate_digest,
                    spec.runtime_artifact_digest,
                    spec.evaluator_digest,
                    spec.dependency_digest,
                    spec.environment_digest,
                    backend,
                    resources_json,
                    timestamp,
                    staging_path,
                ),
            )
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (spec.job_id,)
            ).fetchone()
            assert row is not None
            sequence = self._append_event(
                connection,
                row=row,
                event_type="prepared",
                state=JobPhase.PREPARED,
                now=timestamp,
                payload={"spec_digest": spec.digest},
            )
            connection.execute(
                "UPDATE evaluation_jobs SET event_sequence = ? WHERE job_id = ?",
                (sequence, spec.job_id),
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (spec.job_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._record(row) if row is not None else None

    def transition(
        self,
        job_id: str,
        new_state: JobPhase,
        *,
        expected: Iterable[JobPhase] | None = None,
        event_type: str | None = None,
        payload: Mapping[str, Any] | None = None,
        backend_id: str | None = None,
        worker_pid: int | None = None,
        result_digest: str | None = None,
        failure_class: FailureClass | None = None,
        diagnostic_digest: str | None = None,
        now: float | None = None,
    ) -> JobRecord:
        if result_digest is not None:
            validate_digest(result_digest)
        if diagnostic_digest is not None:
            validate_digest(diagnostic_digest)
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = JobPhase(row["state"])
            if current is new_state:
                return self._record(row)
            expected_states = set(expected) if expected is not None else {current}
            if current not in expected_states:
                raise SecurityPolicyError(
                    f"Job {job_id} is {current.value}, expected "
                    f"{sorted(state.value for state in expected_states)}"
                )
            if new_state not in _TRANSITIONS[current]:
                raise SecurityPolicyError(
                    f"Invalid job transition {current.value} -> {new_state.value}"
                )
            if new_state is JobPhase.FAILED and failure_class is None:
                raise SecurityPolicyError("Failed jobs require a typed failure class")
            if new_state is JobPhase.RESULT_VALIDATED and result_digest is None:
                raise SecurityPolicyError("Validated jobs require a result digest")

            sequence = self._append_event(
                connection,
                row=row,
                event_type=event_type or new_state.value.lower(),
                state=new_state,
                now=timestamp,
                payload=payload,
            )
            timestamp_column = {
                JobPhase.QUEUED: "queued_at",
                JobPhase.RUNNING: "started_at",
                JobPhase.RESULT_VALIDATED: "terminal_at",
                JobPhase.FAILED: "terminal_at",
                JobPhase.PERSISTED: "persisted_at",
                JobPhase.CLEANED: "cleanup_at",
            }.get(new_state)
            assignments = ["state = ?", "event_sequence = ?"]
            values: list[Any] = [new_state.value, sequence]
            if timestamp_column:
                assignments.append(f"{timestamp_column} = ?")
                values.append(timestamp)
            for column, value in (
                ("backend_id", backend_id),
                ("worker_pid", worker_pid),
                ("result_digest", result_digest),
                ("failure_class", failure_class.value if failure_class else None),
                ("diagnostic_digest", diagnostic_digest),
            ):
                if value is not None:
                    assignments.append(f"{column} = ?")
                    values.append(value)
            values.append(job_id)
            connection.execute(
                f"UPDATE evaluation_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def heartbeat(
        self,
        job_id: str,
        *,
        phase: str | None = None,
        now: float | None = None,
    ) -> JobRecord:
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            state = JobPhase(row["state"])
            if state not in {
                JobPhase.STARTING,
                JobPhase.RUNNING,
                JobPhase.COLLECTING,
            }:
                raise SecurityPolicyError(
                    "Heartbeat is not valid in the current job state"
                )
            sequence = self._append_event(
                connection,
                row=row,
                event_type="heartbeat",
                state=state,
                now=timestamp,
                payload={"phase": phase} if phase else {},
            )
            connection.execute(
                """
                UPDATE evaluation_jobs
                SET heartbeat_at = ?, event_sequence = ?
                WHERE job_id = ?
                """,
                (timestamp, sequence, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def record_worker_launch(
        self,
        job_id: str,
        *,
        worker_pid: int,
        backend_id: str,
        now: float | None = None,
    ) -> JobRecord:
        """Durably bind a launched worker without changing the job phase."""

        if worker_pid <= 0 or not backend_id:
            raise ConfigurationError("Worker launch identity is invalid")
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            state = JobPhase(row["state"])
            if state not in {JobPhase.STARTING, JobPhase.RUNNING}:
                raise SecurityPolicyError(
                    "Worker launch can be bound only while starting or running"
                )
            sequence = self._append_event(
                connection,
                row=row,
                event_type="worker_launched",
                state=state,
                now=timestamp,
                payload={"backend_id": backend_id, "worker_pid": worker_pid},
            )
            connection.execute(
                """
                UPDATE evaluation_jobs
                SET worker_pid = ?, backend_id = ?, event_sequence = ?
                WHERE job_id = ?
                """,
                (worker_pid, backend_id, sequence, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def checkpoint(
        self,
        job_id: str,
        digest: str,
        *,
        now: float | None = None,
    ) -> JobRecord:
        validate_digest(digest)
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            state = JobPhase(row["state"])
            if state is not JobPhase.RUNNING:
                raise SecurityPolicyError("Checkpoints are accepted only while running")
            sequence = self._append_event(
                connection,
                row=row,
                event_type="checkpoint",
                state=state,
                now=timestamp,
                payload={"checkpoint_digest": digest},
            )
            connection.execute(
                """
                UPDATE evaluation_jobs
                SET checkpoint_at = ?, checkpoint_digest = ?, event_sequence = ?
                WHERE job_id = ?
                """,
                (timestamp, digest, sequence, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._record(updated)

    def list_recoverable(self) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_jobs WHERE state != ? ORDER BY created_at",
                (JobPhase.CLEANED.value,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, job_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_job_events
                WHERE job_id = ? AND sequence > ? ORDER BY sequence
                """,
                (job_id, after_sequence),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "attempt_id": row["attempt_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "state": row["state"],
                "created_at": row["created_at"],
                "payload": _json_load(row["payload_json"]),
            }
            for row in rows
        ]

    def prepare_mutation(
        self,
        *,
        attempt_id: str,
        job_id: str,
        parent_digest: str,
        prompt_digest: str,
        image: str,
        agent: str,
        model: str | None,
        container_name: str,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        """Persist immutable mutation launch intent before container creation."""

        if not all((attempt_id, job_id, image, agent, container_name)):
            raise ConfigurationError("Mutation launch identity is incomplete")
        validate_digest(parent_digest)
        validate_digest(prompt_digest)
        timestamp = time.time() if now is None else now
        immutable = {
            "job_id": job_id,
            "parent_digest": parent_digest,
            "prompt_digest": prompt_digest,
            "image": image,
            "agent": agent,
            "model": model,
            "container_name": container_name,
        }
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if existing is not None:
                if any(existing[key] != value for key, value in immutable.items()):
                    raise SecurityPolicyError(
                        "Mutation attempt identity was reused for different launch intent"
                    )
                return self._mutation_record(existing)
            connection.execute(
                """
                INSERT INTO mutation_attempts (
                    attempt_id, job_id, state, event_sequence, parent_digest,
                    prompt_digest, image, agent, model, container_name, created_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    job_id,
                    MutationPhase.PREPARED.value,
                    parent_digest,
                    prompt_digest,
                    image,
                    agent,
                    model,
                    container_name,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert row is not None
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="prepared",
                state=MutationPhase.PREPARED,
                now=timestamp,
                payload={
                    "parent_digest": parent_digest,
                    "prompt_digest": prompt_digest,
                    "image": image,
                    "agent": agent,
                    "model": model,
                    "container_name": container_name,
                },
            )
            connection.execute(
                "UPDATE mutation_attempts SET event_sequence = ? WHERE attempt_id = ?",
                (sequence, attempt_id),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def get_mutation(self, attempt_id: str) -> MutationAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return self._mutation_record(row) if row is not None else None

    def record_mutation_launch(
        self,
        attempt_id: str,
        *,
        container_id: str,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        if not container_id:
            raise ConfigurationError("Mutation container identity is required")
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            state = MutationPhase(row["state"])
            if state is MutationPhase.RUNNING and row["container_id"] == container_id:
                return self._mutation_record(row)
            if state is not MutationPhase.PREPARED:
                raise SecurityPolicyError("Mutation launch is not in PREPARED state")
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="container_created",
                state=MutationPhase.RUNNING,
                now=timestamp,
                payload={"container_id": container_id},
            )
            connection.execute(
                """
                UPDATE mutation_attempts
                SET state = ?, event_sequence = ?, container_id = ?, started_at = ?
                WHERE attempt_id = ?
                """,
                (
                    MutationPhase.RUNNING.value,
                    sequence,
                    container_id,
                    timestamp,
                    attempt_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def mark_mutation_output_pending(
        self,
        attempt_id: str,
        *,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            state = MutationPhase(row["state"])
            if state is MutationPhase.OUTPUT_PENDING:
                return self._mutation_record(row)
            if MutationPhase.OUTPUT_PENDING not in _MUTATION_TRANSITIONS[state]:
                raise SecurityPolicyError(
                    "Mutation output cannot be staged in this state"
                )
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="agent_completed",
                state=MutationPhase.OUTPUT_PENDING,
                now=timestamp,
            )
            connection.execute(
                """
                UPDATE mutation_attempts
                SET state = ?, event_sequence = ?, agent_completed_at = ?
                WHERE attempt_id = ?
                """,
                (MutationPhase.OUTPUT_PENDING.value, sequence, timestamp, attempt_id),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def complete_mutation(
        self,
        attempt_id: str,
        *,
        candidate_digest: str,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        validate_digest(candidate_digest)
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            state = MutationPhase(row["state"])
            if state is MutationPhase.SUCCEEDED:
                if row["candidate_digest"] != candidate_digest:
                    raise SecurityPolicyError(
                        "Completed mutation was bound to a different candidate"
                    )
                return self._mutation_record(row)
            if MutationPhase.SUCCEEDED not in _MUTATION_TRANSITIONS[state]:
                raise SecurityPolicyError("Mutation cannot complete in this state")
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="candidate_persisted",
                state=MutationPhase.SUCCEEDED,
                now=timestamp,
                payload={"candidate_digest": candidate_digest},
            )
            connection.execute(
                """
                UPDATE mutation_attempts
                SET state = ?, event_sequence = ?, candidate_digest = ?, terminal_at = ?
                WHERE attempt_id = ?
                """,
                (
                    MutationPhase.SUCCEEDED.value,
                    sequence,
                    candidate_digest,
                    timestamp,
                    attempt_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def fail_mutation(
        self,
        attempt_id: str,
        *,
        failure_class: FailureClass,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            state = MutationPhase(row["state"])
            if state is MutationPhase.FAILED:
                return self._mutation_record(row)
            if MutationPhase.FAILED not in _MUTATION_TRANSITIONS[state]:
                raise SecurityPolicyError("A successful mutation cannot be failed")
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="failed",
                state=MutationPhase.FAILED,
                now=timestamp,
                payload={"failure_class": failure_class.value},
            )
            connection.execute(
                """
                UPDATE mutation_attempts
                SET state = ?, event_sequence = ?, failure_class = ?, terminal_at = ?
                WHERE attempt_id = ?
                """,
                (
                    MutationPhase.FAILED.value,
                    sequence,
                    failure_class.value,
                    timestamp,
                    attempt_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def mark_mutation_cleaned(
        self,
        attempt_id: str,
        *,
        now: float | None = None,
    ) -> MutationAttemptRecord:
        timestamp = time.time() if now is None else now
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["cleanup_at"] is not None:
                return self._mutation_record(row)
            state = MutationPhase(row["state"])
            sequence = self._append_mutation_event(
                connection,
                row=row,
                event_type="container_cleaned",
                state=state,
                now=timestamp,
            )
            connection.execute(
                """
                UPDATE mutation_attempts
                SET event_sequence = ?, cleanup_at = ? WHERE attempt_id = ?
                """,
                (sequence, timestamp, attempt_id),
            )
            updated = connection.execute(
                "SELECT * FROM mutation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            assert updated is not None
            return self._mutation_record(updated)

    def list_recoverable_mutations(self) -> list[MutationAttemptRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM mutation_attempts
                WHERE state IN (?, ?, ?) ORDER BY created_at
                """,
                (
                    MutationPhase.PREPARED.value,
                    MutationPhase.RUNNING.value,
                    MutationPhase.OUTPUT_PENDING.value,
                ),
            ).fetchall()
        return [self._mutation_record(row) for row in rows]

    def mutation_events(
        self,
        attempt_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM mutation_attempt_events
                WHERE attempt_id = ? AND sequence > ? ORDER BY sequence
                """,
                (attempt_id, after_sequence),
            ).fetchall()
        return [
            {
                "attempt_id": row["attempt_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "state": row["state"],
                "created_at": row["created_at"],
                "payload": _json_load(row["payload_json"]),
            }
            for row in rows
        ]
