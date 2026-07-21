"""Validation for the explicit secure runner mode."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import NetworkMode, ResourceLimits
from .errors import ConfigurationError


@dataclass(frozen=True)
class SecureRuntimeSettings:
    state_root: Path
    agents: frozenset[str]
    credentials: Mapping[str, Mapping[str, str]]
    network: NetworkMode
    limits: ResourceLimits


def _headless_agent(model: str) -> str:
    if not model.startswith("headless/"):
        raise ConfigurationError(
            f"Secure mutation requires headless models, got {model!r}"
        )
    agent = model.split("headless/", 1)[1].split("@", 1)[0].split("?", 1)[0]
    if not agent:
        raise ConfigurationError("Secure mutation model is missing an agent name")
    return agent


def validate_secure_runtime_configuration(
    evo_config: Any,
    *,
    results_dir: Path,
) -> SecureRuntimeSettings:
    agents = frozenset(_headless_agent(str(model)) for model in evo_config.llm_models)
    if not agents:
        raise ConfigurationError("Secure mode requires at least one Headless agent")

    missing_profiles = sorted(agents - set(evo_config.agent_auth_profiles))
    if missing_profiles:
        raise ConfigurationError(
            "Secure mode is missing agent auth profiles for: "
            + ", ".join(missing_profiles)
        )
    for agent in sorted(agents):
        path = Path(evo_config.agent_auth_profiles[agent]).expanduser()
        if path.is_symlink() or not path.is_dir():
            raise ConfigurationError(
                f"Secure auth profile for {agent} is missing or unsafe: {path}"
            )

    credential_names = evo_config.agent_credential_env_names or {}
    unexpected_agents = sorted(set(credential_names) - agents)
    if unexpected_agents:
        raise ConfigurationError(
            "Credential environment names configured for unused agents: "
            + ", ".join(unexpected_agents)
        )
    credentials: dict[str, dict[str, str]] = {}
    for agent in sorted(agents):
        names = list(credential_names.get(agent, ()))
        missing = sorted(name for name in names if not os.environ.get(name))
        if missing:
            raise ConfigurationError(
                f"Secure auth environment for {agent} is missing: {', '.join(missing)}"
            )
        credentials[agent] = {name: os.environ[name] for name in names}

    try:
        network = NetworkMode(evo_config.agent_network)
    except ValueError as exc:
        raise ConfigurationError(
            "evo.agent_network must be 'disabled' or 'provider_only'"
        ) from exc
    if network is NetworkMode.PROVIDER_ONLY and (
        not evo_config.agent_provider_network or not evo_config.agent_provider_proxy
    ):
        raise ConfigurationError(
            "provider_only secure mutation requires evo.agent_provider_network "
            "and evo.agent_provider_proxy"
        )

    limits = ResourceLimits(
        cpus=evo_config.sandbox_cpus,
        memory_bytes=evo_config.sandbox_memory_bytes,
        pids=evo_config.sandbox_pids,
        open_files=evo_config.sandbox_open_files,
        output_bytes=evo_config.sandbox_output_bytes,
    )
    state_root = Path(
        evo_config.secure_state_root or results_dir / ".secure-state"
    ).expanduser()
    return SecureRuntimeSettings(
        state_root=state_root.resolve(),
        agents=agents,
        credentials=credentials,
        network=network,
        limits=limits,
    )
