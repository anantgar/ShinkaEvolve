"""Paired log-speed scoring; whole AB/BA rounds are the statistical units."""

from __future__ import annotations

import math
import statistics

from scipy.stats import t


def score_pairs(pairs: list[dict], weights: dict[str, float]) -> dict:
    if (
        len(pairs) < 6
        or len(pairs) % 2
        or set(weights) != {"incremental", "refresh", "hot"}
    ):
        raise ValueError("Insufficient pairs or invalid workload weights")
    if not math.isclose(sum(weights.values()), 1.0) or any(
        not math.isfinite(w) or w <= 0 for w in weights.values()
    ):
        raise ValueError("Invalid workload weights")
    logs = []
    workload_logs = {name: [] for name in weights}
    durations = []
    orders = []
    calls = {}
    for pair in pairs:
        if set(pair) != set(weights):
            raise ValueError("Paired round has missing or unexpected workloads")
        order = pair["incremental"].get("order")
        if order not in {"AB", "BA"} or any(
            s.get("order") != order for s in pair.values()
        ):
            raise ValueError("Every workload in a round must share a valid AB/BA order")
        orders.append(order)
        value = 0.0
        for name, weight in weights.items():
            sample = pair[name]
            baseline, candidate = (
                sample["baseline_seconds"],
                sample["candidate_seconds"],
            )
            if not all(
                type(x) in {int, float} and math.isfinite(x) and x > 0
                for x in (baseline, candidate)
            ):
                raise ValueError("Timings must be positive finite numbers")
            if (
                type(sample["baseline_calls"]) is not int
                or type(sample["candidate_calls"]) is not int
                or sample["baseline_calls"] != sample["candidate_calls"]
                or sample["baseline_calls"] <= 0
            ):
                raise ValueError(
                    "Baseline and candidate must do identical positive work"
                )
            if sample["baseline_calls"] != calls.setdefault(
                name, sample["baseline_calls"]
            ):
                raise ValueError("Work per workload changed between paired rounds")
            durations.extend((baseline, candidate))
            log_ratio = math.log(baseline) - math.log(candidate)
            workload_logs[name].append(log_ratio)
            value += weight * log_ratio
        logs.append(value)
    if orders.count("AB") != orders.count("BA"):
        raise ValueError("AB/BA order must be balanced")
    mean = statistics.mean(logs)
    se = statistics.stdev(logs) / math.sqrt(len(logs))
    lower = mean - float(t.ppf(0.95, len(logs) - 1)) * se
    return {
        "combined_score": math.exp(lower),
        "geometric_speedup": math.exp(mean),
        "log_standard_error": se,
        "pair_count": len(logs),
        "minimum_sample_seconds_observed": min(durations),
        "workload_log_standard_errors": {
            name: statistics.stdev(values) / math.sqrt(len(values))
            for name, values in workload_logs.items()
        },
        "workload_speedups": {
            name: math.exp(statistics.mean(values))
            for name, values in workload_logs.items()
        },
    }
