import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from shinka.run_manifest import _command_output, write_run_manifest


def manifest(tmp_path):
    path = write_run_manifest(
        evo_config=SimpleNamespace(evaluation_mode="secure", llm_models=[]),
        db_config={},
        job_config=SimpleNamespace(evaluator_repo_path=str(tmp_path)),
        results_dir=tmp_path / "results",
        effective_workers={"proposal": 1, "evaluation": 1, "database": 1},
        minimum_request_demand={},
    )
    return json.loads(path.read_text())


def test_installed_worker_without_checkout_records_unavailable_git(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    framework = manifest(tmp_path)["framework"]
    assert framework["repo_root"] == str(tmp_path)
    assert framework["git_metadata_available"] is False
    assert framework["git_commit"] is None
    assert framework["dirty"] is None
    assert framework["dirty_status_sha256"] is None
    assert framework["dirty_diff_sha256"] is None


@pytest.mark.parametrize(
    "error", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 15)]
)
def test_unavailable_git_does_not_abort_manifest(tmp_path, monkeypatch, error):
    monkeypatch.chdir(tmp_path)

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert manifest(tmp_path)["framework"]["git_metadata_available"] is False


def test_command_failure_text_is_never_returned_as_data(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 128, b"misleading output", b"fatal: not a git repository"
        ),
    )
    assert _command_output(["git", "rev-parse", "--show-toplevel"]) is None


def test_git_provenance_distinguishes_clean_and_full_dirty_state(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True
        ).stdout

    git("init")
    for name in ("first.txt", "second.txt"):
        (repo / name).write_text("initial\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "initial",
    )
    monkeypatch.chdir(repo)
    clean = manifest(tmp_path)["framework"]
    assert clean["git_commit"] == git("rev-parse", "HEAD").decode().strip()
    assert clean["git_metadata_available"] is True
    assert clean["dirty"] is False
    assert clean["dirty_status_sha256"] == hashlib.sha256(b"").hexdigest()
    assert clean["dirty_diff_sha256"] == hashlib.sha256(b"").hexdigest()

    for name in ("first.txt", "second.txt"):
        (repo / name).write_text("changed\n")
    status = git("status", "--porcelain=v1")
    assert len(status.splitlines()) == 2
    dirty = manifest(tmp_path)["framework"]
    assert dirty["dirty"] is True
    assert dirty["dirty_status_sha256"] == hashlib.sha256(status).hexdigest()
    assert (
        dirty["dirty_diff_sha256"]
        == hashlib.sha256(git("diff", "--binary", "HEAD")).hexdigest()
    )


def test_failed_git_diff_records_unknown_diff_without_hiding_dirty_state(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def command(args, **kwargs):
        if args[1] == "diff":
            raise subprocess.TimeoutExpired(args, 15)
        output = {
            ("rev-parse", "--show-toplevel"): str(tmp_path).encode() + b"\n",
            ("rev-parse", "HEAD"): b"a" * 40 + b"\n",
            ("status", "--porcelain=v1"): b" M changed.py\n",
        }[tuple(args[1:])]
        return subprocess.CompletedProcess(args, 0, output, b"")

    monkeypatch.setattr(subprocess, "run", command)
    framework = manifest(tmp_path)["framework"]
    assert framework["dirty"] is True
    assert framework["dirty_diff_sha256"] is None
