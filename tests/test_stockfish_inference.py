from __future__ import annotations

import io
import json
import math
import shutil
import statistics
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.stockfish_nnue.corpus import import_pgn, read_tsv, validate_traces
from examples.stockfish_nnue.evaluate import (
    InvalidCandidate,
    measure,
    measurement_rejection,
    reference_runner,
    require_match,
)
from examples.stockfish_nnue.harness.service import receive
from examples.stockfish_nnue.policy import fingerprints, load_manifest, validate_source
from examples.stockfish_nnue.scoring import score_pairs
from examples.stockfish_nnue.seed import (
    PRIVATE_BUILD_FILES,
    prepare_seed,
    restore_build_context,
)
from shinka.launch.secure import SecureEvaluationScheduler, SecureJobConfig
from shinka.repo.secure_worktree import MutabilityViolation, WorktreeManager
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.contracts import ResourceLimits
from shinka.secure.coordinator import SecureEvaluationCoordinator
from shinka.secure.runtime import ContainerCandidateRunner
from shinka.secure.dependencies import (
    DependencyArtifact,
    DependencyManifest,
    DependencyPreparer,
)

TASK = Path(__file__).resolve().parents[1] / "examples/stockfish_nnue"


def samples(ratios):
    return [
        {
            name: {
                "baseline_seconds": ratio,
                "candidate_seconds": 1.0,
                "baseline_calls": 123,
                "candidate_calls": 123,
                "order": "BA" if index % 2 else "AB",
            }
            for name in ("incremental", "refresh", "hot")
        }
        for index, ratio in enumerate(ratios)
    ]


def test_score_uses_paired_log_ratios_and_penalizes_uncertainty():
    weights = {"incremental": 0.6, "refresh": 0.25, "hot": 0.15}
    flat = score_pairs(samples([1.1] * 12), weights)
    noisy = score_pairs(samples([math.exp(x) for x in [0.05, 0.15] * 6]), weights)
    assert flat["combined_score"] == pytest.approx(1.1)
    assert noisy["combined_score"] < noisy["geometric_speedup"]
    assert score_pairs(samples([0.9] * 12), weights)["combined_score"] < 1
    assert score_pairs(samples([1.0] * 12), weights)["combined_score"] == 1


def test_score_matches_independent_calculation_with_correlated_workloads():
    # Known one-sided 95% t critical value, 11 degrees of freedom. Workloads
    # share a round disturbance, so treating their samples independently is wrong.
    critical = 1.7958848187036691
    disturbances = [-0.02, -0.01, 0.0, 0.01, 0.02, 0.03] * 2
    pairs = samples([1.0] * 12)
    for pair, disturbance in zip(pairs, disturbances):
        for name, factor in (("incremental", 2), ("refresh", -1), ("hot", 0.5)):
            pair[name]["baseline_seconds"] = math.exp(factor * disturbance)
    weighted = [1.025 * value for value in disturbances]
    expected_se = statistics.stdev(weighted) / math.sqrt(12)
    actual = score_pairs(pairs, {"incremental": 0.6, "refresh": 0.25, "hot": 0.15})
    assert actual["log_standard_error"] == pytest.approx(expected_se)
    assert actual["combined_score"] == pytest.approx(
        math.exp(statistics.mean(weighted) - critical * expected_se)
    )
    for pair in pairs:
        for sample in pair.values():
            sample["candidate_seconds"] *= 1000
            sample["baseline_seconds"] *= 1000
    assert score_pairs(pairs, {"incremental": 0.6, "refresh": 0.25, "hot": 0.15})[
        "combined_score"
    ] == pytest.approx(actual["combined_score"])


@pytest.mark.parametrize(
    "change",
    [
        "zero",
        "nan",
        "different_work",
        "no_pairs",
        "boolean_calls",
        "changing_work",
        "unbalanced_order",
        "mixed_order",
        "odd_pairs",
        "extra_workload",
    ],
)
def test_score_rejects_invalid_or_unmatched_samples(change):
    pairs = samples([1.0] * 12)
    if change == "zero":
        pairs[0]["hot"]["candidate_seconds"] = 0
    elif change == "nan":
        pairs[0]["hot"]["candidate_seconds"] = float("nan")
    elif change == "different_work":
        pairs[0]["hot"]["candidate_calls"] = 1
    elif change == "boolean_calls":
        pairs[0]["hot"].update(baseline_calls=True, candidate_calls=True)
    elif change == "changing_work":
        pairs[0]["hot"].update(baseline_calls=100, candidate_calls=100)
    elif change == "unbalanced_order":
        for sample in pairs[0].values():
            sample["order"] = "BA"
    elif change == "mixed_order":
        pairs[0]["hot"]["order"] = "BA"
    elif change == "odd_pairs":
        pairs.pop()
    elif change == "extra_workload":
        pairs[0]["other"] = dict(pairs[0]["hot"])
    else:
        pairs = []
    with pytest.raises(ValueError):
        score_pairs(pairs, {"incremental": 0.6, "refresh": 0.25, "hot": 0.15})


def test_source_policy_rejects_build_edits_added_files_and_symlinks(tmp_path):
    root = tmp_path / "seed"
    (root / "src/nnue").mkdir(parents=True)
    (root / "src/nnue/layer.h").write_text("old")
    makefile = root / "src/Makefile"
    makefile.write_text("trusted build")
    original = fingerprints(root)
    (root / "src/nnue/layer.h").write_text("new implementation")
    validate_source(root, original, ["src/nnue"])
    makefile.write_text("untrusted build")
    with pytest.raises(ValueError, match="immutable"):
        validate_source(root, original, ["src/nnue"])
    makefile.write_text("trusted build")
    (root / "src/nnue/new.h").write_text("new file")
    with pytest.raises(ValueError, match="file set"):
        validate_source(root, original, ["src/nnue"])
    (root / "src/nnue/new.h").unlink()
    (root / "src/nnue/link").symlink_to(makefile)
    with pytest.raises(ValueError, match="Symlinks"):
        validate_source(root, original, ["src/nnue"])


def test_checksums_do_not_replace_exact_value_verification():
    baseline = {"calls": 1, "checksum": "123", "values": [2, 3, 2, 3]}
    forged = {**baseline, "values": [2, 4, 2, 4]}
    with pytest.raises(InvalidCandidate, match="Exact"):
        require_match(baseline, forged, checking=True)
    with pytest.raises(InvalidCandidate):
        require_match(baseline, {**baseline, "calls": True})


class FakeRunner:
    def __init__(self, multiplier=1.0):
        self.multiplier = multiplier
        self.requests = []

    def request(self, payload):
        self.requests.append(payload)
        if payload["op"] == "load":
            result = {"loaded": len(payload["traces"])}
        else:
            result = {
                "calls": 1
                if payload["workload"].startswith("check")
                else payload["passes"] * 100,
                "checksum": str(payload["offset"]),
                "values": [10, 12, 10, 12],
                "kernel_ns": 0,
            }
        return SimpleNamespace(output=result, elapsed_seconds=2.0 * self.multiplier)


def test_timing_uses_external_elapsed_and_identical_work():
    settings = load_manifest(TASK / "task_manifest.json")["benchmark"]
    baseline, candidate = FakeRunner(), FakeRunner(0.8)
    measured = measure(
        baseline,
        candidate,
        read_tsv(TASK / "corpus/public_traces.tsv"),
        settings,
        lambda _: None,
    )
    assert measured["combined_score"] == pytest.approx(1.25)
    assert measured["pair_count"] == 24
    assert len([p for p in measured["samples"] if p["hot"]["order"] == "AB"]) == 12
    assert measurement_rejection(measured, settings) is None
    assert all(
        sample["hot"]["baseline_calls"] == sample["hot"]["candidate_calls"]
        for sample in measured["samples"]
    )


def test_exact_correctness_output_is_sharded_before_timing_large_corpus():
    settings = load_manifest(TASK / "task_manifest.json")["benchmark"]
    seed = read_tsv(TASK / "corpus/public_traces.tsv")[0]
    traces = [{**seed, "id": str(i)} for i in range(40)]
    baseline, candidate = FakeRunner(), FakeRunner()
    measured = measure(baseline, candidate, traces, settings, lambda _: None)
    sizes = [len(p["traces"]) for p in candidate.requests if p["op"] == "load"]
    assert sizes == [16, 16, 8] * 3 + [40] + [16, 16, 8]
    assert measured["exact_values_checked"] == 96


def test_duration_floor_checks_actual_pairs_not_only_calibration_probe():
    settings = load_manifest(TASK / "task_manifest.json")["benchmark"]
    measured = score_pairs(samples([2.0] * 12), settings["workload_weights"])
    assert measurement_rejection(measured, settings) is None
    pairs = samples([2.0] * 12)
    pairs[-1]["hot"]["candidate_seconds"] = 0.0001
    measured = score_pairs(pairs, settings["workload_weights"])
    assert "duration floor" in measurement_rejection(measured, settings)


def test_opposing_workload_noise_cannot_hide_behind_a_stable_aggregate():
    settings = load_manifest(TASK / "task_manifest.json")["benchmark"]
    pairs = samples([1.0] * 12)
    for index, pair in enumerate(pairs):
        disturbance = 0.1 if index % 2 else -0.1
        pair["incremental"]["baseline_seconds"] = 2 * math.exp(disturbance)
        pair["refresh"]["baseline_seconds"] = 2 * math.exp(-2.4 * disturbance)
        pair["hot"]["baseline_seconds"] = 2.0
        for sample in pair.values():
            sample["candidate_seconds"] = 2.0
    measured = score_pairs(pairs, settings["workload_weights"])
    assert measured["log_standard_error"] < 1e-10
    assert "workload" in measurement_rejection(measured, settings)


def test_edge_cases_are_checked_but_do_not_change_timing_distribution():
    settings = load_manifest(TASK / "task_manifest.json")["benchmark"]
    fixtures = read_tsv(TASK / "corpus/public_traces.tsv")
    baseline, candidate = FakeRunner(), FakeRunner()
    measure(
        baseline,
        candidate,
        fixtures[:1],
        settings,
        lambda _: None,
        correctness_traces=fixtures,
    )
    latest_ids = []
    for request in candidate.requests:
        if request["op"] == "load":
            latest_ids = [trace["id"] for trace in request["traces"]]
        elif request["workload"] in settings["workload_weights"]:
            assert latest_ids == [fixtures[0]["id"]]


def test_agent_snapshot_history_and_dependencies_contain_no_test_canaries(tmp_path):
    upstream = tmp_path / "upstream"
    files = [
        "src/nnue/kernel.cpp",
        "src/search.cpp",
        "scripts/net.sh",
        "scripts/get_native_properties.sh",
        "AUTHORS",
        "Copying.txt",
        "README.md",
        ".clang-format",
        *PRIVATE_BUILD_FILES,
        "tests/test.py",
        ".github/workflows/test.yml",
    ]
    for name in files:
        path = upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        private = name in PRIVATE_BUILD_FILES or name.startswith(("tests/", ".github/"))
        path.write_text("WITHHELD_TEST_CANARY" if private else "public source")
    seed, context = tmp_path / "seed", tmp_path / "build-context.json"
    prepare_seed(upstream, seed, context)
    assert (seed / "src/search.cpp").read_text() == "public source"
    assert all(
        b"WITHHELD_TEST_CANARY" not in p.read_bytes()
        for p in seed.rglob("*")
        if p.is_file()
    )
    manager = WorktreeManager(
        seed_repo_path=str(seed),
        worktree_root=str(tmp_path / "views"),
        mutable_paths=["src/nnue"],
    )
    parent = manager.initialize_seed_repo()
    child = manager.create_child_worktree(
        parent_commit=parent, generation=1, individual_id="probe"
    )
    view = manager.create_agent_worktree_view(child)
    import subprocess

    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"], cwd=view.path
    ).decode()
    assert (
        "tests/" not in names
        and "benchmark.cpp" not in names
        and "perft.h" not in names
    )
    revisions = (
        subprocess.check_output(["git", "rev-list", "--all"], cwd=view.path)
        .decode()
        .split()
    )
    found = subprocess.run(
        ["git", "grep", "-F", "WITHHELD_TEST_CANARY", *revisions],
        cwd=view.path,
        capture_output=True,
    )
    assert found.returncode == 1
    (view.path / "src/search.cpp").write_text("forbidden engine change")
    with pytest.raises(MutabilityViolation):
        manager.validate_snapshot(
            view, manager.diff_parent(view.path, view.parent_digest)
        )

    net = tmp_path / "net"
    net.write_text("public weights")
    from examples.stockfish_nnue.policy import sha256

    store = ContentAddressedStore(tmp_path / "store")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest(
            artifacts=tuple(
                DependencyArtifact(
                    name=path.name,
                    url=path.as_uri(),
                    sha256=sha256(path),
                    size=path.stat().st_size,
                    runtime=path == net,
                )
                for path in (net, context)
            )
        )
    )
    scheduler = object.__new__(SecureEvaluationScheduler)
    scheduler.config = SecureJobConfig(mutation_dependency_scope="runtime")
    scheduler.prepared = SimpleNamespace(dependencies=bundle)
    store.materialize_archive(
        scheduler.mutation_dependency_artifact, tmp_path / "agent-deps"
    )
    assert not (tmp_path / "agent-deps/files/build-context.json").exists()
    assert (
        b"WITHHELD_TEST_CANARY"
        not in store.verify(scheduler.mutation_dependency_artifact.digest).read_bytes()
    )
    build = tmp_path / "build"
    shutil.copytree(seed, build)
    restore_build_context(build, context)
    assert (build / "src/benchmark.cpp").read_text() == "WITHHELD_TEST_CANARY"


def test_reference_container_shares_cleanup_identity_with_candidate(tmp_path):
    store = ContentAddressedStore(tmp_path / "objects")
    baseline = tmp_path / "baseline.tar"
    baseline.write_bytes(b"baseline")
    ref = store.put_file(baseline, kind="runtime")
    metadata = tmp_path / "baseline.json"
    metadata.write_text(json.dumps({"digest": ref.digest, "size": ref.size}))
    removed = []
    candidate_handle = SimpleNamespace(
        job_id="job", attempt_id="attempt", name="candidate"
    )
    reference_handle = SimpleNamespace(
        job_id="job", attempt_id="attempt", name="reference"
    )
    engine = SimpleNamespace(
        list_managed=lambda: [candidate_handle, reference_handle],
        stop=lambda *args, **kwargs: None,
        remove=lambda handle, **kwargs: removed.append(handle.name),
    )
    candidate = ContainerCandidateRunner(
        engine=engine,
        artifacts=store,
        runtime_artifact=ref,
        dependency_artifact=ref,
        image="test@sha256:" + "a" * 64,
        command=("run",),
        limits=ResourceLimits(cpus=2, memory_bytes=128 * 1024**2, pids=64),
        job_id="job",
        attempt_id="attempt",
        startup_timeout_seconds=60,
        request_timeout_seconds=60,
    )
    private = SimpleNamespace(
        path=lambda key: baseline if key == "baseline" else metadata
    )
    reference = reference_runner(candidate, private)
    assert reference.attempt_id == candidate.attempt_id
    assert reference.container_suffix != candidate.container_suffix
    coordinator = SecureEvaluationCoordinator(tmp_path / "state", engine=engine)
    coordinator._cleanup_job_containers(candidate_handle)
    assert set(removed) == {"candidate", "reference"}


def test_protocol_handles_partial_pipe_reads_and_oversized_frame():
    class Partial(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    payload = json.dumps({"op": "info"}).encode()
    assert receive(Partial(len(payload).to_bytes(4, "big") + payload)) == {"op": "info"}
    with pytest.raises(ValueError):
        receive(io.BytesIO((2**30).to_bytes(4, "big")))


def test_public_special_move_corpus_and_field_validation():
    traces = read_tsv(TASK / "corpus/public_traces.tsv")
    assert {"en_passant", "promotion_knight", "king_mirror_boundary"} <= {
        t["id"] for t in traces
    }
    with pytest.raises(ValueError, match="unique"):
        validate_traces([traces[0], traces[0]])
    with pytest.raises(ValueError):
        validate_traces([{**traces[0], "moves": ["e2e4\nRUN hot 0 0"]}])


def test_pgn_splits_by_deduplicated_game_not_position(tmp_path):
    pytest.importorskip("chess.pgn")
    pgn = tmp_path / "games.pgn"
    pgn.write_text(
        '[Event "a"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n'
        '[Event "same game, different header"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n'
        '[Event "b"]\n\n1. d4 d5 2. c4 e6 *\n'
    )
    public, private = import_pgn(pgn, 17, 0.5)
    assert len(public["traces"]) == len(private["traces"]) == 1
    assert set(public["provenance"]["game_ids"]).isdisjoint(
        private["provenance"]["game_ids"]
    )


def test_large_declared_dependency_includes_manifest_size(tmp_path):
    from examples.stockfish_nnue.policy import sha256

    artifact = tmp_path / "net.nnue"
    artifact.write_bytes(b"0" * (1024 * 1024 + 1))
    store = ContentAddressedStore(tmp_path / "objects")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest(
            artifacts=(
                DependencyArtifact(
                    name="net.nnue",
                    url=artifact.as_uri(),
                    sha256=sha256(artifact),
                    size=artifact.stat().st_size,
                ),
            )
        )
    )
    store.materialize_archive(bundle.runtime_artifact, tmp_path / "runtime")
    assert (tmp_path / "runtime/files/net.nnue").read_bytes() == artifact.read_bytes()
