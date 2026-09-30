from __future__ import annotations

from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import uuid

from botocore.exceptions import ClientError
import pytest

from shinka.launch.aws import (
    AwsTransport,
    AwsSecureEvaluationScheduler,
    SCHEMA,
    prepared_from_dict,
)
from shinka.launch.secure import SecureEvaluationScheduler, SecureJobConfig
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.canonical import canonical_json_bytes, digest_json
from shinka.secure.contracts import (
    EnvironmentContract,
    EvaluatorContract,
    JobSpec,
    JobStatus,
    PublicTaskContract,
    ResourceLimits,
    ResultManifest,
)
from shinka.secure.containers import ContainerPlan
from shinka.secure.coordinator import PreparedTask, SecureEvaluationCoordinator
from shinka.secure.dependencies import DependencyBundle
from shinka.secure.errors import ArtifactIntegrityError, ConfigurationError
from shinka.secure.aws_worker import cleanup, process_message
from shinka.secure.jobs import JobPhase

IMAGE = "example.invalid/nnue@sha256:" + "a" * 64


def error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "test")


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise error("404")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket, Key):
        head = self.head_object(Bucket=Bucket, Key=Key)
        return {**head, "Body": io.BytesIO(self.objects[Key])}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        assert kwargs["ServerSideEncryption"] == "AES256"
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise error("PreconditionFailed")
        self.objects[Key] = Body

    def upload_file(self, path, bucket, key, ExtraArgs):
        assert ExtraArgs["ServerSideEncryption"] == "AES256"
        self.objects[key] = Path(path).read_bytes()


class MemorySQS:
    def __init__(self):
        self.messages = []
        self.deleted = []
        self.heartbeats = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    def change_message_visibility(self, **kwargs):
        self.heartbeats.append(kwargs)


@pytest.fixture
def transport():
    return AwsTransport(
        region="us-east-1",
        bucket="test",
        queue_url="https://sqs.test/jobs.fifo",
        s3=MemoryS3(),
        sqs=MemorySQS(),
    )


def prepared(store, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir(exist_ok=True)
    (seed / "code.cpp").write_text("candidate")
    ref, snapshot = store.put_tree(seed, kind="candidate", excludes=())
    return PreparedTask(
        candidate=ref,
        evaluator=ref,
        dependencies=DependencyBundle(ref, ref, ref.digest, ("net",)),
        task_contract=PublicTaskContract(
            "test", "speed", "framed_stdio_v1", public_metric_allowlist=("speed",)
        ),
        evaluator_contract=EvaluatorContract(
            "evaluate.py", public_metric_allowlist=("speed",)
        ),
        environment=EnvironmentContract(IMAGE, IMAGE, IMAGE),
        candidate_snapshot=snapshot,
        evaluator_snapshot=snapshot,
    )


@pytest.fixture
def scheduler(tmp_path, transport, monkeypatch):
    def initialize(self, **kwargs):
        self.config = kwargs["config"]
        self.coordinator = SecureEvaluationCoordinator(
            kwargs.get("state_root", tmp_path / "state")
        )
        self.prepared = prepared(self.coordinator.artifacts, tmp_path)
        self._run_id = "run"

    monkeypatch.setattr(SecureEvaluationScheduler, "__init__", initialize)
    monkeypatch.setattr("shinka.launch.aws.AwsTransport", lambda **kwargs: transport)
    return AwsSecureEvaluationScheduler(
        config=SecureJobConfig(
            backend="aws",
            aws_region="us-east-1",
            aws_bucket="test",
            aws_queue_url="https://sqs.test/jobs.fifo",
            public_metric_allowlist=["speed"],
        )
    )


def test_artifact_transport_rejects_tampering_and_atomic_duplicate_results(
    tmp_path, transport
):
    store = ContentAddressedStore(tmp_path / "local")
    remote = ContentAddressedStore(tmp_path / "remote")
    ref = store.put_bytes(b"expected", kind="test")
    transport.upload(store, ref)
    assert transport.download(remote, ref).read_bytes() == b"expected"
    transport.s3.objects[transport.key(f"objects/{ref.digest[7:]}")] = b"tampered"
    with pytest.raises(ArtifactIntegrityError, match="digest"):
        transport.download(ContentAddressedStore(tmp_path / "other"), ref)
    assert transport.put_json_once("result", {"first": True})
    assert not transport.put_json_once("result", {"first": False})
    assert transport.get_json("result") == {"first": True}


def test_prepared_task_roundtrip_preserves_every_identity(tmp_path):
    task = prepared(ContentAddressedStore(tmp_path / "store"), tmp_path)
    copy = prepared_from_dict(json.loads(canonical_json_bytes(asdict(task))))
    assert task == copy
    assert copy.environment.digest == task.environment.digest


def test_submission_is_durable_idempotent_and_uses_independent_fifo_groups(
    tmp_path, scheduler
):
    first = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    assert first == scheduler.submit_async(str(tmp_path / "seed"), "unused")
    (tmp_path / "seed/code.cpp").write_text("new candidate")
    second = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    assert first != second
    assert scheduler.transport.sqs.messages[0]["MessageGroupId"] == first
    assert scheduler.transport.sqs.messages[-1]["MessageGroupId"] == second
    assert (
        scheduler._request(first)["candidate"]["digest"]
        != scheduler._request(second)["candidate"]["digest"]
    )
    assert scheduler.check_job_status(first)


def test_new_state_is_an_independent_repeat_but_resume_keeps_identity(
    tmp_path, scheduler
):
    first = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    resumed = AwsSecureEvaluationScheduler(config=scheduler.config)
    assert resumed.submit_async(str(tmp_path / "seed"), "unused") == first
    independent = AwsSecureEvaluationScheduler(
        config=scheduler.config, state_root=tmp_path / "independent"
    )
    assert independent.submit_async(str(tmp_path / "seed"), "unused") != first


def result_for(scheduler, job_id):
    request = scheduler._request(job_id)
    task = scheduler.prepared
    spec = JobSpec(
        job_id="inner",
        attempt_id="attempt",
        run_id=request["run_id"],
        individual_id=request["individual_id"],
        candidate_digest=request["candidate"]["digest"],
        runtime_artifact_digest=task.candidate.digest,
        evaluator_digest=task.evaluator.digest,
        dependency_digest=task.dependencies.runtime_artifact.digest,
        environment_digest=task.environment.digest,
        task_contract_digest=task.task_contract.digest,
        evaluator_contract=task.evaluator_contract,
        runtime_image=IMAGE,
        candidate_command=("run",),
    )
    manifest = ResultManifest(
        job_id=spec.job_id,
        run_id=spec.run_id,
        individual_id=spec.individual_id,
        attempt_id=spec.attempt_id,
        candidate_digest=spec.candidate_digest,
        runtime_artifact_digest=spec.runtime_artifact_digest,
        evaluator_digest=spec.evaluator_digest,
        dependency_digest=spec.dependency_digest,
        environment_digest=spec.environment_digest,
        job_spec_digest=spec.digest,
        status=JobStatus.SUCCEEDED,
        correct=True,
        combined_score=1.02,
        public_metrics={"speed": 1.02},
        private_metrics={"sentinel": "SECRET_HOLDOUT"},
    )
    return {
        "job_id": job_id,
        "request_digest": digest_json(request),
        "manifest": asdict(manifest),
        "spec": asdict(spec),
    }


def test_remote_results_validate_inputs_and_never_expose_private_metrics(
    tmp_path, scheduler
):
    job_id = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    result = result_for(scheduler, job_id)
    scheduler.transport.put_json_once(f"jobs/{job_id}/result.json", result)
    public = scheduler.get_job_results(job_id, str(tmp_path / "output"))
    assert public["metrics"]["combined_score"] == 1.02
    assert "SECRET_HOLDOUT" not in json.dumps(public)
    assert "SECRET_HOLDOUT" not in (tmp_path / "output/public_result.json").read_text()
    scheduler._remote_results.clear()
    result["spec"]["candidate_digest"] = "sha256:" + "f" * 64
    scheduler.transport.s3.objects[
        scheduler.transport.key(f"jobs/{job_id}/result.json")
    ] = canonical_json_bytes(result)
    with pytest.raises(ArtifactIntegrityError):
        scheduler.get_job_results(job_id, str(tmp_path / "bad-output"))


def test_unserved_job_eventually_fails_and_requests_cancellation(
    tmp_path, scheduler, monkeypatch
):
    job_id = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    monkeypatch.setattr("shinka.launch.aws.time.time", lambda: 1e20)
    assert not scheduler.check_job_status(job_id)
    assert scheduler.transport.get_json(f"jobs/{job_id}/cancel.json") == {
        "cancelled": True
    }
    assert (
        scheduler.get_job_results(job_id, "unused")["job_failure"]["failure_class"]
        == "queue_timeout"
    )


def test_completed_duplicate_sqs_message_is_acknowledged_without_execution(
    tmp_path, transport, monkeypatch
):
    job_id = str(uuid.uuid4())
    transport.put_json_once(f"jobs/{job_id}/result.json", {"complete": True})
    monkeypatch.setattr(
        "shinka.secure.aws_worker.subprocess.Popen",
        lambda *a, **k: pytest.fail("duplicate was executed"),
    )
    process_message(
        {
            "Body": json.dumps({"schema": SCHEMA, "job_id": job_id}),
            "ReceiptHandle": "receipt",
        },
        transport,
        tmp_path,
    )
    assert len(transport.sqs.deleted) == 1


def test_queue_message_cannot_choose_arbitrary_local_path(tmp_path, transport):
    with pytest.raises(ValueError):
        process_message(
            {
                "Body": json.dumps({"schema": SCHEMA, "job_id": "../../escape"}),
                "ReceiptHandle": "receipt",
            },
            transport,
            tmp_path,
        )
    assert not transport.sqs.deleted


def queued_request(transport):
    job_id = str(uuid.uuid4())
    request = {"schema": SCHEMA, "job_id": job_id, "timeout_seconds": 60}
    transport.put_json_once(f"jobs/{job_id}/request.json", request)
    message = {
        "Body": json.dumps({"schema": SCHEMA, "job_id": job_id}),
        "ReceiptHandle": "receipt",
    }
    return job_id, request, message


def test_pending_cancellation_does_not_launch_a_compiler(
    tmp_path, transport, monkeypatch
):
    job_id, request, message = queued_request(transport)
    transport.put_json_once(f"jobs/{job_id}/cancel.json", {"cancelled": True})
    monkeypatch.setattr(
        "shinka.secure.aws_worker.subprocess.Popen",
        lambda *a, **k: pytest.fail("cancelled job was started"),
    )
    process_message(message, transport, tmp_path)
    result = transport.get_json(f"jobs/{job_id}/result.json")
    assert result["failure_class"] == "cancelled"
    assert result["request_digest"] == digest_json(request)
    assert transport.sqs.deleted
    assert not (tmp_path / "jobs" / job_id).exists()


def test_worker_heartbeats_and_publishes_before_acknowledging(
    tmp_path, transport, monkeypatch
):
    import subprocess

    job_id, request, message = queued_request(transport)
    cleaned = []

    class Process:
        returncode = None
        polls = 0

        def __init__(self, command, **kwargs):
            self.output = Path(command[command.index("--result-file") + 1])
            Path(command[command.index("--state-root") + 1]).mkdir()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.polls += 1
            if self.polls == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            self.output.write_bytes(
                canonical_json_bytes(
                    {
                        "job_id": job_id,
                        "request_digest": digest_json(request),
                        "failure_class": "build_failed",
                    }
                )
            )
            self.returncode = 0
            return 0

    monkeypatch.setattr("shinka.secure.aws_worker.subprocess.Popen", Process)
    monkeypatch.setattr(
        "shinka.secure.aws_worker.cleanup", lambda path: cleaned.append(path)
    )
    original_delete = transport.sqs.delete_message

    def delete(**kwargs):
        assert transport.get_json(f"jobs/{job_id}/result.json") is not None
        original_delete(**kwargs)

    transport.sqs.delete_message = delete
    process_message(message, transport, tmp_path)
    assert len(transport.sqs.heartbeats) == 2
    assert len(cleaned) == 1
    assert transport.sqs.deleted


def test_crashed_child_is_cleaned_before_another_job_can_run(
    tmp_path, transport, monkeypatch
):
    _, _, message = queued_request(transport)
    cleaned = []

    class CrashedProcess:
        returncode = 1

        def __init__(self, command, **kwargs):
            Path(command[command.index("--state-root") + 1]).mkdir()

        def poll(self):
            return 1

    monkeypatch.setattr("shinka.secure.aws_worker.subprocess.Popen", CrashedProcess)
    monkeypatch.setattr(
        "shinka.secure.aws_worker.cleanup", lambda path: cleaned.append(path)
    )
    with pytest.raises(RuntimeError, match="retry"):
        process_message(message, transport, tmp_path)
    assert len(cleaned) == 1
    assert not transport.sqs.deleted


def test_cpu_affinity_is_required_in_container_launch():
    limits = ResourceLimits(cpus=2, memory_bytes=128 * 1024**2, pids=64, cpu_set="2")
    plan = ContainerPlan(
        name="test",
        image=IMAGE,
        command=("run",),
        role="runtime",
        limits=limits,
        labels={
            "shinka.managed": "true",
            "shinka.job_id": "job",
            "shinka.attempt_id": "attempt",
            "shinka.role": "runtime",
        },
    )
    assert "--cpuset-cpus=2" in plan.docker_create_argv()
    with pytest.raises(ConfigurationError):
        ResourceLimits(cpus=2, memory_bytes=128 * 1024**2, pids=64, cpu_set="2;bad")


def test_optional_cpu_affinity_preserves_legacy_job_and_environment_identities(
    tmp_path, scheduler
):
    job_id = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    old_spec = result_for(scheduler, job_id)["spec"]
    old_spec["resources"].pop("cpu_set")
    restored = JobSpec.from_dict(old_spec)
    assert restored.digest == digest_json(old_spec)
    assert restored.to_json_bytes() == canonical_json_bytes(old_spec)
    pinned = replace(restored, resources=replace(restored.resources, cpu_set="2"))
    assert pinned.digest != restored.digest
    assert JobSpec.from_dict(json.loads(pinned.to_json_bytes())) == pinned

    environment = scheduler.prepared.environment
    old_environment = asdict(environment)
    old_environment["limits"].pop("cpu_set")
    assert environment.digest == digest_json(old_environment)
    assert (
        replace(environment, limits=replace(environment.limits, cpu_set="2")).digest
        != environment.digest
    )


@pytest.mark.parametrize(
    "phase", [JobPhase.RUNNING, JobPhase.RESULT_VALIDATED, JobPhase.PERSISTED]
)
def test_worker_cleanup_recovers_active_and_already_persisted_jobs(
    tmp_path, scheduler, monkeypatch, phase
):
    job_id = scheduler.submit_async(str(tmp_path / "seed"), "unused")
    result = result_for(scheduler, job_id)
    spec = JobSpec.from_dict(result["spec"])
    coordinator = scheduler.coordinator
    manifest = coordinator.artifacts.put_bytes(
        canonical_json_bytes(result["manifest"]), kind="result_manifest"
    )
    coordinator.jobs.prepare(spec, idempotency_key="cleanup")
    for state in (
        JobPhase.QUEUED,
        JobPhase.STARTING,
        JobPhase.RUNNING,
        JobPhase.COLLECTING,
        JobPhase.RESULT_VALIDATED,
        JobPhase.PERSISTED,
    ):
        coordinator.jobs.transition(
            spec.job_id,
            state,
            result_digest=manifest.digest if state is JobPhase.RESULT_VALIDATED else None,
        )
        if state is phase:
            break
    monkeypatch.setattr(coordinator.engine, "list_managed", lambda: [])
    monkeypatch.setattr(
        "shinka.secure.aws_worker.SecureEvaluationCoordinator",
        lambda *args, **kwargs: coordinator,
    )
    cleanup(coordinator.state_root)
    record = coordinator.jobs.get(spec.job_id)
    assert record.state is JobPhase.CLEANED
    if phase is not JobPhase.RUNNING:
        assert record.result_digest == manifest.digest
    cleanup(coordinator.state_root)


def test_aws_parallelism_is_not_capped_by_proposer_cpu_count():
    from shinka.core.async_runner import ShinkaEvolveRunner

    runner = object.__new__(ShinkaEvolveRunner)
    runner.verbose = False
    runner.job_config = SecureJobConfig(backend="aws")
    assert runner._validate_concurrency_settings(128, 2, 2, 2)[0] == 128
    runner.job_config = SecureJobConfig(backend="local")
    assert runner._validate_concurrency_settings(128, 2, 2, 2)[0] <= 2


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("SHINKA_STOCKFISH_CAMPAIGN"),
    reason="Requires a prepared Stockfish campaign and Docker",
)
def test_aws_worker_executes_real_stockfish_with_stubbed_s3(
    tmp_path, transport, monkeypatch
):
    """Exercise the real remote executor/build/containers, replacing AWS I/O only."""
    from examples.stockfish_nnue.run import scheduler as make_scheduler
    from shinka.secure.aws_worker import execute
    import subprocess

    campaign = Path(os.environ["SHINKA_STOCKFISH_CAMPAIGN"]).resolve()
    local = make_scheduler(campaign, tmp_path / "preparer", backend="local")
    task = local.prepared
    store = local.coordinator.artifacts
    template = {
        "schema": SCHEMA,
        "prepared": asdict(task),
        "candidate_command": local.config.candidate_command,
        "build_command": local.config.build_command,
        "resources": asdict(local.config.resources),
        "timeouts": asdict(local.config.timeouts),
        "sandbox_user": "65532:65532",
    }
    template_ref = store.put_bytes(canonical_json_bytes(template), kind="aws_task")
    for ref in (
        template_ref,
        task.candidate,
        task.evaluator,
        task.dependencies.artifact,
        task.dependencies.runtime_artifact,
    ):
        transport.upload(store, ref)
    request = {
        "schema": SCHEMA,
        "job_id": str(uuid.uuid4()),
        "run_id": "integration",
        "individual_id": "baseline",
        "template": asdict(template_ref),
        "candidate": asdict(task.candidate),
        "timeout_seconds": 120,
    }
    original = subprocess.run

    def use_local_published_image(command, **kwargs):
        if command[:2] == ["docker", "pull"]:
            assert command[2] in {
                task.environment.runtime_image,
                task.environment.build_image,
            }
            return subprocess.CompletedProcess(command, 0)
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", use_local_published_image)
    result = execute(request, transport, tmp_path / "remote-worker")
    manifest = ResultManifest.from_dict(result["manifest"])
    assert manifest.correct is True
    assert manifest.combined_score > 0
    assert result["request_digest"] == digest_json(request)
    assert manifest.artifacts
    assert all(
        transport.key(f"objects/{ref.digest[7:]}") in transport.s3.objects
        for ref in manifest.artifacts
    )
