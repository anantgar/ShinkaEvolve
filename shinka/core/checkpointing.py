"""Crash-safe clean checkpoints for deterministic Shinka-owned RNG resume.

Checkpoint files use pickle and must only be loaded from a trusted results
directory. Pickle can execute code while loading untrusted input.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import pickle
import platform
import random
import tempfile
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from shinka import __version__

CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_FILENAME = "checkpoint.pkl"
PREVIOUS_CHECKPOINT_FILENAME = "checkpoint.previous.pkl"
RESUME_METADATA_FILENAME = "checkpoint_resume.json"
_ENVELOPE_FORMAT = "shinka-clean-checkpoint-v1"


class CheckpointError(RuntimeError):
    """Base error for checkpoint persistence or validation failures."""


class CheckpointNotFoundError(CheckpointError):
    """Raised when no checkpoint file exists in the results directory."""


class CheckpointIntegrityError(CheckpointError):
    """Raised when a checkpoint envelope or checksum is invalid."""


class CheckpointCompatibilityError(CheckpointError):
    """Raised when a valid checkpoint is incompatible with the current run."""


class UncleanCheckpointError(CheckpointError):
    """Raised when a checkpoint does not represent a clean drain boundary."""


@dataclass(frozen=True)
class LoadedCheckpoint:
    payload: dict[str, Any]
    path: Path


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync for atomic directory-entry publication."""
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_fsynced_temp(directory: Path, prefix: str, data: bytes) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _copy_fsynced(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    temp_path = _write_fsynced_temp(
        destination.parent,
        f".{destination.name}.",
        data,
    )
    try:
        os.replace(temp_path, destination)
        _fsync_directory(destination.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _checkpoint_bytes(payload: Mapping[str, Any]) -> bytes:
    payload_bytes = pickle.dumps(dict(payload), protocol=pickle.HIGHEST_PROTOCOL)
    envelope = {
        "format": _ENVELOPE_FORMAT,
        "schema_version": payload.get("schema_version"),
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload_bytes,
    }
    return pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL)


def write_checkpoint(results_dir: Path | str, payload: Mapping[str, Any]) -> Path:
    """Atomically publish a checkpoint while retaining the prior generation."""
    directory = Path(results_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CHECKPOINT_FILENAME
    previous = directory / PREVIOUS_CHECKPOINT_FILENAME
    temp_path = _write_fsynced_temp(
        directory,
        f".{CHECKPOINT_FILENAME}.",
        _checkpoint_bytes(payload),
    )
    try:
        # Copy rather than rename the current checkpoint away. If publication of
        # the replacement fails, the current checkpoint remains valid in place.
        if target.exists():
            _copy_fsynced(target, previous)
        os.replace(temp_path, target)
        _fsync_directory(directory)
    finally:
        temp_path.unlink(missing_ok=True)
    return target


def _decode_checkpoint(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            envelope = pickle.load(handle)
    except Exception as exc:
        raise CheckpointIntegrityError(
            f"Could not load checkpoint envelope {path.name}: {exc}"
        ) from exc

    if not isinstance(envelope, dict) or envelope.get("format") != _ENVELOPE_FORMAT:
        raise CheckpointIntegrityError(f"Invalid checkpoint envelope in {path.name}")
    payload_bytes = envelope.get("payload")
    expected_digest = envelope.get("payload_sha256")
    if not isinstance(payload_bytes, bytes) or not isinstance(expected_digest, str):
        raise CheckpointIntegrityError(
            f"Checkpoint envelope fields are invalid in {path.name}"
        )
    actual_digest = hashlib.sha256(payload_bytes).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise CheckpointIntegrityError(f"Checkpoint checksum mismatch in {path.name}")

    try:
        payload = pickle.loads(payload_bytes)
    except Exception as exc:
        raise CheckpointIntegrityError(
            f"Could not decode checkpoint payload in {path.name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CheckpointIntegrityError(
            f"Checkpoint payload in {path.name} must be a dictionary"
        )
    if envelope.get("schema_version") != payload.get("schema_version"):
        raise CheckpointIntegrityError(
            f"Checkpoint schema fields disagree in {path.name}"
        )
    return payload


def load_checkpoint(results_dir: Path | str) -> LoadedCheckpoint:
    """Load the newest valid fixed-name checkpoint, falling back to the prior one."""
    directory = Path(results_dir).resolve()
    candidates = [
        directory / CHECKPOINT_FILENAME,
        directory / PREVIOUS_CHECKPOINT_FILENAME,
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise CheckpointNotFoundError(
            f"No {CHECKPOINT_FILENAME} found in results directory {directory}"
        )

    failures: list[str] = []
    for path in existing:
        try:
            return LoadedCheckpoint(payload=_decode_checkpoint(path), path=path)
        except CheckpointIntegrityError as exc:
            failures.append(str(exc))
    raise CheckpointIntegrityError("; ".join(failures))


def write_resume_metadata(results_dir: Path | str, metadata: Mapping[str, Any]) -> Path:
    """Atomically publish a human-readable resume audit record."""
    directory = Path(results_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / RESUME_METADATA_FILENAME
    data = (json.dumps(dict(metadata), indent=2, sort_keys=True) + "\n").encode()
    temp_path = _write_fsynced_temp(directory, f".{target.name}.", data)
    try:
        os.replace(temp_path, target)
        _fsync_directory(directory)
    finally:
        temp_path.unlink(missing_ok=True)
    return target


def _qualified_type(value: Any) -> str:
    value_type = value if isinstance(value, type) else type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _normalize_config_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, np.generic):
        return _normalize_config_value(value.item())
    if isinstance(value, np.ndarray):
        return [_normalize_config_value(item) for item in value.tolist()]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_config_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_config_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize_config_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if is_dataclass(value):
        return {
            field.name: _normalize_config_value(getattr(value, field.name))
            for field in fields(value)
        }
    checkpoint_config = getattr(value, "checkpoint_config", None)
    if callable(checkpoint_config):
        return _normalize_config_value(checkpoint_config())
    if callable(value) or isinstance(value, type):
        return {"type": _qualified_type(value)}
    return {"type": _qualified_type(value)}


def _file_identity(path_value: Any) -> Any:
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.is_file():
        return {"path": str(path), "sha256": None}
    return {
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _hash_mapping(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _normalize_config_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def configuration_hashes(
    evo_config: Any,
    db_config: Any,
    job_config: Any,
) -> dict[str, str]:
    """Hash normalized selection, database, and evaluator configuration."""
    evolution = _normalize_config_value(evo_config)
    database = _normalize_config_value(db_config)
    job = _normalize_config_value(job_config)
    if not isinstance(evolution, dict) or not isinstance(database, dict):
        raise TypeError("Evolution and database configurations must be structured")
    if not isinstance(job, dict):
        raise TypeError("Job configuration must be structured")

    # Location, observability, and resume policy do not alter selection state.
    evolution.pop("results_dir", None)
    evolution.pop("checkpoint_resume_mode", None)
    evolution.pop("num_generations", None)
    for key in list(evolution):
        if key.startswith("wandb_") or key == "enable_wandb_logging":
            evolution.pop(key, None)
    evolution["init_program"] = _file_identity(
        getattr(evo_config, "init_program_path", None)
    )
    evolution.pop("init_program_path", None)

    database.pop("db_path", None)
    job["eval_program"] = _file_identity(getattr(job_config, "eval_program_path", None))
    job.pop("eval_program_path", None)
    return {
        "evolution": _hash_mapping(evolution),
        "database": _hash_mapping(database),
        "job": _hash_mapping(job),
    }


def source_tree_identity(package_dir: Path | str | None = None) -> dict[str, str]:
    """Return a stable identifier for the installed Shinka Python source tree."""
    root = (
        Path(package_dir).resolve()
        if package_dir is not None
        else Path(__file__).resolve().parents[1]
    )
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return {"shinka_version": __version__, "python_source_sha256": digest.hexdigest()}


def runtime_identity(
    named_generators: Mapping[str, np.random.Generator],
) -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "python_random_implementation": _qualified_type(random._inst),
        "numpy_legacy_implementation": _qualified_type(np.random.mtrand._rand),
        "named_bit_generators": {
            name: _qualified_type(generator.bit_generator)
            for name, generator in sorted(named_generators.items())
        },
    }


def capture_rng_states(
    named_generators: Mapping[str, np.random.Generator],
) -> dict[str, Any]:
    """Capture RNG state without drawing from any stream."""
    return {
        "python_global": copy.deepcopy(random.getstate()),
        "numpy_legacy_global": copy.deepcopy(np.random.get_state()),
        "named_generators": {
            name: {
                "bit_generator": _qualified_type(generator.bit_generator),
                "state": copy.deepcopy(generator.bit_generator.state),
            }
            for name, generator in sorted(named_generators.items())
        },
    }


def _validated_rng_states(
    state: Mapping[str, Any],
    named_generators: Mapping[str, np.random.Generator],
) -> tuple[Any, Any, Mapping[str, Any]]:
    try:
        python_state = copy.deepcopy(state["python_global"])
        numpy_state = copy.deepcopy(state["numpy_legacy_global"])
        saved_named = state["named_generators"]
    except (KeyError, TypeError) as exc:
        raise CheckpointCompatibilityError(
            "Checkpoint RNG state is incomplete"
        ) from exc
    if not isinstance(saved_named, Mapping):
        raise CheckpointCompatibilityError("Named RNG state must be a mapping")
    if set(saved_named) != set(named_generators):
        raise CheckpointCompatibilityError(
            "Named RNG registry mismatch: "
            f"checkpoint={sorted(saved_named)} current={sorted(named_generators)}"
        )

    # Probe every state first so a malformed named state cannot leave globals
    # partially restored.
    try:
        python_probe = random.Random()
        python_probe.setstate(python_state)
        numpy_probe = np.random.RandomState()
        numpy_probe.set_state(numpy_state)
        for name, generator in named_generators.items():
            saved = saved_named[name]
            expected_type = _qualified_type(generator.bit_generator)
            if saved.get("bit_generator") != expected_type:
                raise CheckpointCompatibilityError(
                    f"BitGenerator mismatch for {name}: "
                    f"checkpoint={saved.get('bit_generator')} current={expected_type}"
                )
            probe = type(generator.bit_generator)()
            probe.state = copy.deepcopy(saved["state"])
    except CheckpointCompatibilityError:
        raise
    except Exception as exc:
        raise CheckpointCompatibilityError(
            f"Checkpoint RNG state is not restorable: {exc}"
        ) from exc
    return python_state, numpy_state, saved_named


def validate_rng_states(
    state: Mapping[str, Any],
    named_generators: Mapping[str, np.random.Generator],
) -> None:
    """Validate RNG compatibility without mutating any current stream."""
    _validated_rng_states(state, named_generators)


def restore_rng_states(
    state: Mapping[str, Any],
    named_generators: Mapping[str, np.random.Generator],
) -> None:
    """Validate all RNG states, then restore globals and named generators."""
    python_state, numpy_state, saved_named = _validated_rng_states(
        state,
        named_generators,
    )

    random.setstate(python_state)
    np.random.set_state(numpy_state)
    for name, generator in named_generators.items():
        generator.bit_generator.state = copy.deepcopy(saved_named[name]["state"])


def validate_checkpoint_identity(
    payload: Mapping[str, Any],
    *,
    expected_source: Mapping[str, Any],
    expected_config_hashes: Mapping[str, str],
    expected_runtime: Mapping[str, Any],
) -> None:
    """Validate schema, clean marker, source/config, and runtime identity."""
    schema_version = payload.get("schema_version")
    if schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointCompatibilityError(
            f"Unsupported checkpoint schema {schema_version}; "
            f"expected {CHECKPOINT_SCHEMA_VERSION}"
        )
    if payload.get("clean") is not True:
        raise UncleanCheckpointError("Strict resume requires a clean checkpoint")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping):
        raise CheckpointCompatibilityError("Checkpoint identity is missing")
    if identity.get("source") != dict(expected_source):
        raise CheckpointCompatibilityError("Shinka source-tree identity changed")
    if identity.get("configuration_hashes") != dict(expected_config_hashes):
        raise CheckpointCompatibilityError("Selection-relevant configuration changed")
    if identity.get("runtime") != dict(expected_runtime):
        raise CheckpointCompatibilityError(
            "Python, NumPy, or RNG implementation version changed"
        )
