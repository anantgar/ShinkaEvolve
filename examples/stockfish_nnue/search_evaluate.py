"""Private v3 evaluator: exact NNUE checks, then externally timed fixed searches."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import platform
import random
import re

from shinka.secure.errors import FailureClass, SecureExecutionError
from shinka.secure.runtime import ContainerCandidateRunner

try:
    from .corpus import validate_traces
    from .evaluate import InvalidCandidate, check_exact, reference_runner
    from .policy import load_manifest
    from .search_checkpoint import SearchCheckpoint
    from .search_scoring import score_search_blocks, search_measurement_rejection
except ImportError:
    from corpus import validate_traces
    from evaluate import InvalidCandidate, check_exact, reference_runner
    from policy import load_manifest
    from search_checkpoint import SearchCheckpoint
    from search_scoring import score_search_blocks, search_measurement_rejection

MOVE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")
FIELDS = {
    "depth",
    "seldepth",
    "nodes",
    "score_kind",
    "score",
    "bound",
    "pv",
    "bestmove",
    "ponder",
}


def validate_search_output(output: dict, calls: int) -> None:
    if not isinstance(output, dict) or set(output) != {
        "calls",
        "nodes",
        "checksum",
        "records",
    }:
        raise InvalidCandidate("Invalid full-engine response fields")
    if type(output["calls"]) is not int or output["calls"] != calls:
        raise InvalidCandidate("Fixed-search call count differs from the declared work")
    records = output["records"]
    if not isinstance(records, list) or len(records) != calls:
        raise InvalidCandidate("Missing fixed-search results")
    for record in records:
        if not isinstance(record, dict) or set(record) != FIELDS:
            raise InvalidCandidate("Invalid fixed-search record")
        if any(
            type(record[key]) is not int
            for key in ("depth", "seldepth", "nodes", "score")
        ):
            raise InvalidCandidate("Non-integer search result")
        if (
            not 1 <= record["depth"] <= 24
            or not 1 <= record["seldepth"] <= 256
            or not 0 < record["nodes"] < 2**63
            or not -(2**31) < record["score"] < 2**31
            or record["score_kind"] not in {"cp", "mate"}
            or record["bound"] not in {"", "lowerbound", "upperbound"}
        ):
            raise InvalidCandidate("Search result outside the contract")
        pv = record["pv"]
        if (
            not isinstance(pv, list)
            or not 1 <= len(pv) <= 256
            or any(not isinstance(move, str) or not MOVE.fullmatch(move) for move in pv)
            or record["bestmove"] != pv[0]
            or not isinstance(record["ponder"], str)
            or (record["ponder"] and not MOVE.fullmatch(record["ponder"]))
        ):
            raise InvalidCandidate("Invalid search move sequence")
    if type(output["nodes"]) is not int or output["nodes"] != sum(
        record["nodes"] for record in records
    ):
        raise InvalidCandidate("Search node total does not match its records")
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    if output["checksum"] != hashlib.sha256(canonical).hexdigest():
        raise InvalidCandidate("Search checksum does not match its records")


def require_search_match(reference: dict, candidate: dict, calls: int) -> None:
    validate_search_output(candidate, calls)
    if candidate != reference:
        raise InvalidCandidate(
            "Search outputs or node fingerprints differ from the reference"
        )


def clone_runner(template, artifact, suffix: str):
    return ContainerCandidateRunner(
        engine=template.engine,
        artifacts=template.artifacts,
        runtime_artifact=artifact,
        dependency_artifact=template.dependency_artifact,
        image=template.image,
        command=template.command,
        limits=template.limits,
        job_id=template.job_id,
        attempt_id=template.attempt_id,
        container_suffix=suffix,
        startup_timeout_seconds=template.startup_timeout_seconds,
        request_timeout_seconds=template.request_timeout_seconds,
        sandbox_user=template.sandbox_user,
    )


def measure_paired_search(
    factory,
    cases: list[dict],
    settings: dict,
    heartbeat,
    *,
    checkpoint=lambda _: None,
    maximum_memory_ratio: float = 1.05,
) -> dict:
    """Each factory invocation creates a new container and executable copy."""
    rng = random.Random(settings["seed"])
    startup_orders = ["AB", "BA"] * (settings["process_blocks"] // 2)
    rng.shuffle(startup_orders)
    plan = []
    for startup in startup_orders:
        orders = ["AB", "BA"] * (settings["rounds_per_block"] // 2)
        rng.shuffle(orders)
        plan.append(
            {
                "startup_order": startup,
                "rounds": [
                    {"order": order, "offset": rng.randrange(len(cases))}
                    for order in orders
                ],
            }
        )
    blocks = []
    calls = len(cases) * settings["passes"]
    reference_outputs = {}
    checkpoint({"plan": plan, "blocks": blocks})

    def verify(samples, offset):
        expected = samples["baseline"].output
        try:
            validate_search_output(expected, calls)
        except InvalidCandidate as exc:
            raise ValueError(
                "The frozen reference returned an invalid search result"
            ) from exc
        if expected != reference_outputs.setdefault(offset, expected):
            raise ValueError("The frozen reference search is not deterministic")
        require_search_match(expected, samples["candidate"].output, calls)

    for index, entry in enumerate(plan):
        block = {"block": index, "startup_order": entry["startup_order"], "rounds": []}
        blocks.append(block)
        with ExitStack() as stack:
            runners = {}
            for label in entry["startup_order"]:
                role = "baseline" if label == "A" else "candidate"
                runner = stack.enter_context(factory(role, index))
                runners[role] = runner
                response = runner.request(
                    {
                        "op": "search_load",
                        "cases": cases,
                        "depth": settings["depth"],
                        "hash_mb": settings["hash_mb"],
                    }
                )
                if response.output != {"loaded": len(cases)}:
                    raise InvalidCandidate("Search corpus load did not complete")
            for _ in range(settings["warmups"]):
                samples = {
                    role: runners[role].request(
                        {"op": "search", "passes": settings["passes"], "offset": 0}
                    )
                    for role in ("baseline", "candidate")
                }
                verify(samples, 0)
                heartbeat("search_warmup")
            for spec in entry["rounds"]:
                roles = (
                    ("baseline", "candidate")
                    if spec["order"] == "AB"
                    else ("candidate", "baseline")
                )
                samples = {
                    role: runners[role].request(
                        {
                            "op": "search",
                            "passes": settings["passes"],
                            "offset": spec["offset"],
                        }
                    )
                    for role in roles
                }
                sample = {**spec}
                for role, response in samples.items():
                    sample[f"{role}_seconds"] = response.elapsed_seconds
                    sample[f"{role}_output"] = response.output
                block["rounds"].append(sample)
                # Preserve evidence before checking correctness or statistical gates.
                checkpoint({"plan": plan, "blocks": blocks})
                verify(samples, spec["offset"])
                for role, response in samples.items():
                    sample[f"{role}_calls"] = response.output["calls"]
                    sample[f"{role}_nodes"] = response.output["nodes"]
                heartbeat(f"search_block_{index + 1}")
            block["memory_peak_bytes"] = {
                role: runner.peak_memory_bytes() for role, runner in runners.items()
            }
            memory = block["memory_peak_bytes"]
            if memory["candidate"] > memory["baseline"] * maximum_memory_ratio:
                raise InvalidCandidate("Full-engine peak memory growth exceeded policy")
            if runners["baseline"].stderr:
                raise ValueError("Frozen reference emitted unexpected diagnostics")
            if runners["candidate"].stderr:
                raise InvalidCandidate("Candidate emitted unexpected diagnostics")
        checkpoint({"plan": plan, "blocks": blocks})
    return {**score_search_blocks(blocks), "blocks": blocks, "plan": plan}


def measure_search(
    factory,
    cases,
    settings,
    heartbeat,
    *,
    checkpoint=lambda _: None,
    maximum_memory_ratio=1.05,
):
    if settings.get("process_isolation", "paired") == "paired":
        return measure_paired_search(
            factory,
            cases,
            settings,
            heartbeat,
            checkpoint=checkpoint,
            maximum_memory_ratio=maximum_memory_ratio,
        )
    # One engine is resident at a time. A block comprises four fresh processes,
    # ABBA or BAAB, so both roles occupy matching early/late temporal positions.
    if (
        settings["process_isolation"] != "sequential_abba_v1"
        or settings["rounds_per_block"] != 2
    ):
        raise ValueError("Unknown fixed-search process design")
    rng = random.Random(settings["seed"])
    orders = ["AB", "BA"] * (settings["process_blocks"] // 2)
    rng.shuffle(orders)
    plan = [
        {
            "startup_order": order,
            "sequence": order + order[::-1],
            "offset": rng.randrange(len(cases)),
        }
        for order in orders
    ]
    blocks, reference_outputs = [], {}
    calls = len(cases) * settings["passes"]
    checkpoint({"plan": plan, "blocks": blocks})
    for index, entry in enumerate(plan):
        block = {
            "block": index,
            **entry,
            "rounds": [],
            "observations": [],
            "memory_peaks": [],
        }
        blocks.append(block)
        for position, label in enumerate(entry["sequence"]):
            role = "baseline" if label == "A" else "candidate"
            if position % 2 == 0:
                block["rounds"].append(
                    {
                        "order": entry["sequence"][position : position + 2],
                        "offset": entry["offset"],
                    }
                )
            sample = block["rounds"][-1]
            with factory(role, 4 * index + position) as runner:
                loaded = runner.request(
                    {
                        "op": "search_load",
                        "cases": cases,
                        "depth": settings["depth"],
                        "hash_mb": settings["hash_mb"],
                    }
                )
                if loaded.output != {"loaded": len(cases)}:
                    raise InvalidCandidate("Search corpus load did not complete")
                for iteration in range(settings["warmups"] + 1):
                    response = runner.request(
                        {
                            "op": "search",
                            "passes": settings["passes"],
                            "offset": entry["offset"],
                        }
                    )
                    observation = {
                        "role": role,
                        "process": 4 * index + position,
                        "warmup": iteration < settings["warmups"],
                        "output": response.output,
                    }
                    block["observations"].append(observation)
                    if not observation["warmup"]:
                        sample.update(
                            {
                                f"{role}_seconds": response.elapsed_seconds,
                                f"{role}_output": response.output,
                            }
                        )
                    checkpoint({"plan": plan, "blocks": blocks})
                    try:
                        validate_search_output(response.output, calls)
                    except InvalidCandidate as exc:
                        if role == "baseline":
                            raise ValueError(
                                "Frozen reference returned an invalid search result"
                            ) from exc
                        raise
                    if role == "baseline":
                        expected = reference_outputs.setdefault(
                            entry["offset"], response.output
                        )
                        if response.output != expected:
                            raise ValueError(
                                "The frozen reference search is not deterministic"
                            )
                    if entry["offset"] in reference_outputs:
                        require_search_match(
                            reference_outputs[entry["offset"]], response.output, calls
                        )
                    if not observation["warmup"]:
                        sample.update(
                            {
                                f"{role}_calls": response.output["calls"],
                                f"{role}_nodes": response.output["nodes"],
                            }
                        )
                    heartbeat(f"search_block_{index + 1}_process_{position + 1}")
                block["memory_peaks"].append(
                    {"role": role, "bytes": runner.peak_memory_bytes()}
                )
                if runner.stderr:
                    if role == "baseline":
                        raise ValueError(
                            "Frozen reference emitted unexpected diagnostics"
                        )
                    raise InvalidCandidate("Candidate emitted unexpected diagnostics")
            # The context closes this engine before the next allocation starts.
        for observation in block["observations"]:
            require_search_match(
                reference_outputs[entry["offset"]], observation["output"], calls
            )
        memory = {
            role: max(
                value["bytes"]
                for value in block["memory_peaks"]
                if value["role"] == role
            )
            for role in ("baseline", "candidate")
        }
        if memory["candidate"] > memory["baseline"] * maximum_memory_ratio:
            raise InvalidCandidate("Full-engine peak memory growth exceeded policy")
        checkpoint({"plan": plan, "blocks": blocks})
    return {**score_search_blocks(blocks), "blocks": blocks, "plan": plan}


def evaluate(job, candidate_runner, private_inputs, result_writer) -> None:
    manifest = load_manifest(private_inputs.path("manifest"))
    if manifest["schema"] != "stockfish-inference-v3":
        raise ValueError("Fixed-search evaluation requires a v3 campaign")
    machine = manifest["targets"][manifest["campaign"]["target"]]["machine"]
    if platform.system() != "Linux" or platform.machine() != machine:
        raise ValueError("Fixed-search evaluation requires the native Linux target")
    if not manifest["campaign"].get("search_work_frozen"):
        raise ValueError(
            "Calibrate and freeze search work before controls or evolution"
        )
    settings = manifest["search_benchmark"]
    corpus = json.loads(private_inputs.path("corpus").read_text())
    traces = corpus["traces"]
    edges = json.loads(private_inputs.path("edge_cases").read_text())
    cases = json.loads(private_inputs.path("search_cases").read_text())
    for values in (traces, edges, cases):
        validate_traces(values)
    diagnostics = {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "stockfish_commit": manifest["stockfish_commit"],
        "network_sha256": manifest["network_sha256"],
        "corpus_provenance": corpus["provenance"],
        "settings": settings,
        "fitness_method": "independent-process-fixed-search-"
        + settings.get("process_isolation", "paired"),
    }

    checkpoint = SearchCheckpoint(diagnostics, job.checkpoint)

    try:
        with result_writer.phase("exact_verification"):
            with reference_runner(candidate_runner, private_inputs) as reference:
                reference_artifact = reference.runtime_artifact
                expected = reference.request({"op": "info"}).output
                actual = candidate_runner.request({"op": "info"}).output
                for name in (
                    "network_bytes",
                    "stack_bytes",
                    "cache_bytes",
                    "binary_bytes",
                    "search_binary_bytes",
                ):
                    limit = manifest["benchmark"][
                        "max_binary_ratio"
                        if "binary" in name
                        else "max_structure_ratio"
                    ]
                    value = actual.get(name)
                    if (
                        type(value) is not int
                        or value <= 0
                        or value > expected[name] * limit
                    ):
                        raise InvalidCandidate(
                            "NNUE structure or binary growth exceeded policy"
                        )
                diagnostics["exact_values_checked"] = check_exact(
                    reference,
                    candidate_runner,
                    traces + edges,
                    manifest["benchmark"],
                    job.heartbeat,
                    random.Random(manifest["benchmark"]["seed"]),
                    rounds=4,
                )
                diagnostics["replay_memory_peak_bytes"] = {
                    "baseline": reference.peak_memory_bytes(),
                    "candidate": candidate_runner.peak_memory_bytes(),
                }
                memory = diagnostics["replay_memory_peak_bytes"]
                if (
                    memory["candidate"]
                    > memory["baseline"] * manifest["benchmark"]["max_memory_ratio"]
                ):
                    raise InvalidCandidate(
                        "Exact-check peak memory growth exceeded policy"
                    )
        # No correctness process remains resident during the engine measurements.
        candidate_runner.stop()

        def factory(role, index):
            artifact = (
                reference_artifact
                if role == "baseline"
                else candidate_runner.runtime_artifact
            )
            return clone_runner(candidate_runner, artifact, f"{role}-b{index:03d}")

        with result_writer.phase("fixed_search_timing"):
            measurement = measure_search(
                factory,
                cases,
                settings,
                job.heartbeat,
                checkpoint=checkpoint,
                maximum_memory_ratio=manifest["benchmark"]["max_memory_ratio"],
            )
        diagnostics["measurement"] = measurement
        diagnostics.pop("progress", None)
        rejection = search_measurement_rejection(measurement, settings)
        if rejection:
            raise SecureExecutionError(FailureClass.EVALUATOR_FAILED, rejection)
        result_writer.add_private_artifact(
            json.dumps(diagnostics, allow_nan=False).encode(),
            kind="stockfish_measurements",
        )
        result_writer.succeed(
            correct=True,
            combined_score=measurement["combined_score"],
            public_metrics={
                name: measurement[name]
                for name in ("geometric_speedup", "log_standard_error", "pair_count")
            },
            private_metrics={"timed_pair_count": measurement["timed_pair_count"]},
        )
    except InvalidCandidate as exc:
        diagnostics["rejection"] = str(exc)
        checkpoint.flush()
        result_writer.add_private_artifact(
            json.dumps(diagnostics, allow_nan=False).encode(),
            kind="stockfish_measurements",
        )
        result_writer.succeed(
            correct=False,
            combined_score=0.0,
            operator_diagnostics={"rejection": str(exc)},
        )
    except Exception as exc:
        diagnostics["failure"] = str(exc)
        checkpoint.flush()
        raise SecureExecutionError(
            exc.failure_class
            if isinstance(exc, SecureExecutionError)
            else FailureClass.EVALUATOR_FAILED,
            "Fixed-search measurement failed",
            private_diagnostic=json.dumps(diagnostics, allow_nan=False),
        ) from exc
