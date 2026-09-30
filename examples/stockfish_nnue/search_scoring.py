"""Fixed-search fitness with independent process pairs as statistical units."""

from __future__ import annotations

import math
import statistics

from scipy.stats import t


def score_search_blocks(blocks: list[dict]) -> dict:
    if len(blocks) < 6 or len(blocks) % 2:
        raise ValueError("Require an even number of at least six process blocks")
    startup_orders = []
    block_logs = []
    durations = []
    expected_work = None
    round_count = None
    for block in blocks:
        startup = block.get("startup_order")
        if startup not in {"AB", "BA"}:
            raise ValueError("Invalid process startup order")
        startup_orders.append(startup)
        rounds = block.get("rounds")
        if not isinstance(rounds, list) or len(rounds) < 2 or len(rounds) % 2:
            raise ValueError("Each block requires balanced repeated timing pairs")
        if round_count is None:
            round_count = len(rounds)
        if len(rounds) != round_count:
            raise ValueError("All process blocks require the same round count")
        orders = []
        ratios = []
        for sample in rounds:
            if sample.get("order") not in {"AB", "BA"}:
                raise ValueError("Invalid timing order")
            orders.append(sample["order"])
            baseline = sample["baseline_seconds"]
            candidate = sample["candidate_seconds"]
            if any(
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
                for value in (baseline, candidate)
            ):
                raise ValueError("Durations must be positive finite numbers")
            work = tuple(sample[f"baseline_{name}"] for name in ("calls", "nodes"))
            other = tuple(sample[f"candidate_{name}"] for name in ("calls", "nodes"))
            if any(type(value) is not int or value <= 0 for value in (*work, *other)):
                raise ValueError("Work counts must be positive integers")
            if work != other:
                raise ValueError("Baseline and candidate performed different work")
            if expected_work is None:
                expected_work = work
            if work != expected_work:
                raise ValueError("Search work changed between samples")
            durations.extend((baseline, candidate))
            ratios.append(math.log(baseline) - math.log(candidate))
        if orders.count("AB") != orders.count("BA"):
            raise ValueError("Timing order must be balanced inside every block")
        block_logs.append(statistics.mean(ratios))
    if startup_orders.count("AB") != startup_orders.count("BA"):
        raise ValueError("Process startup order must be balanced across blocks")
    mean = statistics.mean(block_logs)
    se = statistics.stdev(block_logs) / math.sqrt(len(block_logs))
    margin = float(t.ppf(0.95, len(block_logs) - 1)) * se
    two_sided = float(t.ppf(0.975, len(block_logs) - 1)) * se
    return {
        "combined_score": math.exp(mean - margin),
        "upper_confidence_score": math.exp(mean + margin),
        "geometric_speedup": math.exp(mean),
        "log_standard_error": se,
        "pair_count": len(blocks),
        "timed_pair_count": len(blocks) * round_count,
        "minimum_sample_seconds_observed": min(durations),
        "interval_95": [math.exp(mean - two_sided), math.exp(mean + two_sided)],
        "block_log_ratios": block_logs,
    }


def validate_search_aa(measurement: dict, settings: dict) -> None:
    """Require the complete A/A interval to fit the declared equivalence margin."""
    interval = measurement.get("interval_95")
    if not isinstance(interval, list) or len(interval) != 2:
        raise ValueError("A/A requires a complete confidence interval")
    values = [measurement.get("geometric_speedup"), *interval]
    if any(
        type(value) not in {int, float} or not math.isfinite(value) or value <= 0
        for value in values
    ):
        raise ValueError("Invalid A/A measurement")
    if not interval[0] <= values[0] <= interval[1]:
        raise ValueError("A/A interval does not contain its point estimate")
    if any(abs(math.log(value)) > settings["max_aa_log_bias"] for value in values):
        raise RuntimeError("Search A/A interval exceeds the equivalence margin")


def search_measurement_rejection(measurement: dict, settings: dict) -> str | None:
    if measurement["pair_count"] != settings["process_blocks"]:
        return "The measurement is missing independent process blocks"
    if measurement["timed_pair_count"] != (
        settings["process_blocks"] * settings["rounds_per_block"]
    ):
        return "The measurement is missing timed pairs"
    if (
        measurement["minimum_sample_seconds_observed"]
        < settings["minimum_sample_seconds"]
    ):
        return "A search sample is shorter than the frozen duration floor"
    if measurement["log_standard_error"] > settings["max_log_standard_error"]:
        return "Between-process timing uncertainty exceeds campaign policy"
    return None
