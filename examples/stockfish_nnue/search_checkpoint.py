"""Self-contained private checkpoints without repeated raw search output copies.

Completed blocks are compacted once. Raw outputs are interned by SHA-256 and
compressed once per distinct value. The small timing/history index remains in
each snapshot, so recovery requires only the latest checkpoint, including on AWS.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import zlib

ENCODING = "stockfish-search-checkpoint-v1"
REFERENCE = "$stockfish_output"
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


class SearchCheckpoint:
    def __init__(self, diagnostics: dict, publish):
        self.diagnostics = diagnostics
        self.publish = publish
        self.outputs = []
        self.output_indices = {}
        self.completed = []

    def _output(self, value):
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise ValueError("Search output exceeds the checkpoint bound")
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in self.output_indices:
            self.output_indices[digest] = len(self.outputs)
            self.outputs.append(
                {
                    "sha256": digest,
                    "bytes": len(raw),
                    "zlib_base64": base64.b64encode(zlib.compress(raw, 1)).decode(
                        "ascii"
                    ),
                }
            )
        return {REFERENCE: self.output_indices[digest]}

    def _block(self, block):
        compact = deepcopy(
            {k: v for k, v in block.items() if k not in {"rounds", "observations"}}
        )
        compact["rounds"] = [
            {
                k: self._output(v)
                if k in {"baseline_output", "candidate_output"}
                else v
                for k, v in row.items()
            }
            for row in block["rounds"]
        ]
        if "observations" in block:
            compact["observations"] = [
                {k: self._output(v) if k == "output" else v for k, v in row.items()}
                for row in block["observations"]
            ]
        return compact

    def encode(self) -> bytes:
        data = dict(self.diagnostics)
        section = "measurement" if "measurement" in data else "progress"
        if section in data:
            progress = data[section]
            blocks = progress["blocks"]
            # Only the last block can still change. Never revisit completed raw
            # output history while serializing the next process's checkpoint.
            if self.completed and len(blocks) <= len(self.completed):
                raise ValueError("Search checkpoint history moved backwards")
            while len(self.completed) < max(0, len(blocks) - 1):
                self.completed.append(self._block(blocks[len(self.completed)]))
            data[section] = {
                **progress,
                "blocks": self.completed
                + ([self._block(blocks[-1])] if blocks else []),
            }
        data.update(checkpoint_encoding=ENCODING, output_catalog=self.outputs)
        return json.dumps(data, separators=(",", ":"), allow_nan=False).encode()

    def __call__(self, progress):
        self.diagnostics["progress"] = progress
        blocks = progress["blocks"]
        observations = blocks[-1].get("observations", []) if blocks else []
        if observations and observations[-1].get("warmup"):
            # Retain the live in-memory evidence for the exception handler, but
            # keep bulk serialization and filesystem writes out of warmup→timing.
            return
        self.flush()

    def flush(self):
        return self.publish(self.encode())


def decode_search_checkpoint(payload: dict) -> dict:
    """Restore exact raw evidence; accept the legacy unencoded JSON format."""
    if "checkpoint_encoding" not in payload:
        return payload
    if payload["checkpoint_encoding"] != ENCODING:
        raise ValueError("Unknown search checkpoint encoding")
    data = deepcopy(payload)
    catalog = data.pop("output_catalog")
    data.pop("checkpoint_encoding")
    if not isinstance(catalog, list):
        raise ValueError("Invalid checkpoint output catalog")
    outputs = []
    for entry in catalog:
        size = entry["bytes"]
        if type(size) is not int or not 0 < size <= MAX_OUTPUT_BYTES:
            raise ValueError("Invalid checkpoint output size")
        compressed = base64.b64decode(entry["zlib_base64"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, size + 1)
        if (
            len(raw) != size
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Checkpoint output integrity check failed")
        outputs.append(json.loads(raw))

    def restore(value):
        if not isinstance(value, dict) or set(value) != {REFERENCE}:
            raise ValueError("Invalid checkpoint output reference")
        index = value[REFERENCE]
        if type(index) is not int or not 0 <= index < len(outputs):
            raise ValueError("Missing checkpoint output")
        return outputs[index]

    for section in ("progress", "measurement"):
        if section not in data:
            continue
        for block in data[section]["blocks"]:
            for row in block["rounds"]:
                for key in ("baseline_output", "candidate_output"):
                    if key in row:
                        row[key] = restore(row[key])
            for row in block.get("observations", []):
                row["output"] = restore(row["output"])
    return data
