"""Trusted evaluator using only the framed candidate-runner capability."""

from __future__ import annotations

import json
import statistics


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def evaluate(job, candidate_runner, private_inputs, result_writer) -> None:
    cases = json.loads(private_inputs.path("cases").read_text(encoding="utf-8"))
    latencies: list[float] = []
    passed = 0

    with result_writer.phase("warmup"):
        for case in cases[:2]:
            candidate_runner.request(case["input"])

    with result_writer.phase("correctness"):
        for case in cases:
            response = candidate_runner.request(case["input"])
            actual = response.output
            expected = case["expected"]
            if (
                isinstance(actual, list)
                and len(actual) == len(expected)
                and all(abs(float(a) - float(b)) <= 1e-9 for a, b in zip(actual, expected))
            ):
                passed += 1

    with result_writer.phase("benchmark"):
        for _ in range(30):
            for case in cases:
                response = candidate_runner.request(case["input"])
                latencies.append(response.elapsed_seconds)
        job.heartbeat("benchmark_complete")

    pass_rate = passed / len(cases)
    correct = passed == len(cases)
    p50 = statistics.median(latencies)
    p90 = _percentile(latencies, 0.90)
    combined_score = (1.0 if correct else 0.0) + 1.0 / (1.0 + p50 * 1_000.0)
    result_writer.succeed(
        correct=correct,
        combined_score=combined_score,
        public_metrics={
            "correctness_pass_rate": pass_rate,
            "latency_p50_seconds": p50,
            "latency_p90_seconds": p90,
        },
        private_metrics={
            "private_case_count": len(cases),
            "trusted_request_samples": len(latencies),
        },
    )
