from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from shinka.secure.build import SecureBuildBackend, _build_diagnostic
from shinka.secure.contracts import (
    EnvironmentContract,
    EvaluatorContract,
    ResourceLimits,
    TimeoutPolicy,
)
from shinka.secure.coordinator import SecureEvaluationCoordinator
from shinka.secure.errors import FailureClass, SecureExecutionError
from shinka.secure.jobs import JobPhase
from shinka.core.async_runner import ShinkaEvolveRunner


def test_build_failure_retains_compiler_output_outside_public_failure(
    tmp_path, monkeypatch
):
    result = SimpleNamespace(
        stdout=b"compiler stdout",
        stderr=b"PRIVATE-COMPILER-REASON",
        exit_code=1,
        timed_out=False,
        output_limited=False,
    )
    failure = SecureExecutionError(
        FailureClass.BUILD_FAILED,
        "Candidate build failed",
        private_diagnostic=_build_diagnostic(result),
    )

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(SecureBuildBackend, "build", fail)
    coordinator = SecureEvaluationCoordinator(tmp_path / "state")
    store = coordinator.artifacts
    candidate = store.put_bytes(b"candidate", kind="candidate")
    evaluator = store.put_bytes(b"evaluator", kind="evaluator")
    dependencies = store.put_bytes(b"deps", kind="dependencies")
    contract = store.put_bytes(b"contract", kind="contract")
    image = "example.invalid/build@sha256:" + "a" * 64
    limits = ResourceLimits(cpus=1, memory_bytes=128 * 1024**2, pids=16)
    prepared = SimpleNamespace(
        environment=EnvironmentContract(
            mutation_image=image, build_image=image, runtime_image=image, limits=limits
        ),
        evaluator=evaluator,
        dependencies=SimpleNamespace(artifact=dependencies),
        task_contract=contract,
        evaluator_contract=EvaluatorContract(entrypoint="evaluate.py"),
    )
    with pytest.raises(SecureExecutionError) as caught:
        coordinator._build_candidate(
            prepared=prepared,
            candidate=candidate,
            build_command=("make",),
            run_id="run",
            individual_id="candidate",
            idempotency_key="key",
            resources=limits,
            timeouts=TimeoutPolicy(),
            sandbox_user="65532:65532",
        )
    digest = caught.value.details["diagnostic_digest"]
    diagnostic = json.loads(store.verify(digest).read_text())
    assert "PRIVATE-COMPILER-REASON" in diagnostic["private_diagnostic"]
    assert "compiler stdout" in diagnostic["private_diagnostic"]
    assert str(caught.value) == "Candidate build failed"
    record = coordinator.jobs.get(caught.value.details["build_job_id"])
    assert record.state is JobPhase.CLEANED
    assert record.failure_class == FailureClass.BUILD_FAILED
    assert record.diagnostic_digest == digest


def test_failure_artifact_references_diagnostics_without_copying_them(tmp_path):
    runner = object.__new__(ShinkaEvolveRunner)
    diagnostic = tmp_path / "private-stderr.log"
    diagnostic.write_text("PRIVATE-REASON")
    metadata = {
        "headless_diagnostic_path": str(diagnostic),
        "operator_diagnostic_digest": "sha256:" + "a" * 64,
        "secure_failure_class": "build_failed",
        "build_job_id": "build",
    }
    artifacts = runner._collect_failure_artifacts(
        generation_dir=str(tmp_path), meta_patch_data=metadata
    )
    assert artifacts["headless_diagnostic_path"] == str(diagnostic)
    assert (
        artifacts["operator_diagnostic_digest"]
        == metadata["operator_diagnostic_digest"]
    )
    assert "PRIVATE-REASON" not in json.dumps(artifacts)
    assert (
        runner._classify_failed_proposal(
            failure_stage="evaluation_submit",
            failure_reason="failed",
            meta_patch_data=metadata,
        )
        == "build_failed"
    )
