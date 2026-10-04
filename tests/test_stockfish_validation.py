from __future__ import annotations

import json
import random
from types import SimpleNamespace
from pathlib import Path

import chess
import chess.pgn
import pytest

from examples.stockfish_nnue import validate_candidate
from examples.stockfish_nnue.validation_corpus import generate, position_key
from examples.stockfish_nnue.search_checkpoint import decode_search_checkpoint
from tests.test_stockfish_search import response, blocks
from examples.stockfish_nnue.search_scoring import score_search_blocks


def pgn_file(tmp_path):
    games = []
    for seed in range(10):
        rng = random.Random(seed)
        board = chess.Board()
        game = chess.pgn.Game()
        node = game
        for _ in range(32):
            if board.is_game_over():
                break
            move = rng.choice(list(board.legal_moves))
            node = node.add_variation(move)
            board.push(move)
        games.append(str(game))
    path = tmp_path / "games.pgn"
    path.write_text("\n\n".join(games))
    return path


def test_real_pgn_splits_reserve_games_and_search_positions(tmp_path):
    path = pgn_file(tmp_path)
    data = generate(path, 17, max_cases=9)
    assert data == generate(path, 17, max_cases=9)
    game_sets, position_sets = [], []
    for role, corpus in data.items():
        assert corpus["provenance"]["role"] == role
        assert len(corpus["search_cases"]) <= 9
        assert set(corpus["provenance"]["search_phases"].values()) == {
            "early",
            "middle",
            "late",
        }
        game_sets.append(set(corpus["provenance"]["game_ids"]))
        position_sets.append({position_key(case) for case in corpus["search_cases"]})
    for index in range(3):
        for other in range(index):
            assert not game_sets[index] & game_sets[other]
            assert not position_sets[index] & position_sets[other]


def args_for(tmp_path, role="screening"):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "search_cases": [
                    {
                        "id": "one",
                        "fen": chess.STARTING_FEN,
                        "moves": [],
                        "chess960": False,
                    }
                ],
                "provenance": {"role": role},
            }
        )
    )
    binary = tmp_path / "binary"
    binary.write_bytes(b"test")
    return SimpleNamespace(
        corpus=corpus,
        baseline=binary,
        candidate=binary,
        network=binary,
        output=tmp_path / "result",
        stage=role,
        depth=6,
        hash_mb=16,
        passes=1,
        control="candidate",
        cpu=None,
    )


def test_timing_requires_native_isolation_and_matching_reserved_split(
    tmp_path, monkeypatch
):
    args = args_for(tmp_path)
    monkeypatch.setattr(validate_candidate.platform, "system", lambda: "Darwin")
    with pytest.raises(ValueError, match="native Linux"):
        validate_candidate.validate(args)
    args.stage = "finalist"
    with pytest.raises(ValueError, match="reserved corpus"):
        validate_candidate.validate(args)
    assert not args.output.exists()


def test_correctness_retains_raw_evidence_and_never_admits_fitness(
    tmp_path, monkeypatch
):
    args = args_for(tmp_path, role="correctness")

    def measure(factory, *, cases, settings, heartbeat, checkpoint):
        checkpoint(
            {
                "plan": [],
                "blocks": [
                    {
                        "rounds": [],
                        "observations": [
                            {"role": "candidate", "warmup": False, "output": response()}
                        ],
                    }
                ],
            }
        )
        return {"blocks": [], "combined_score": 2.0, "geometric_speedup": 2.1}

    monkeypatch.setattr(validate_candidate, "measure_search", measure)
    result = validate_candidate.validate(args)
    assert result["correct"] is True
    assert result["measurement"] is None
    assert result["fitness_admitted"] is False
    assert result["supports_speed_claim"] is False
    raw = decode_search_checkpoint(
        json.loads((args.output / "checkpoint.json").read_text())
    )
    assert raw["progress"]["blocks"][0]["observations"][0]["output"] == response()
    with pytest.raises(FileExistsError):
        validate_candidate.validate(args)


@pytest.mark.parametrize("stage,budget", [("screening", 24), ("finalist", 128)])
@pytest.mark.parametrize("noisy", [False, True])
def test_timing_stages_keep_fixed_budgets_reject_noise_and_never_provide_fitness(
    tmp_path, monkeypatch, stage, budget, noisy
):
    args = args_for(tmp_path, role=stage)
    args.cpu = 2
    monkeypatch.setattr(validate_candidate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        validate_candidate.os, "sched_getaffinity", lambda _: {0, 1, 2}, raising=False
    )
    pinned = []
    monkeypatch.setattr(
        validate_candidate.os,
        "sched_setaffinity",
        lambda pid, cpus: pinned.append(cpus),
        raising=False,
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "boot-identity"
            if str(p) == "/proc/sys/kernel/random/boot_id"
            else original_read(p, *a, **kw)
        ),
    )

    def measure(factory, *, cases, settings, heartbeat, checkpoint):
        assert settings["process_blocks"] == budget
        measurement = score_search_blocks(blocks([0.0] * budget))
        if noisy:
            measurement["log_standard_error"] = 0.1
        return {**measurement, "blocks": []}

    monkeypatch.setattr(validate_candidate, "measure_search", measure)
    result = validate_candidate.validate(args)
    assert pinned == [{0, 1}, {0, 1, 2}]
    assert result["correct"] is True
    assert result["measurement_accepted"] is not noisy
    assert result["fitness_admitted"] is False
    assert result["supports_speed_claim"] is False
    assert "combined_score" not in result["measurement"]
    assert result["protocol_sha256"]


@pytest.mark.parametrize("fail", [False, True])
def test_validation_restores_affinity_for_repeated_calls_and_errors(monkeypatch, fail):
    current = {0, 1, 2}
    monkeypatch.setattr(validate_candidate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        validate_candidate.os, "sched_getaffinity", lambda _: current.copy(), raising=False
    )

    def set_affinity(pid, cpus):
        current.clear()
        current.update(cpus)

    monkeypatch.setattr(
        validate_candidate.os, "sched_setaffinity", set_affinity, raising=False
    )

    def run(args):
        assert args.cpu in current
        set_affinity(0, current - {args.cpu})
        if fail:
            raise RuntimeError("preflight failed")
        return {"complete": True}

    monkeypatch.setattr(validate_candidate, "_validate", run)
    for _ in range(2):
        if fail:
            with pytest.raises(RuntimeError, match="preflight failed"):
                validate_candidate.validate(SimpleNamespace(cpu=2))
        else:
            assert validate_candidate.validate(SimpleNamespace(cpu=2)) == {"complete": True}
        assert current == {0, 1, 2}
