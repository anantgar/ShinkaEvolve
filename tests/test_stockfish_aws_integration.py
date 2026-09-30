from dataclasses import asdict
import os
from pathlib import Path
import uuid

import pytest

from shinka.launch.aws import SCHEMA
from shinka.secure.canonical import canonical_json_bytes, digest_json
from shinka.secure.contracts import ResultManifest
from test_secure_aws import transport as transport


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
