"""Import PGNs with game-level splits, plus deterministic public smoke traces."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random


def read_tsv(path: Path) -> list[dict]:
    traces = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) not in (3, 4):
            raise ValueError("Expected id, FEN, moves, optional Chess960 flag")
        traces.append(
            {
                "id": fields[0],
                "fen": fields[1],
                "moves": fields[2].split(),
                "chess960": len(fields) == 4 and fields[3] == "1",
            }
        )
    validate_traces(traces)
    return traces


def validate_traces(traces: list[dict]) -> None:
    if not isinstance(traces, list) or not 1 <= len(traces) <= 10000:
        raise ValueError("Corpus must have 1–10000 traces")
    identities = set()
    for trace in traces:
        if not isinstance(trace["id"], str) or trace["id"] in identities:
            raise ValueError("Trace ids must be unique strings")
        identities.add(trace["id"])
        if not isinstance(trace["fen"], str) or len(trace["fen"].split()) != 6:
            raise ValueError("Expected a six-field FEN")
        if not isinstance(trace["moves"], list) or len(trace["moves"]) > 192:
            raise ValueError("Trace exceeds 192 plies")
        if type(trace.get("chess960", False)) is not bool:
            raise ValueError("Chess960 flag must be boolean")
        for value in [trace["fen"], *trace["moves"]]:
            if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
                raise ValueError("Unsafe corpus field")


def import_pgn(
    path: Path, seed: int, public_fraction: float = 0.2
) -> tuple[dict, dict]:
    # Optional corpus-preparation dependency, never installed in candidate runtime.
    import chess.pgn

    if not 0 < public_fraction < 1:
        raise ValueError("public_fraction must be between zero and one")
    groups = {}
    with path.open(encoding="utf-8-sig") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors:
                raise ValueError(f"Malformed PGN game: {game.errors[0]}")
            board = game.board()
            initial = board.fen()
            moves = list(game.mainline_moves())
            if not moves:
                continue
            # Ignore headers when deduplicating; identical game content cannot cross splits.
            identity = hashlib.sha256(
                (initial + " " + " ".join(m.uci() for m in moves)).encode()
            ).hexdigest()
            if identity in groups:
                continue
            traces = []
            for begin in range(0, len(moves), 96):
                fen = board.fen()
                segment = moves[begin : begin + 96]
                uci = []
                for move in segment:
                    uci.append(board.uci(move, chess960=board.chess960))
                    board.push(move)
                traces.append(
                    {
                        "id": f"{identity[:20]}-{begin}",
                        "fen": fen,
                        "moves": uci,
                        "chess960": board.chess960,
                    }
                )
            groups[identity] = traces
    if len(groups) < 2:
        raise ValueError("Need at least two distinct games for a disjoint split")
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    cut = max(1, min(len(keys) - 1, round(len(keys) * public_fraction)))
    provenance = {
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "seed": seed,
        "split_unit": "deduplicated_game",
        "kind": "pgn",
    }
    outputs = []
    for split, selection in (("public", keys[:cut]), ("holdout", keys[cut:])):
        traces = [trace for key in selection for trace in groups[key]]
        validate_traces(traces)
        outputs.append(
            {
                "traces": traces,
                "provenance": {**provenance, "split": split, "game_ids": selection},
            }
        )
    return outputs[0], outputs[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=73013)
    parser.add_argument("--public-fraction", type=float, default=0.2)
    args = parser.parse_args()
    public, holdout = import_pgn(args.pgn, args.seed, args.public_fraction)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, data in (("public", public), ("holdout", holdout)):
        (args.output / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
    print(
        f"Wrote {len(public['traces'])} public and {len(holdout['traces'])} holdout traces"
    )


if __name__ == "__main__":
    main()
