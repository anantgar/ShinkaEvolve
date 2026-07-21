from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from dataclasses import asdict
from pathlib import Path

import pytest

from shinka.repo.secure_worktree import MutabilityViolation, WorktreeManager
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.canonical import canonical_json_bytes
from shinka.secure.containers import (
    ContainerMount,
    ContainerPlan,
    _validate_headless_image_agents,
    normalize_candidate_permissions,
    prepare_bind_source,
)
from shinka.secure.contracts import (
    EnvironmentContract,
    EvaluatorContract,
    JobSpec,
    JobStatus,
    ResourceLimits,
    ResultManifest,
)
from shinka.secure.coordinator import SecureEvaluationCoordinator
from shinka.secure.dependencies import (
    DependencyArtifact,
    DependencyManifest,
    DependencyPreparer,
)
from shinka.secure.errors import (
    ConfigurationError,
    ResultValidationError,
    SecureExecutionError,
    SecurityPolicyError,
)
from shinka.secure.jobs import EvaluationJobStore, JobPhase, MutationPhase
from shinka.secure.mutation import (
    AgentSpec,
    _agent_command,
    _copy_minimal_auth_profile,
)
from shinka.secure.protocol import encode_frame, read_frame

DIGEST = "sha256:" + "1" * 64
OTHER_DIGEST = "sha256:" + "2" * 64
IMAGE = "example.invalid/runtime@sha256:" + "a" * 64


def _limits() -> ResourceLimits:
    return ResourceLimits(cpus=1, memory_bytes=128 * 1024 * 1024, pids=16)


def _spec(*, allowlist: tuple[str, ...] = ("score",)) -> JobSpec:
    return JobSpec(
        job_id="job",
        run_id="run",
        individual_id="individual",
        attempt_id="attempt",
        candidate_digest=DIGEST,
        runtime_artifact_digest=DIGEST,
        evaluator_digest=OTHER_DIGEST,
        dependency_digest=DIGEST,
        environment_digest=OTHER_DIGEST,
        task_contract_digest=DIGEST,
        evaluator_contract=EvaluatorContract(
            entrypoint="evaluate.py",
            public_metric_allowlist=allowlist,
            public_feedback_enabled=True,
            public_feedback_max_chars=32,
        ),
        runtime_image=IMAGE,
        candidate_command=("candidate", "--serve"),
        resources=_limits(),
    )


def _success(spec: JobSpec, **overrides) -> ResultManifest:
    values = {
        "job_id": spec.job_id,
        "run_id": spec.run_id,
        "individual_id": spec.individual_id,
        "attempt_id": spec.attempt_id,
        "candidate_digest": spec.candidate_digest,
        "runtime_artifact_digest": spec.runtime_artifact_digest,
        "evaluator_digest": spec.evaluator_digest,
        "dependency_digest": spec.dependency_digest,
        "environment_digest": spec.environment_digest,
        "job_spec_digest": spec.digest,
        "status": JobStatus.SUCCEEDED,
        "correct": True,
        "combined_score": 1.0,
        "public_metrics": {"score": 1.0},
    }
    values.update(overrides)
    return ResultManifest(**values)


def test_contracts_require_pinned_images_and_exact_result_identity() -> None:
    with pytest.raises(ConfigurationError):
        EnvironmentContract(
            mutation_image="latest",
            build_image=IMAGE,
            runtime_image=IMAGE,
            limits=_limits(),
        )
    spec = _spec()
    result = _success(spec)
    result.validate_against(
        spec,
        public_metric_allowlist=("score",),
        public_feedback_enabled=True,
        public_feedback_max_chars=32,
    )
    forged = _success(spec, candidate_digest=OTHER_DIGEST)
    with pytest.raises(ResultValidationError, match="candidate_digest"):
        forged.validate_against(
            spec,
            public_metric_allowlist=("score",),
            public_feedback_enabled=True,
        )


def test_private_result_namespaces_never_enter_public_view() -> None:
    sentinel = "PRIVATE-SENTINEL-7c2231"
    spec = _spec()
    result = _success(
        spec,
        private_metrics={"private": sentinel},
        operator_diagnostics={"trace": sentinel},
        public_feedback="safe aggregate",
    )
    public = json.dumps(result.public_view(), sort_keys=True)
    assert sentinel not in public
    assert "safe aggregate" in public
    with pytest.raises(ResultValidationError, match="non-allowlisted"):
        _success(spec, public_metrics={"secret_case": sentinel}).validate_against(
            spec,
            public_metric_allowlist=("score",),
            public_feedback_enabled=True,
        )


def test_container_plan_has_hardened_exact_policy(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    plan = ContainerPlan(
        name="shinka-test-attempt",
        image=IMAGE,
        command=("candidate", "--serve"),
        role="runtime",
        labels={
            "shinka.managed": "true",
            "shinka.job_id": "job",
            "shinka.attempt_id": "attempt",
            "shinka.role": "runtime",
        },
        limits=_limits(),
        mounts=(ContainerMount(candidate, "/candidate", read_only=True),),
        environment={"HOME": "/tmp/home"},
        allowed_environment_names=frozenset({"HOME"}),
        workdir="/candidate",
    )
    argv = plan.docker_create_argv()
    rendered = " ".join(argv)
    for required in (
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--network=none",
        "--read-only",
        "--pids-limit=16",
    ):
        assert required in rendered
    assert "docker.sock" not in rendered
    with pytest.raises(SecurityPolicyError):
        ContainerMount(Path.home(), "/home", read_only=True)


def test_secure_mutation_invokes_headless_adapter() -> None:
    command, stdin_data = _agent_command(
        AgentSpec(
            agent="antigravity",
            model="Gemini 3.5 Flash (Low)",
        ),
        "reply exactly ok",
    )

    assert command == (
        "headless",
        "antigravity",
        "--model",
        "Gemini 3.5 Flash (Low)",
        "--allow",
        "yolo",
        "--json",
    )
    assert stdin_data == b"reply exactly ok"


@pytest.mark.parametrize(
    "agent",
    ["antigravity", "claude", "codex", "cursor", "gemini", "opencode", "pi"],
)
def test_secure_mutation_supports_every_pinned_native_headless_agent(
    agent: str,
) -> None:
    command, stdin_data = _agent_command(
        AgentSpec(agent=agent, model="model-name", effort="low"),
        "prompt",
    )

    assert command == (
        "headless",
        agent,
        "--model",
        "model-name",
        "--reasoning-effort",
        "low",
        "--allow",
        "yolo",
        "--json",
    )
    assert stdin_data == b"prompt"


def test_secure_mutation_rejects_dynamic_acp_agents() -> None:
    with pytest.raises(SecurityPolicyError, match="Unsupported secure mutation agent"):
        AgentSpec(agent="acp")


def test_mutation_image_declares_every_selected_headless_agent() -> None:
    image = {
        "Config": {
            "Labels": {
                "io.shinka.headless.agents": "claude,codex,cursor",
                "io.shinka.headless.version": "0.4.0",
                "io.shinka.claude.version": "1",
                "io.shinka.codex.version": "1",
                "io.shinka.cursor.version": "1",
                "io.shinka.sandbox-user": "65532:65532",
            }
        }
    }

    _validate_headless_image_agents(image, ["codex", "cursor"])
    with pytest.raises(SecureExecutionError, match="agent contract"):
        _validate_headless_image_agents(image, ["antigravity", "codex"])


def test_antigravity_auth_profile_requires_container_oauth_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()

    with pytest.raises(SecurityPolicyError, match="antigravity-oauth-token"):
        _copy_minimal_auth_profile(source, destination, "antigravity")


def test_bind_sources_support_remapped_non_root_uids_without_persisting_modes(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    regular = candidate / "source.py"
    executable = candidate / "serve"
    regular.write_text("value = 1\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    regular.chmod(0o600)
    executable.chmod(0o700)

    prepare_bind_source(candidate, writable=True)
    assert candidate.stat().st_mode & 0o777 == 0o777
    assert regular.stat().st_mode & 0o777 == 0o666
    assert executable.stat().st_mode & 0o777 == 0o777

    normalize_candidate_permissions(candidate)
    assert candidate.stat().st_mode & 0o777 == 0o755
    assert regular.stat().st_mode & 0o777 == 0o644
    assert executable.stat().st_mode & 0o777 == 0o755


def test_dependency_prefetch_is_hash_pinned_and_offline_bundle_hides_url(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "tool.bin"
    payload.write_bytes(b"trusted bytes")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    store = ContentAddressedStore(tmp_path / "cas")
    manifest = DependencyManifest(
        artifacts=(
            DependencyArtifact(
                name="tool.bin",
                url=payload.as_uri(),
                sha256=digest,
                size=len(payload.read_bytes()),
            ),
        )
    )
    bundle = DependencyPreparer(store).prepare(manifest)
    materialized = tmp_path / "materialized"
    store.materialize_archive(bundle.runtime_artifact, materialized)
    assert (materialized / "files" / "tool.bin").read_bytes() == b"trusted bytes"
    assert payload.as_uri() not in (materialized / "manifest.json").read_text()
    wrong = DependencyManifest(
        artifacts=(
            DependencyArtifact(name="tool.bin", url=payload.as_uri(), sha256="0" * 64),
        )
    )
    with pytest.raises(Exception, match="hash mismatch"):
        DependencyPreparer(store).prepare(wrong)


def test_worktree_diff_never_executes_candidate_controlled_git_config(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required to create the synthetic agent repository")
    seed = tmp_path / "seed"
    (seed / "src").mkdir(parents=True)
    (seed / "src" / "value.txt").write_text("old", encoding="utf-8")
    manager = WorktreeManager(
        seed_repo_path=str(seed),
        worktree_root=str(tmp_path / "worktrees"),
        mutable_paths=["src"],
    )
    parent = manager.initialize_seed_repo()
    child = manager.create_child_worktree(
        parent_commit=parent, generation=1, individual_id="individual"
    )
    marker = tmp_path / "host-command-ran"
    with (child.path / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[core]\n\tfsmonitor = touch {marker}\n")
    (child.path / "src" / "value.txt").write_text("new", encoding="utf-8")

    snapshot = manager.diff_parent(child.path, child.parent_digest)
    manager.validate_snapshot(child, snapshot)
    committed = manager.commit_child(child)

    assert snapshot.changed_files == ["src/value.txt"]
    assert committed.candidate_digest and committed.candidate_digest.startswith(
        "sha256:"
    )
    assert not marker.exists()
    with tarfile.open(manager.artifacts.verify(committed.candidate_digest)) as archive:
        assert not any(name.startswith(".git") for name in archive.getnames())


def test_worktree_rejects_changes_outside_mutable_paths(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "locked.txt").write_text("old", encoding="utf-8")
    manager = WorktreeManager(
        seed_repo_path=str(seed),
        worktree_root=str(tmp_path / "worktrees"),
        mutable_paths=["src"],
    )
    parent = manager.initialize_seed_repo()
    child = manager.create_child_worktree(
        parent_commit=parent, generation=1, individual_id="individual"
    )
    (child.path / "locked.txt").write_text("new", encoding="utf-8")
    snapshot = manager.diff_parent(child.path, child.parent_digest)
    with pytest.raises(MutabilityViolation):
        manager.validate_snapshot(child, snapshot)


@pytest.mark.parametrize(
    "field_name",
    ("mutable_paths", "immutable_paths", "omitted_paths", "ignore_paths"),
)
def test_secure_worktree_rejects_policy_paths_outside_candidate(
    tmp_path: Path,
    field_name: str,
) -> None:
    values = {
        "seed_repo_path": str(tmp_path / "seed"),
        "worktree_root": str(tmp_path / "worktrees"),
        field_name: ["../outside"],
    }
    with pytest.raises(SecurityPolicyError, match="candidate root"):
        WorktreeManager(**values)


class _NoContainerEngine:
    executable = "docker"

    def list_managed(self):
        return []


def test_mutation_launch_intent_is_durable_before_container_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "jobs.sqlite"
    store = EvaluationJobStore(path)
    prepared = store.prepare_mutation(
        attempt_id="mutation-attempt",
        job_id="individual",
        parent_digest=DIGEST,
        prompt_digest=OTHER_DIGEST,
        image=IMAGE,
        agent="codex",
        model="gpt-test",
        container_name="shinka-mutation-attempt",
    )
    assert prepared.state is MutationPhase.PREPARED
    assert EvaluationJobStore(path).get_mutation("mutation-attempt") == prepared

    store.record_mutation_launch("mutation-attempt", container_id="container-id")
    store.mark_mutation_output_pending("mutation-attempt")
    store.mark_mutation_cleaned("mutation-attempt")
    completed = store.complete_mutation(
        "mutation-attempt", candidate_digest=OTHER_DIGEST
    )

    assert completed.state is MutationPhase.SUCCEEDED
    assert completed.candidate_digest == OTHER_DIGEST
    assert completed.cleanup_at is not None
    assert [
        event["event_type"] for event in store.mutation_events("mutation-attempt")
    ] == [
        "prepared",
        "container_created",
        "agent_completed",
        "container_cleaned",
        "candidate_persisted",
    ]


def test_reconcile_recovers_valid_staged_result(tmp_path: Path) -> None:
    coordinator = SecureEvaluationCoordinator(
        tmp_path / "state", engine=_NoContainerEngine()
    )
    spec = _spec()
    staging = coordinator.state_root / "workers" / spec.attempt_id / "result.json"
    record = coordinator.jobs.prepare(
        spec, idempotency_key="evaluation:key", staging_path=str(staging)
    )
    for phase in (
        JobPhase.QUEUED,
        JobPhase.STARTING,
        JobPhase.RUNNING,
        JobPhase.COLLECTING,
    ):
        record = coordinator.jobs.transition(record.job_id, phase)
    staging.parent.mkdir(parents=True)
    staging.write_bytes(canonical_json_bytes(asdict(_success(spec))))

    actions = coordinator.reconcile()

    assert any(action.action == "result_recovered" for action in actions)
    recovered = coordinator.get_result(spec.job_id)
    assert recovered.status is JobStatus.SUCCEEDED
    assert coordinator.jobs.get(spec.job_id).state is JobPhase.RESULT_VALIDATED


def test_restart_reconciliation_cleans_persisted_job_resources(tmp_path: Path) -> None:
    state = tmp_path / "state"
    coordinator = SecureEvaluationCoordinator(state, engine=_NoContainerEngine())
    spec = _spec()
    request = coordinator.requests / f"{spec.attempt_id}.json"
    staging = state / "workers" / spec.attempt_id / "result.json"
    coordinator.jobs.prepare(
        spec,
        idempotency_key="evaluation:cleanup",
        staging_path=str(staging),
    )
    for phase in (
        JobPhase.QUEUED,
        JobPhase.STARTING,
        JobPhase.RUNNING,
        JobPhase.COLLECTING,
    ):
        coordinator.jobs.transition(spec.job_id, phase)
    result = coordinator.artifacts.put_bytes(
        canonical_json_bytes(asdict(_success(spec))),
        kind="result_manifest",
    )
    coordinator.jobs.transition(
        spec.job_id,
        JobPhase.RESULT_VALIDATED,
        result_digest=result.digest,
    )
    coordinator.jobs.transition(spec.job_id, JobPhase.PERSISTED)
    request.write_text("durable request", encoding="utf-8")
    stdout = coordinator.logs / f"{spec.attempt_id}.stdout.log"
    stdout.write_text("worker output", encoding="utf-8")

    restarted = SecureEvaluationCoordinator(state, engine=_NoContainerEngine())
    actions = restarted.reconcile()

    record = restarted.jobs.get(spec.job_id)
    assert any(action.action == "cleaned" for action in actions)
    assert record is not None and record.state is JobPhase.CLEANED
    cleanup_event = restarted.jobs.events(spec.job_id)[-1]
    assert cleanup_event["payload"]["worker_log_digest"].startswith("sha256:")
    assert not request.exists()
    assert not stdout.exists()


def test_framed_protocol_round_trip_and_limit() -> None:
    frame = encode_frame({"type": "response", "id": "1", "output": [1, 2]})
    assert read_frame(__import__("io").BytesIO(frame))["output"] == [1, 2]
    with pytest.raises(Exception):
        encode_frame({"too_big": "x" * 20}, max_bytes=8)
