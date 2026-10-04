"""Reproduce the expanded positions from the checksum-pinned public PGN source.

Run from the Shinka checkout with python-chess==1.999. This only prepares data;
it does not launch AWS jobs or run engines. Concrete output belongs outside
mutation snapshots.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random

import chess
import chess.pgn

from examples.stockfish_nnue.validation_corpus import generate, position_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    games, seen, valid = [], set(), 0
    rng = random.Random(20261003)
    with args.pgn.open(encoding="utf-8-sig") as source:
        while (game := chess.pgn.read_game(source)) is not None:
            if game.errors or game.headers.get("Variant", "Standard") not in (
                "Standard",
                "Chess960",
            ):
                continue
            moves = list(game.mainline_moves())
            if len(moves) < 16:
                continue
            identity = hashlib.sha256(
                (game.board().fen() + " " + " ".join(m.uci() for m in moves)).encode()
            ).hexdigest()
            if identity in seen:
                continue
            seen.add(identity)
            valid += 1
            if len(games) < 1000:
                games.append(game)
            elif (index := rng.randrange(valid)) < 1000:
                games[index] = game
    sample = args.output / "sample.pgn"
    sample.write_text(
        "\n\n".join(
            game.accept(
                chess.pgn.StringExporter(headers=True, variations=False, comments=False)
            )
            for game in games
        )
        + "\n"
    )
    assert hashlib.sha256(sample.read_bytes()).hexdigest() == (
        "28da73d91d5981d45604045b895f3c2994ace11caab4c42091d370d33fbfeca0"
    ), "Curated PGN differs; verify source checksum and parser version"
    original = generate(sample, 20261003, max_cases=12)["finalist"]
    seen = {position_key(case) for case in original["search_cases"]}
    traces = list(original["traces"])
    random.Random(20261004).shuffle(traces)
    cases = []
    for phase, fraction in (("early", 0.2), ("middle", 0.5), ("late", 0.8)):
        count = 0
        for trace in traces:
            case = {
                **trace,
                "id": trace["id"] + "-expanded-" + phase,
                "moves": trace["moves"][
                    : max(1, round(len(trace["moves"]) * fraction))
                ],
            }
            board = chess.Board(case["fen"], chess960=case.get("chess960", False))
            for move in case["moves"]:
                board.push_uci(move)
            key = position_key(case)
            if key in seen or board.is_game_over(claim_draw=True):
                continue
            seen.add(key)
            cases.append(case)
            count += 1
            if count == 16:
                break
    assert len(cases) == 48
    digest = hashlib.sha256(
        json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = json.loads(Path(__file__).with_name("case_identity.json").read_text())
    assert digest == manifest["normalized_cases_sha256"], "Position selection differs"
    (args.output / "corpus.json").write_text(
        json.dumps(
            {
                "search_cases": cases,
                "provenance": {
                    "role": "finalist",
                    "sampling": "fixed 16 early/middle/late positions each",
                    "source": "Lichess September 2025 broadcasts",
                    "license": "CC BY-SA 4.0",
                    "normalized_cases_sha256": digest,
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"positions": len(cases), "normalized_cases_sha256": digest}))


if __name__ == "__main__":
    main()
