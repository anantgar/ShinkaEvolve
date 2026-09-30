from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.stockfish_nnue.evaluate import InvalidCandidate
from examples.stockfish_nnue.policy import load_manifest
from examples.stockfish_nnue.search_checkpoint import (
    REFERENCE,
    SearchCheckpoint,
    decode_search_checkpoint,
)
from examples.stockfish_nnue.search_evaluate import measure_search

TASK = Path(__file__).resolve().parents[1] / "examples/stockfish_nnue"
SETTINGS = load_manifest(TASK / "search_manifest.json")["search_benchmark"]


def output(offset=0, count=1):
    records = [
        {
            "depth": 13,
            "seldepth": 18,
            "nodes": 1234 + (index + offset) % 12,
            "score_kind": "cp",
            "score": 16,
            "bound": "",
            "pv": ["e2e4", "e7e5"] * 8,
            "bestmove": "e2e4",
            "ponder": "e7e5",
        }
        for index in range(count)
    ]
    return {
        "calls": count,
        "nodes": sum(row["nodes"] for row in records),
        "checksum": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "records": records,
    }


def test_checkpoint_has_no_bulk_write_between_warmup_and_timing():
    events, payloads = [], []
    diagnostics = {"settings": {**SETTINGS, "process_blocks": 6}}

    def publish(payload):
        events.append("checkpoint")
        payloads.append(payload)

    checkpoint = SearchCheckpoint(diagnostics, publish)

    class Runner:
        stderr = b""

        def __init__(self, role, index):
            self.searches = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def peak_memory_bytes(self):
            return 100000

        def request(self, request):
            if request["op"] == "search_load":
                return SimpleNamespace(output={"loaded": 1})
            self.searches += 1
            events.append("warmup" if self.searches == 1 else "timed")
            return SimpleNamespace(output=output(), elapsed_seconds=2.0)

    result = measure_search(
        Runner, [{}], diagnostics["settings"], lambda _: None, checkpoint=checkpoint
    )
    for index, event in enumerate(events):
        if event == "warmup":
            assert events[index + 1] == "timed"
    assert len(payloads) == 1 + 6 * 5  # initial + four processes + completed block
    decoded = decode_search_checkpoint(json.loads(payloads[-1]))
    assert decoded == diagnostics
    assert decoded["progress"]["blocks"] == result["blocks"]
    assert result["combined_score"] == pytest.approx(1.0)


def test_failed_warmup_is_recoverable_even_when_normal_checkpoint_was_deferred():
    payloads = []
    diagnostics = {}
    checkpoint = SearchCheckpoint(diagnostics, payloads.append)

    class Runner:
        stderr = b""

        def __init__(self, role, index):
            self.role = role

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def peak_memory_bytes(self):
            return 100000

        def request(self, request):
            if request["op"] == "search_load":
                return SimpleNamespace(output={"loaded": 1})
            return SimpleNamespace(
                output=output(int(self.role == "candidate")), elapsed_seconds=2.0
            )

    with pytest.raises(InvalidCandidate):
        measure_search(
            Runner,
            [{}],
            {**SETTINGS, "process_blocks": 6},
            lambda _: None,
            checkpoint=checkpoint,
        )
    # The evaluator's exception handler must force this final snapshot.
    checkpoint.flush()
    recovered = decode_search_checkpoint(json.loads(payloads[-1]))
    assert recovered == diagnostics
    wrong = [
        observation
        for block in recovered["progress"]["blocks"]
        for observation in block["observations"]
        if observation["role"] == "candidate"
    ]
    assert wrong and wrong[-1]["output"] == output(1)


def test_128_block_checkpoints_remain_small_and_self_contained():
    byte_counts, latest = [], []

    def publish(data):
        byte_counts.append(len(data))
        latest[:] = [data]

    diagnostics = {"settings": {**SETTINGS, "process_blocks": 128}}
    checkpoint = SearchCheckpoint(diagnostics, publish)
    progress = {"plan": [], "blocks": []}
    for index in range(128):
        response = output(index % 12, count=96)
        block = {
            "block": index,
            "startup_order": "AB",
            "rounds": [],
            "observations": [],
            "memory_peaks": [],
        }
        progress["blocks"].append(block)
        for position, role in enumerate(
            ("baseline", "candidate", "candidate", "baseline")
        ):
            if position % 2 == 0:
                block["rounds"].append({"order": "AB" if position == 0 else "BA"})
            for warmup in (True, False):
                block["observations"].append(
                    {
                        "role": role,
                        "warmup": warmup,
                        "process": 4 * index + position,
                        "output": response,
                    }
                )
                if not warmup:
                    block["rounds"][-1].update(
                        {
                            f"{role}_output": response,
                            f"{role}_seconds": 2.0 + index / 1000,
                        }
                    )
                checkpoint(progress)
            block["memory_peaks"].append({"role": role, "bytes": 123456})
        checkpoint(progress)
    # A realistic 128-block stream previously wrote tens of GB of raw duplicates.
    # The bound includes every emitted snapshot, not just the final one.
    assert sum(byte_counts) < 64 * 1024**2
    assert max(byte_counts) < 256 * 1024
    encoded = json.loads(latest[-1])
    assert len(encoded["output_catalog"]) == 12
    assert decode_search_checkpoint(encoded) == diagnostics
    # Recovery uses only this single byte string; no earlier artifact is needed.
    assert len(encoded["progress"]["blocks"]) == 128


@pytest.mark.parametrize(
    "corruption", ["size", "digest", "data", "reference", "encoding"]
)
def test_corrupt_checkpoint_cannot_silently_change_raw_evidence(corruption):
    diagnostics = {
        "progress": {
            "plan": [],
            "blocks": [{"rounds": [{"baseline_output": output()}]}],
        }
    }
    encoded = json.loads(SearchCheckpoint(diagnostics, lambda _: None).encode())
    entry = encoded["output_catalog"][0]
    if corruption == "size":
        entry["bytes"] -= 1
    elif corruption == "digest":
        entry["sha256"] = "0" * 64
    elif corruption == "data":
        entry["zlib_base64"] = "a" * 8
    elif corruption == "reference":
        encoded["progress"]["blocks"][0]["rounds"][0]["baseline_output"][REFERENCE] = (
            "missing"
        )
    else:
        encoded["checkpoint_encoding"] = "unknown"
    import zlib

    with pytest.raises((ValueError, zlib.error)):
        decode_search_checkpoint(encoded)


def test_legacy_checkpoint_and_final_measurement_recovery():
    legacy = {"progress": {"plan": [], "blocks": []}}
    assert decode_search_checkpoint(deepcopy(legacy)) == legacy
    diagnostics = {
        "measurement": {
            "blocks": [{"rounds": [{"candidate_output": output()}]}],
            "combined_score": 1.0,
        }
    }
    checkpoint = SearchCheckpoint(diagnostics, lambda _: None)
    assert decode_search_checkpoint(json.loads(checkpoint.encode())) == diagnostics
