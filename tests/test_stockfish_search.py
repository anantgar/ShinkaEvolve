from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
from types import SimpleNamespace

import pytest

from examples.stockfish_nnue.evaluate import InvalidCandidate
from examples.stockfish_nnue.harness.search_service import (
    UciEngine,
    finish_search,
    parse_search_info,
)
from examples.stockfish_nnue.policy import load_manifest
from examples.stockfish_nnue.search_campaign import (
    evaluation_identity,
    validate_control,
    write_manifest,
)
from examples.stockfish_nnue.mini_run import MODEL, configuration
from examples.stockfish_nnue.search_evaluate import (
    measure_search,
    require_search_match,
    validate_search_output,
)
from examples.stockfish_nnue.search_scoring import (
    score_search_blocks,
    search_measurement_rejection,
    validate_search_aa,
)

TASK = Path(__file__).resolve().parents[1] / "examples/stockfish_nnue"
SETTINGS = load_manifest(TASK / "search_manifest.json")["search_benchmark"]


def blocks(logs, repetitions=2):
    return [
        {
            "startup_order": "AB" if index % 2 == 0 else "BA",
            "rounds": [
                {
                    "order": "AB" if pair % 2 == 0 else "BA",
                    "baseline_seconds": 2 * math.exp(value),
                    "candidate_seconds": 2.0,
                    "baseline_calls": 12,
                    "candidate_calls": 12,
                    "baseline_nodes": 1234,
                    "candidate_nodes": 1234,
                }
                for pair in range(repetitions)
            ],
        }
        for index, value in enumerate(logs)
    ]


def test_confidence_uses_independent_processes_not_repeated_observations():
    logs = [-0.01, 0.02, 0.01, 0.03, -0.03, 0.04]
    actual = score_search_blocks(blocks(logs))
    repeated = score_search_blocks(blocks(logs, repetitions=20))
    se = statistics.stdev(logs) / math.sqrt(6)
    assert actual["log_standard_error"] == pytest.approx(se)
    assert repeated["log_standard_error"] == pytest.approx(se)
    # Independent known one-sided 95% t critical value, five degrees of freedom.
    assert actual["combined_score"] == pytest.approx(
        math.exp(statistics.mean(logs) - 2.0150483733330233 * se)
    )
    assert actual["combined_score"] == pytest.approx(repeated["combined_score"])
    assert actual["pair_count"] == 6
    assert actual["timed_pair_count"] == 12


@pytest.mark.parametrize(
    "change",
    [
        "nan",
        "infinite",
        "negative",
        "bool",
        "nodes",
        "work",
        "startup",
        "order",
        "missing",
        "round_count",
    ],
)
def test_score_fails_closed_on_invalid_work_or_plan(change):
    data = blocks([0.0] * 6)
    row = data[0]["rounds"][0]
    if change in {"nan", "infinite", "negative", "bool"}:
        row["candidate_seconds"] = {
            "nan": math.nan,
            "infinite": math.inf,
            "negative": -1,
            "bool": True,
        }[change]
    elif change == "nodes":
        row["candidate_nodes"] += 1
    elif change == "work":
        row.update(candidate_nodes=999, baseline_nodes=999)
    elif change == "startup":
        data[0]["startup_order"] = "BA"
    elif change == "order":
        row["order"] = "BA"
    elif change == "missing":
        data.pop()
    else:
        data[0]["rounds"] *= 2
    with pytest.raises(ValueError):
        score_search_blocks(data)


def test_aa_equivalence_is_stronger_than_interval_containing_one():
    wide = score_search_blocks(blocks([-0.02, 0.02] * 12))
    assert wide["interval_95"][0] < 1 < wide["interval_95"][1]
    with pytest.raises(RuntimeError, match="equivalence"):
        validate_search_aa(wide, SETTINGS)
    validate_search_aa(score_search_blocks(blocks([0.0001, -0.0001] * 12)), SETTINGS)
    with pytest.raises(RuntimeError):
        validate_search_aa(score_search_blocks(blocks([0.004] * 24)), SETTINGS)


def test_noisy_short_or_incomplete_measurements_have_no_fitness():
    measurement = score_search_blocks(blocks([0] * 24))
    assert search_measurement_rejection(measurement, SETTINGS) is None
    for key, value in (
        ("pair_count", 6),
        ("timed_pair_count", 2),
        ("minimum_sample_seconds_observed", 0.1),
        ("log_standard_error", 0.1),
    ):
        assert search_measurement_rejection({**measurement, key: value}, SETTINGS)


def response(nodes=1234):
    record = {
        "depth": 13,
        "seldepth": 18,
        "score_kind": "cp",
        "score": 16,
        "bound": "",
        "pv": ["e2e4", "e7e5"],
        "bestmove": "e2e4",
        "ponder": "e7e5",
        "nodes": nodes,
    }
    records = [record]
    return {
        "calls": 1,
        "nodes": nodes,
        "records": records,
        "checksum": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


@pytest.mark.parametrize(
    "change", ["checksum", "nodes", "score", "bool", "extra", "move", "calls"]
)
def test_search_identity_and_host_checksum_are_required(change):
    good = response()
    validate_search_output(good, 1)
    bad = deepcopy(good)
    if change == "checksum":
        bad["checksum"] = "0" * 64
    elif change == "nodes":
        bad["nodes"] += 1
    elif change == "score":
        bad = response(1235)
    elif change == "bool":
        bad["records"][0]["depth"] = True
    elif change == "extra":
        bad["elapsed_seconds"] = 0.001
    elif change == "move":
        bad["records"][0]["bestmove"] = "d2d4"
    else:
        bad["calls"] = True
    with pytest.raises(InvalidCandidate):
        require_search_match(good, bad, 1)


def test_parser_ignores_engine_reported_time_and_nps():
    prefix = "info depth 13 seldepth 18 multipv 1 score cp 16 nodes 1234 "
    first = parse_search_info(prefix + "nps 1 time 999999 pv e2e4 e7e5")
    second = parse_search_info(prefix + "nps 999999 time 0 pv e2e4 e7e5")
    assert first == second
    assert finish_search(first, "bestmove e2e4 ponder e7e5") == response()["records"][0]
    assert parse_search_info("info string NNUE evaluation using frozen network") is None
    assert parse_search_info("readyok") is None
    with pytest.raises(ValueError):
        finish_search(first, "bestmove d2d4")
    with pytest.raises(ValueError):
        finish_search(None, "bestmove e2e4")


def test_uci_readyok_cannot_finish_search_and_eof_is_failure(tmp_path):
    engine = tmp_path / "engine"
    engine.write_text(
        "#!/usr/bin/env python3\nimport sys\nfor command in sys.stdin:\n"
        " if command.strip() == 'uci': print('uciok', flush=True)\n"
        " elif command.strip() == 'isready': print('readyok', flush=True)\n"
        " elif command.startswith('go'):\n"
        "  print('readyok', flush=True)\n"
        "  print('info depth 13 seldepth 18 score cp 16 nodes 1234 time 0 nps 0 pv e2e4 e7e5', flush=True)\n"
        "  print('bestmove e2e4 ponder e7e5', flush=True)\n"
        " elif command.strip() == 'quit': break\n"
    )
    engine.chmod(0o755)
    uci = UciEngine(engine, tmp_path / "network.nnue", 16)
    try:
        assert (
            uci.search({"fen": "unused", "moves": []}, 13) == response()["records"][0]
        )
        uci.command("quit")
        with pytest.raises(RuntimeError, match="stream ended"):
            uci.until("bestmove")
    finally:
        uci.close()


def test_measurement_uses_fresh_pairs_and_preserves_failed_samples():
    created, snapshots = [], []

    class Runner:
        stderr = b""

        def __init__(self, role, index):
            self.role, self.index, self.calls = role, index, 0
            self.closed = False
            created.append(self)

        def __enter__(self):
            assert sum(not value.closed for value in created) <= 2
            return self

        def __exit__(self, *_):
            self.closed = True

        def peak_memory_bytes(self):
            return 100000

        def request(self, payload):
            if payload["op"] == "search_load":
                return SimpleNamespace(output={"loaded": 1})
            self.calls += 1
            value = response()
            if self.role == "candidate" and self.index == 5 and self.calls == 3:
                value = response(1235)
            return SimpleNamespace(output=value, elapsed_seconds=2.0)

    with pytest.raises(InvalidCandidate, match="fingerprints"):
        measure_search(
            Runner,
            [{"id": "a"}],
            {**SETTINGS, "process_blocks": 6, "process_isolation": "paired"},
            lambda _: None,
            checkpoint=lambda progress: snapshots.append(deepcopy(progress)),
        )
    assert len(created) == 12
    assert all(value.closed for value in created)
    last = snapshots[-1]["blocks"][-1]["rounds"][-1]
    assert last["candidate_output"]["nodes"] == 1235
    assert last["baseline_output"]["nodes"] == 1234


def test_wrong_control_requires_a_real_exact_output_rejection():
    record = {
        "result": {"correct": {"correct": False}},
        "diagnostics": {
            "rejection": "Raw NNUE output stream differs from the reference"
        },
    }
    validate_control("wrong", record, SETTINGS)
    record["diagnostics"]["rejection"] = "Out of memory"
    with pytest.raises(RuntimeError):
        validate_control("wrong", record, SETTINGS)
    record["result"]["job_failure"] = {"failure_class": "worker_lost"}
    with pytest.raises(RuntimeError):
        validate_control("wrong", record, SETTINGS)


def test_manifest_update_rehashes_private_build_dependency(tmp_path):
    (tmp_path / "evaluator").mkdir()
    (tmp_path / "inputs").mkdir()
    dependencies = {
        "artifacts": [{"name": "task-manifest.json", "sha256": "old", "size": 0}]
    }
    (tmp_path / "dependencies.json").write_text(json.dumps(dependencies))
    write_manifest(tmp_path, {"campaign": {"search_work_frozen": True}})
    first = (tmp_path / "evaluator/task-manifest.json").read_bytes()
    assert first == (tmp_path / "inputs/task-manifest.json").read_bytes()
    artifact = json.loads((tmp_path / "dependencies.json").read_text())["artifacts"][0]
    assert artifact["sha256"] == hashlib.sha256(first).hexdigest()
    assert artifact["size"] == len(first)


def test_pilot_launch_rejects_changed_private_data_and_requires_actual_image_canary(
    tmp_path,
):
    import yaml
    from examples.stockfish_nnue.policy import sha256

    campaign = tmp_path / "campaign"
    evaluator = campaign / "evaluator"
    evaluator.mkdir(parents=True)
    (evaluator / "private.json").write_text("original fixture")
    config = yaml.safe_load((TASK / "shinka.yaml").read_text())
    manifest = load_manifest(TASK / "search_manifest.json")
    config["evo"]["mutable_paths"] = manifest["mutable_paths"]
    (campaign / "shinka.yaml").write_text(yaml.safe_dump(config))
    for name in ("calibration.json", "replication.json"):
        (campaign / name).write_text("{}")
    manifest["campaign"] = {
        "frozen": True,
        "evaluation_identity": evaluation_identity(campaign),
        "calibration_sha256": sha256(campaign / "calibration.json"),
        "replication_sha256": sha256(campaign / "replication.json"),
    }
    (evaluator / "task-manifest.json").write_text(json.dumps(manifest))
    auth = tmp_path / "auth"
    auth.mkdir()
    image = "localhost:5000/agent@sha256:" + "a" * 64
    canary = tmp_path / "canary.json"
    canary.write_text(
        json.dumps(
            {
                "model": MODEL,
                "image": image,
                "worktree_mutated": True,
                "session_resumed": True,
                "credentials_removed_from_session": True,
            }
        )
    )
    prepared = configuration(campaign, image, auth, canary)
    assert prepared["evo"]["num_generations"] == 5
    assert prepared["evo"]["generation_target_mode"] == "proposal_ids"
    assert prepared["evo"]["llm_models"] == [MODEL]
    assert prepared["max_evaluation_jobs"] == prepared["max_proposal_jobs"] == 1
    with pytest.raises(ValueError, match="canary"):
        configuration(campaign, "localhost:5000/agent@sha256:" + "b" * 64, auth, canary)
    (evaluator / "private.json").write_text("changed fixture")
    with pytest.raises(ValueError, match="changed after qualification"):
        configuration(campaign, image, auth, canary)


def test_sequential_abba_has_one_live_engine_and_cancels_linear_log_drift():
    created, sequences, alive = [], [], []
    speed = 1.01

    class Runner:
        stderr = b""

        def __init__(self, role, index):
            self.role, self.index = role, index
            created.append((role, index))

        def __enter__(self):
            assert not alive, "two engines would change the allocation/cache context"
            alive.append(self)
            sequences.append(self.role)
            return self

        def __exit__(self, *_):
            assert alive.pop() is self

        def peak_memory_bytes(self):
            return 100000

        def request(self, payload):
            if payload["op"] == "search_load":
                return SimpleNamespace(output={"loaded": 1})
            # Equal temporal positions within ABBA/BAAB cancel this nuisance.
            duration = 2 * math.exp(0.01 * self.index)
            if self.role == "candidate":
                duration /= speed
            return SimpleNamespace(output=response(), elapsed_seconds=duration)

    actual = measure_search(
        Runner, [{"id": "a"}], {**SETTINGS, "process_blocks": 6}, lambda _: None
    )
    assert actual["combined_score"] == pytest.approx(speed)
    assert actual["log_standard_error"] < 1e-12
    assert len(created) == 24
    assert len({index for _, index in created}) == 24
    assert not alive
    assert [sequences[i : i + 4] for i in range(0, len(sequences), 4)].count(
        ["baseline", "candidate", "candidate", "baseline"]
    ) == 3
