from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shinka.launch import SecureJobConfig, validate_secure_job_config
from shinka.secure.configuration import validate_secure_runtime_configuration
from shinka.secure.errors import ConfigurationError

IMAGE = "example.invalid/shinka@sha256:" + "a" * 64


def _evo(tmp_path: Path, **overrides):
    auth = tmp_path / "auth"
    auth.mkdir(exist_ok=True)
    values = {
        "llm_models": ["headless/codex@test"],
        "agent_auth_profiles": {"codex": str(auth)},
        "agent_credential_env_names": {},
        "agent_network": "disabled",
        "agent_provider_network": None,
        "agent_provider_proxy": None,
        "sandbox_cpus": 1.0,
        "sandbox_memory_bytes": 128 * 1024 * 1024,
        "sandbox_pids": 16,
        "sandbox_open_files": 64,
        "sandbox_output_bytes": 4096,
        "secure_state_root": str(tmp_path / "state"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_secure_runtime_configuration_is_read_only_and_explicit(tmp_path: Path) -> None:
    settings = validate_secure_runtime_configuration(
        _evo(tmp_path),
        results_dir=tmp_path / "results",
    )
    assert settings.agents == {"codex"}
    assert settings.credentials == {"codex": {}}
    assert settings.state_root == (tmp_path / "state").resolve()
    assert not settings.state_root.exists()


def test_secure_runtime_configuration_reports_missing_auth(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="missing agent auth profiles"):
        validate_secure_runtime_configuration(
            _evo(tmp_path, agent_auth_profiles={}),
            results_dir=tmp_path / "results",
        )


def test_secure_runtime_configuration_reports_missing_credential_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_TEST_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="CODEX_TEST_TOKEN"):
        validate_secure_runtime_configuration(
            _evo(
                tmp_path,
                agent_credential_env_names={"codex": ["CODEX_TEST_TOKEN"]},
            ),
            results_dir=tmp_path / "results",
        )


def test_secure_job_config_requires_pinned_images() -> None:
    config = SecureJobConfig(
        evaluator_repo_path="evaluator",
        candidate_command=["candidate", "--serve"],
        build_image=IMAGE,
        runtime_image="runtime:latest",
    )
    with pytest.raises(ConfigurationError, match="job.runtime_image"):
        validate_secure_job_config(config, mutation_image=IMAGE)
