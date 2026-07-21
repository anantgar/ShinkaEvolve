from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from shinka.secure.containers import ContainerMount, ContainerPlan, DockerEngine
from shinka.secure.contracts import ResourceLimits


@pytest.mark.integration
def test_live_container_enforces_candidate_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qualify the exact runtime policy against an explicitly supplied image."""

    image = os.environ.get("SHINKA_SECURE_QUALIFICATION_IMAGE")
    if not image:
        pytest.skip("set SHINKA_SECURE_QUALIFICATION_IMAGE to a pinned shell image")
    executable = os.environ.get("SHINKA_CONTAINER_EXECUTABLE", "docker")
    if shutil.which(executable) is None:
        pytest.skip("Docker-compatible CLI is unavailable")

    candidate = tmp_path / "candidate"
    evaluator = tmp_path / "evaluator-private"
    results = tmp_path / "results-private"
    state = tmp_path / "state-private"
    candidate.mkdir()
    evaluator.mkdir()
    results.mkdir()
    state.mkdir()
    (candidate / "public.txt").write_text("public\n", encoding="utf-8")
    sentinel = "SHINKA_HOST_SECRET_9e5c5f8c"
    (evaluator / sentinel).write_text(sentinel, encoding="utf-8")
    (results / sentinel).write_text(sentinel, encoding="utf-8")
    (state / sentinel).write_text(sentinel, encoding="utf-8")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", sentinel)

    suffix = uuid.uuid4().hex[:16]
    plan = ContainerPlan(
        name=f"shinka-qualification-{suffix}",
        image=image,
        command=(
            "/bin/sh",
            "-ceu",
            """
test "$(cat /candidate/public.txt)" = public
test ! -e "$1"
test ! -e "$2"
test ! -e "$3"
test ! -e /var/run/docker.sock
test ! -e /sys/class/net/eth0
test -z "${AWS_SECRET_ACCESS_KEY+x}"
! touch /candidate/forbidden
! touch /root-filesystem-forbidden
touch /tmp/allowed
sleep 300 &
printf secure-ok
""",
            "shinka-qualification",
            str(evaluator / sentinel),
            str(results / sentinel),
            str(state / sentinel),
        ),
        role="runtime",
        labels={
            "shinka.managed": "true",
            "shinka.job_id": f"qualification-{suffix}",
            "shinka.attempt_id": suffix,
            "shinka.role": "runtime",
        },
        limits=ResourceLimits(
            cpus=0.5,
            memory_bytes=64 * 1024 * 1024,
            pids=16,
            open_files=64,
        ),
        mounts=(ContainerMount(candidate, "/candidate", read_only=True),),
        workdir="/candidate",
    )

    engine = DockerEngine(executable)
    engine.preflight(images=(image,))
    handle = engine.create(plan)
    try:
        result = engine.run_capture(
            handle,
            timeout_seconds=20.0,
            max_output_bytes=4096,
        )
        assert result.exit_code == 0
        assert result.stdout == b"secure-ok"
        assert sentinel.encode() not in result.stdout + result.stderr
        assert not (engine.inspect(handle.container_id).get("State") or {}).get(
            "Running"
        )
    finally:
        engine.remove(handle, force=True)

    assert handle.container_id not in {
        item.container_id for item in engine.list_managed()
    }
