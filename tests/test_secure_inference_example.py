from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


def test_inference_example_uses_runner_api_and_trusted_timings(
    tmp_path: Path,
) -> None:
    evaluator_path = (
        Path(__file__).parents[1]
        / "examples"
        / "inference_pipeline_repo"
        / "evaluate.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "secure_example", evaluator_path
    )
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps([{"input": [1.0, 2.0], "expected": [3.0, 5.0]}]),
        encoding="utf-8",
    )

    class Runner:
        def request(self, values):
            return SimpleNamespace(
                output=[2.0 * value + 1.0 for value in values],
                elapsed_seconds=0.001,
            )

    class Writer:
        result = None

        @contextmanager
        def phase(self, _name):
            yield

        def succeed(self, **kwargs):
            self.result = kwargs

    writer = Writer()
    module.evaluate(
        SimpleNamespace(heartbeat=lambda _phase: None),
        Runner(),
        SimpleNamespace(path=lambda _name: cases),
        writer,
    )
    assert writer.result["correct"] is True
    assert writer.result["public_metrics"]["latency_p50_seconds"] == 0.001
