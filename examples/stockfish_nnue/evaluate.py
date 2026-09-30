"""Private Stockfish evaluator, loaded only by the trusted secure worker."""

from __future__ import annotations

import json
import platform
import random

from shinka.secure.runtime import ContainerCandidateRunner
from shinka.secure.errors import FailureClass, SecureExecutionError

try:
    from .corpus import validate_traces
    from .policy import load_manifest
    from .scoring import score_pairs
except ImportError:  # The secure worker loads the frozen standalone evaluator artifact.
    from corpus import validate_traces
    from policy import load_manifest
    from scoring import score_pairs


class InvalidCandidate(ValueError):
    """A completed candidate violated correctness or a resource constraint."""


def require_match(reference: dict, candidate: dict, *, checking: bool = False) -> None:
    if type(candidate.get("calls")) is not int or candidate["calls"] <= 0:
        raise InvalidCandidate("Invalid call count")
    for name in ("calls", "checksum"):
        if candidate.get(name) != reference.get(name):
            raise InvalidCandidate("Raw NNUE output stream differs from the reference")
    if checking:
        values = candidate.get("values")
        if (
            not isinstance(values, list)
            or len(values) != 4 * candidate["calls"]
            or any(type(v) is not int for v in values)
        ):
            raise InvalidCandidate("Invalid raw evaluation values")
        if values != reference.get("values"):
            raise InvalidCandidate("Exact raw/final/incremental/fresh checks failed")


def run_request(runner, workload: str, passes: int, offset: int):
    return runner.request(
        {"op": "run", "workload": workload, "passes": passes, "offset": offset}
    )


def check_exact(reference, candidate, traces, settings, heartbeat, rng, *, rounds=3):
    checked_values = 0
    for order in range(rounds):
        ordered = list(traces)
        rng.shuffle(ordered)
        salt = rng.randrange(1 << 30)
        # Exact streams can be much larger than inputs. Bound every response,
        # even with 192-ply traces and the maximum 32 sibling branches.
        for begin in range(0, len(ordered), 16):
            shard = ordered[begin : begin + 16]
            payload = {
                "op": "load",
                "traces": shard,
                "branches": settings["branches"],
                "salt": salt,
            }
            for runner in (reference, candidate):
                if runner.request(payload).output != {"loaded": len(shard)}:
                    raise InvalidCandidate("Corpus load did not complete")
            for mode in ("check", "check_lazy"):
                expected = run_request(reference, mode, 1, order).output
                actual = run_request(candidate, mode, 1, order).output
                require_match(expected, actual, checking=True)
                checked_values += len(expected["values"])
            heartbeat("exact_check")
    return checked_values


def measure(
    reference,
    candidate,
    traces: list[dict],
    settings: dict,
    heartbeat,
    *,
    correctness_traces: list[dict] | None = None,
) -> dict:
    rng = random.Random(settings["seed"])
    checks = correctness_traces if correctness_traces is not None else traces
    checked_values = check_exact(reference, candidate, checks, settings, heartbeat, rng)
    ordered = list(traces)
    rng.shuffle(ordered)
    payload = {
        "op": "load",
        "traces": ordered,
        "branches": settings["branches"],
        "salt": rng.randrange(1 << 30),
    }
    for runner in (reference, candidate):
        if runner.request(payload).output != {"loaded": len(ordered)}:
            raise InvalidCandidate("Timing corpus load did not complete")

    passes = {
        name: settings.get("passes_by_workload", {}).get(name, settings["passes"])
        for name in settings["workload_weights"]
    }
    # Only the reference chooses the amount of work; candidate cannot shorten its run.
    # Freeze these calibrated counts in a production manifest with the calibration CLI.
    for name in passes:
        probe = run_request(reference, name, passes[name], 0)
        target_seconds = settings["minimum_sample_seconds"] * (
            1 if "passes_by_workload" in settings else 2
        )
        while probe.elapsed_seconds < target_seconds:
            if passes[name] >= settings["maximum_passes"]:
                raise ValueError("Benchmark cannot reach the minimum sample duration")
            if "passes_by_workload" in settings:
                raise ValueError("Frozen campaign samples are too short; recalibrate")
            passes[name] = min(settings["maximum_passes"], passes[name] * 2)
            probe = run_request(reference, name, passes[name], 0)
        for _ in range(settings["warmups"]):
            expected = run_request(reference, name, passes[name], 0)
            actual = run_request(candidate, name, passes[name], 0)
            require_match(expected.output, actual.output)
        heartbeat("warmup")

    pairs = []
    # Balanced AB/BA, shuffled once; no outlier deletion and no early stopping on winners.
    orders = [index % 2 for index in range(settings["pairs"])]
    rng.shuffle(orders)
    for reverse in orders:
        pair = {}
        workloads = list(passes)
        rng.shuffle(workloads)
        for name in workloads:
            offset = rng.randrange(len(traces))
            runners = [("baseline", reference), ("candidate", candidate)]
            if reverse:
                runners.reverse()
            samples = {
                role: run_request(runner, name, passes[name], offset)
                for role, runner in runners
            }
            require_match(samples["baseline"].output, samples["candidate"].output)
            pair[name] = {
                "order": "BA" if reverse else "AB",
                "passes": passes[name],
                "offset": offset,
            }
            for role, response in samples.items():
                pair[name][f"{role}_seconds"] = response.elapsed_seconds
                pair[name][f"{role}_calls"] = response.output["calls"]
        pairs.append(pair)
        heartbeat("paired_timing")
    score = score_pairs(pairs, settings["workload_weights"])
    # Catch persistent state corruption that appears only after long timed runs.
    checked_values += check_exact(
        reference, candidate, checks, settings, heartbeat, rng, rounds=1
    )
    return {
        **score,
        "samples": pairs,
        "passes_by_workload": passes,
        "exact_values_checked": checked_values,
    }


def reference_runner(candidate, private_inputs):
    baseline_path = private_inputs.path("baseline")
    baseline_meta = json.loads(private_inputs.path("baseline_metadata").read_text())
    ref = candidate.artifacts.put_file(
        baseline_path, kind="runtime", expected_digest=baseline_meta["digest"]
    )
    if ref.size != baseline_meta["size"]:
        raise ValueError("Baseline artifact size mismatch")
    return ContainerCandidateRunner(
        engine=candidate.engine,
        artifacts=candidate.artifacts,
        runtime_artifact=ref,
        dependency_artifact=candidate.dependency_artifact,
        image=candidate.image,
        command=candidate.command,
        limits=candidate.limits,
        job_id=candidate.job_id,
        # Both containers must belong to the same durable job attempt so abort
        # and recovery cleanup finds the reference as well as the candidate.
        attempt_id=candidate.attempt_id,
        container_suffix="reference",
        startup_timeout_seconds=candidate.startup_timeout_seconds,
        request_timeout_seconds=candidate.request_timeout_seconds,
        sandbox_user=candidate.sandbox_user,
    )


def evaluate(job, candidate_runner, private_inputs, result_writer) -> None:
    manifest = load_manifest(private_inputs.path("manifest"))
    if not manifest["campaign"]["smoke_only"]:
        machine = manifest["targets"][manifest["campaign"]["target"]]["machine"]
        if platform.system() != "Linux" or platform.machine() != machine:
            raise ValueError(
                "Production evaluation requires the campaign's native Linux architecture"
            )
    settings = manifest["benchmark"]
    corpus = json.loads(private_inputs.path("corpus").read_text())
    traces = corpus["traces"]
    validate_traces(traces)
    edges = json.loads(private_inputs.path("edge_cases").read_text())
    validate_traces(edges)
    # Edge cases always gate correctness, but do not distort the PGN timing mix.
    checks = traces + [{**trace, "id": "edge:" + trace["id"]} for trace in edges]
    # A worker-wide lock is also held by the AWS daemon. Local runs must use one job.
    diagnostics = {
        "cpu": platform.processor(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "corpus_provenance": corpus["provenance"],
        "stockfish_commit": manifest["stockfish_commit"],
        "network_sha256": manifest["network_sha256"],
    }
    try:
        with reference_runner(candidate_runner, private_inputs) as reference:
            baseline_info = reference.request({"op": "info"}).output
            actual_info = candidate_runner.request({"op": "info"}).output
            for name in ("network_bytes", "stack_bytes", "cache_bytes", "binary_bytes"):
                bound = settings[
                    "max_binary_ratio"
                    if name == "binary_bytes"
                    else "max_structure_ratio"
                ]
                actual = actual_info.get(name)
                if (
                    type(actual) is not int
                    or actual <= 0
                    or actual > baseline_info[name] * bound
                ):
                    raise InvalidCandidate(
                        "Binary or fixed NNUE storage limit exceeded"
                    )
            with result_writer.phase("verification_and_timing"):
                measurement = measure(
                    reference,
                    candidate_runner,
                    traces,
                    settings,
                    job.heartbeat,
                    correctness_traces=checks,
                )
            memory = {
                "baseline": reference.peak_memory_bytes(),
                "candidate": candidate_runner.peak_memory_bytes(),
            }
            diagnostics.update(
                {
                    "measurement": measurement,
                    "memory_peak_bytes": memory,
                    "baseline_info": baseline_info,
                    "candidate_info": actual_info,
                }
            )
            if memory["candidate"] > memory["baseline"] * settings["max_memory_ratio"]:
                raise InvalidCandidate("Peak container memory growth exceeded policy")
            quality_rejection = measurement_rejection(measurement, settings)
            if quality_rejection:
                # Infrastructure noise is not candidate incorrectness and must not earn a score.
                diagnostics["measurement_rejection"] = quality_rejection
                raise SecureExecutionError(
                    FailureClass.EVALUATOR_FAILED,
                    quality_rejection,
                    private_diagnostic=json.dumps(diagnostics, allow_nan=False),
                )
            result_writer.add_private_artifact(
                json.dumps(diagnostics, allow_nan=False).encode(),
                kind="stockfish_measurements",
            )
            result_writer.succeed(
                correct=True,
                combined_score=measurement["combined_score"],
                public_metrics={
                    "geometric_speedup": measurement["geometric_speedup"],
                    "log_standard_error": measurement["log_standard_error"],
                    "pair_count": measurement["pair_count"],
                },
                private_metrics={
                    "workload_speedups": measurement["workload_speedups"],
                    "memory_peak_bytes": memory,
                },
            )
    except InvalidCandidate as exc:
        result_writer.succeed(
            correct=False,
            combined_score=0.0,
            public_metrics={},
            operator_diagnostics={"rejection": str(exc)},
        )


def measurement_rejection(measurement: dict, settings: dict) -> str | None:
    if (
        measurement["minimum_sample_seconds_observed"]
        < settings["minimum_sample_seconds"]
    ):
        return "A timed sample was below the frozen duration floor; recalibrate"
    if measurement["log_standard_error"] > settings["max_log_standard_error"]:
        return "Paired timing noise exceeded campaign policy"
    if any(
        value > settings["max_workload_log_standard_error"]
        for value in measurement["workload_log_standard_errors"].values()
    ):
        return "A workload's timing noise exceeded campaign policy"
    return None
