"""Generate private legal continuations for an exploratory pilot, without games."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

from .corpus import read_tsv, validate_traces
from .policy import sha256


def generate(seed: int) -> dict:
    import chess

    source = Path(__file__).resolve().parent / "corpus/public_traces.tsv"
    rng = random.Random(seed)
    traces, searches = [], []
    # Edge cases remain in the independent exact checker. Search cases need
    # nonterminal positions with legal best moves and useful amounts of work.
    starters = [
        case
        for case in read_tsv(source)
        if case["id"]
        in {
            "italian_castling",
            "queens_gambit",
            "sicilian_capture",
            "rook_endgame",
            "minor_piece_endgame",
            "chess960_castling",
        }
    ]
    for starter in starters:
        for variant in range(4):
            board = chess.Board(starter["fen"], chess960=starter["chess960"])
            moves = list(starter["moves"])
            for value in moves:
                board.push_uci(value)
            for _ in range(8 + 8 * variant):
                if board.is_game_over(claim_draw=True):
                    break
                move = rng.choice(sorted(board.legal_moves, key=lambda m: m.uci()))
                moves.append(board.uci(move, chess960=board.chess960))
                board.push(move)
            case = {**starter, "id": f"pilot-{len(traces):03d}", "moves": moves}
            traces.append(case)
            if variant in (0, 2) and not board.is_game_over(claim_draw=True):
                searches.append(case)
    validate_traces(traces)
    validate_traces(searches)
    return {
        "traces": traces,
        "search_cases": searches,
        "provenance": {
            "kind": "private_synthetic_pilot",
            "split": "pilot",
            "seed": seed,
            "source_sha256": sha256(source),
            "generator_sha256": sha256(Path(__file__)),
            "method": "legal random continuations; no matches or outcome selection",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    data = generate(args.seed)
    with args.output.open("x") as stream:
        stream.write(json.dumps(data, indent=2) + "\n")
    print(
        f"Prepared {len(data['traces'])} private traces and {len(data['search_cases'])} fixed searches"
    )


if __name__ == "__main__":
    main()
