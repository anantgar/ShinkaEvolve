"""Split real PGNs by game into development, screening and untouched finalists."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

from .corpus import import_pgn, validate_traces


def position_key(case: dict) -> str:
    import chess

    board = chess.Board(case["fen"], chess960=case.get("chess960", False))
    for move in case["moves"]:
        board.push_uci(move)
    # Ignore move clocks as an extra protection against transposition leakage.
    return str(board.chess960) + " " + " ".join(board.fen(en_passant="fen").split()[:4])


def generate(path: Path, seed: int, max_cases: int = 96) -> dict[str, dict]:
    import chess

    if not 3 <= max_cases <= 256:
        raise ValueError("Require 3–256 search cases per split")
    development, private = import_pgn(path, seed, public_fraction=0.2)
    games = list(private["provenance"]["game_ids"])
    if len(games) < 4:
        raise ValueError(
            "Need at least four private games for screening/finalist splits"
        )
    random.Random(seed + 1).shuffle(games)
    cut = len(games) // 2
    outputs = {"development": development}
    for role, selected in (("screening", games[:cut]), ("finalist", games[cut:])):
        prefixes = {game[:20] for game in selected}
        traces = [
            trace
            for trace in private["traces"]
            if trace["id"].split("-")[0] in prefixes
        ]
        outputs[role] = {
            "traces": traces,
            "provenance": {**private["provenance"], "game_ids": selected},
        }
    seen = set()
    for role, data in outputs.items():
        validate_traces(data["traces"])
        # Early/middle/late within each trace segment, chosen before engine runs.
        # Equal phase caps avoid filling the budget with only early positions.
        cases, phases = [], {}
        for phase, fraction in (("early", 0.2), ("middle", 0.5), ("late", 0.8)):
            phase_count = 0
            for trace in data["traces"]:
                count = max(1, round(len(trace["moves"]) * fraction))
                case = {
                    **trace,
                    "id": trace["id"] + "-" + phase,
                    "moves": trace["moves"][:count],
                }
                board = chess.Board(case["fen"], chess960=case["chess960"])
                for move in case["moves"]:
                    board.push_uci(move)
                key = position_key(case)
                if board.is_game_over(claim_draw=True) or key in seen:
                    continue
                # Record every considered position, even when beyond the cap.
                seen.add(key)
                if phase_count < max_cases // 3:
                    cases.append(case)
                    phases[case["id"]] = phase
                    phase_count += 1
        if len(cases) < 3:
            raise ValueError(f"Too few disjoint nonterminal {role} positions")
        data["search_cases"] = cases
        data["provenance"].update(
            role=role,
            split="public" if role == "development" else "holdout",
            generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            sampling="fixed trace-segment fractions 0.2/0.5/0.8; no engine outcome selection",
            search_phases=phases,
            coverage_note="Real-game positions; representativeness depends on supplied PGNs",
        )
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--max-cases", type=int, default=96)
    args = parser.parse_args()
    data = generate(args.pgn, args.seed, args.max_cases)
    args.output.mkdir(parents=True, exist_ok=False)
    for role, corpus in data.items():
        (args.output / f"{role}.json").write_text(json.dumps(corpus, indent=2) + "\n")
    print(
        json.dumps({role: len(corpus["search_cases"]) for role, corpus in data.items()})
    )


if __name__ == "__main__":
    main()
