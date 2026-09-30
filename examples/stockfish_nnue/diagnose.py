"""Operator-only A/A timing diagnostics; never publishes evolutionary fitness.

Use a prepared campaign on a dedicated native Linux worker. Both roles execute
the same trusted baseline artifact. Keep outputs outside mutation snapshots.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import time
import uuid

from scipy.stats import t
import yaml

from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.containers import DockerEngine
from shinka.secure.contracts import ResourceLimits
from shinka.secure.dependencies import DependencyManifest, DependencyPreparer
from shinka.secure.runtime import ContainerCandidateRunner

from .evaluate import measure


def summarize_blocks(blocks: list[dict], weights: dict[str, float]) -> dict:
    """Estimate uncertainty over fresh process pairs, not individual NNUE calls."""
    if len(blocks) < 2:
        raise ValueError("At least two independent process blocks are required")
    result = {"block_count": len(blocks), "fitness": None, "workloads": {}}
    for name in ["aggregate", *weights]:
        logs = []
        for block in blocks:
            measurement = block["measurement"]
            speed = (
                measurement["geometric_speedup"]
                if name == "aggregate"
                else measurement["workload_speedups"][name]
            )
            if (
                type(speed) not in {int, float}
                or not math.isfinite(speed)
                or speed <= 0
            ):
                raise ValueError("Invalid block speed ratio")
            logs.append(math.log(speed))
        mean = statistics.mean(logs)
        se = statistics.stdev(logs) / math.sqrt(len(logs))
        delta = float(t.ppf(0.975, len(logs) - 1)) * se
        value = {
            "geometric_speedup": math.exp(mean),
            "between_block_log_se": se,
            "interval_95": [math.exp(mean - delta), math.exp(mean + delta)],
            "block_speedup_range": [math.exp(min(logs)), math.exp(max(logs))],
        }
        if name == "aggregate":
            result["aggregate"] = value
        else:
            result["workloads"][name] = value
    return result


class ObservedRunner:
    """Collect kernel counters outside the timed request, for diagnosis only."""

    def __init__(self, runner, role: str, output: Path):
        self.runner = runner
        self.role = role
        self.output = output
        self.pid = int(
            subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Pid}}",
                    runner.container_name,
                ],
                text=True,
            )
        )
        path = Path(f"/proc/{self.pid}/cgroup").read_text().strip()
        if not path.startswith("0::/") or "\n" in path:
            raise ValueError("Diagnostics require unified cgroup v2")
        self.cgroup = Path("/sys/fs/cgroup") / path[4:]
        self.index = 0

    def counters(self) -> dict:
        return {
            name: int(value)
            for name, value in (
                line.split()
                for line in (self.cgroup / "cpu.stat").read_text().splitlines()
            )
        }

    def snapshot(self, phase: str) -> None:
        directory = self.output / f"{self.role}-{phase}"
        directory.mkdir()
        # Docker's init process and the Python adapter can both sit above C++.
        # Capture the complete tree, including the process that owns the network.
        pids = [str(self.pid)]
        for pid in pids:
            pids.extend(Path(f"/proc/{pid}/task/{pid}/children").read_text().split())
        for pid in pids:
            for name in ["maps", "smaps_rollup", "status", "stat", "schedstat"]:
                (directory / f"{pid}-{name}.txt").write_text(
                    Path(f"/proc/{pid}/{name}").read_text()
                )
        (directory / "cpu-stat.json").write_text(json.dumps(self.counters(), indent=2))

    def request(self, payload):
        observed = payload.get("op") == "run" and payload.get("workload") in {
            "incremental",
            "refresh",
            "hot",
        }
        before = self.counters() if observed else None
        response = self.runner.request(payload)
        if observed:
            after = self.counters()
            record = {
                "role": self.role,
                "request_index": self.index,
                "request": payload,
                "wall_seconds": response.elapsed_seconds,
                "calls": response.output["calls"],
                "checksum": response.output["checksum"],
                "cpu_stat_delta": {
                    name: value - before[name] for name, value in after.items()
                },
            }
            with (self.output / "kernel-counters.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            self.index += 1
        return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=8)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--cpu", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument(
        "--runtime-artifact",
        type=Path,
        help="Explicit trusted diagnostic artifact for both roles",
    )
    allocation_group = parser.add_mutually_exclusive_group()
    allocation_group.add_argument(
        "--allocation",
        choices=("heap", "large", "engine"),
        help="Allocation argument for the operator diagnostic harness only",
    )
    allocation_group.add_argument(
        "--compare-allocations",
        action="store_true",
        help="Interleave heap/large A/A blocks using one diagnostic executable",
    )
    parser.add_argument(
        "--comparison-modes",
        nargs=2,
        choices=("heap", "large", "engine"),
        default=("heap", "large"),
        help="Two modes to interleave when --compare-allocations is set",
    )
    parser.add_argument(
        "--persistent",
        action="store_true",
        help="One process pair, candidate starts first",
    )
    args = parser.parse_args()
    if args.blocks < 2 or args.blocks % 2 or args.pairs < 6 or args.pairs % 2:
        parser.error("Require even blocks >= 2 and even pairs >= 6")
    if args.compare_allocations and args.persistent:
        parser.error("Allocation comparison requires independent process blocks")
    if len(set(args.comparison_modes)) != 2:
        parser.error("Comparison modes must be different")
    if platform.system() != "Linux":
        parser.error("Use a dedicated native Linux worker")
    campaign = args.campaign.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    # Keep the controller off the candidate's core. Record the actual affinity.
    controller_cpus = sorted(os.sched_getaffinity(0) - {args.cpu})
    if not controller_cpus:
        raise ValueError("A separate controller CPU is required")
    os.sched_setaffinity(0, controller_cpus)
    store = ContentAddressedStore(out / "artifacts")
    config = yaml.safe_load((campaign / "shinka.yaml").read_text())["job"]
    manifest = json.loads((campaign / "evaluator/task-manifest.json").read_text())
    settings = dict(manifest["benchmark"])
    settings.update(
        pairs=args.pairs,
        passes_by_workload={"incremental": 4096, "refresh": 2048, "hot": 1024},
    )
    corpus = json.loads((campaign / "evaluator/corpus.json").read_text())
    traces = corpus["traces"]
    checks = traces + json.loads((campaign / "evaluator/edge-cases.json").read_text())
    meta = json.loads((campaign / "evaluator/baseline.json").read_text())
    if (args.allocation or args.compare_allocations) and args.runtime_artifact is None:
        parser.error("Allocation experiment requires an explicit diagnostic artifact")
    baseline = store.put_file(
        args.runtime_artifact or campaign / "evaluator/baseline.tar",
        kind="runtime",
        expected_digest=None if args.runtime_artifact else meta["digest"],
    )
    command = tuple(config["candidate_command"])
    dependencies = DependencyPreparer(store).prepare(
        DependencyManifest.from_dict(
            json.loads((campaign / "dependencies.json").read_text())
        )
    )
    engine = DockerEngine(allow_rootful_dedicated_vm=True)
    engine.preflight(images={config["runtime_image"]})
    order = ["AB", "BA"] * (args.blocks // 2)
    random.Random(args.seed).shuffle(order)
    if args.persistent:
        order = ["BA"]
    cases = [{"startup": startup, "allocation": args.allocation} for startup in order]
    if args.compare_allocations:
        allocation_orders = [False, True] * (args.blocks // 2)
        random.Random(args.seed + 1).shuffle(allocation_orders)
        cases = []
        for startup, reverse in zip(order, allocation_orders):
            allocations = list(args.comparison_modes)
            if reverse:
                allocations.reverse()
            cases.extend(
                {"startup": startup, "allocation": mode} for mode in allocations
            )
    plan = {
        "kind": "unscored_same_artifact_diagnostic",
        "fitness": None,
        "baseline_digest": baseline.digest,
        "original_baseline_digest": meta["digest"],
        "allocation": args.allocation,
        "image": config["runtime_image"],
        "dependency_digest": dependencies.runtime_artifact.digest,
        "diagnostic_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "controller_cpus": controller_cpus,
        "timing_cpu": args.cpu,
        "settings": settings,
        "startup_orders": order,
        "cases": cases,
        "seed": args.seed,
        "platform": platform.platform(),
    }
    (out / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    blocks = []
    for index, case in enumerate(cases):
        startup = case["startup"]
        allocation = case["allocation"]
        block_command = command + ((allocation,) if allocation else ())
        directory = out / f"block-{index:02d}"
        directory.mkdir()
        attempt = str(uuid.uuid4())
        started = time.monotonic()
        runners = {}
        with ExitStack() as stack:
            for label in startup:
                role = "baseline" if label == "A" else "candidate"
                runner = ContainerCandidateRunner(
                    engine=engine,
                    artifacts=store,
                    runtime_artifact=baseline,
                    dependency_artifact=dependencies.runtime_artifact,
                    image=config["runtime_image"],
                    command=block_command,
                    limits=ResourceLimits(
                        cpus=2,
                        cpu_set=str(args.cpu),
                        memory_bytes=4 * 1024**3,
                        pids=128,
                    ),
                    job_id=attempt,
                    attempt_id=attempt,
                    container_suffix=role,
                    startup_timeout_seconds=180,
                    request_timeout_seconds=180,
                    sandbox_user="65532:65532",
                )
                stack.enter_context(runner)
                runners[role] = ObservedRunner(runner, role, directory)
                runners[role].snapshot("before")
            measurement = measure(
                runners["baseline"],
                runners["candidate"],
                traces,
                settings,
                lambda stage: print(
                    json.dumps({"block": index, "stage": stage}), flush=True
                ),
                correctness_traces=checks,
            )
            for runner in runners.values():
                runner.snapshot("after")
                if runner.runner.stderr:
                    raise RuntimeError(
                        "Trusted baseline produced unexpected diagnostics"
                    )
        block = {
            "block": index,
            "startup_order": startup,
            "allocation": allocation,
            "attempt_id": attempt,
            "seconds": time.monotonic() - started,
            "measurement": measurement,
        }
        blocks.append(block)
        (directory / "measurement.json").write_text(json.dumps(block, indent=2) + "\n")
        (out / "blocks.json").write_text(json.dumps(blocks, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "block": index,
                    "finished": True,
                    "speedups": measurement["workload_speedups"],
                }
            ),
            flush=True,
        )
    if len(blocks) > 1:
        if args.compare_allocations:
            summary = {
                "fitness": None,
                "allocations": {
                    mode: summarize_blocks(
                        [block for block in blocks if block["allocation"] == mode],
                        settings["workload_weights"],
                    )
                    for mode in args.comparison_modes
                },
            }
        else:
            summary = summarize_blocks(blocks, settings["workload_weights"])
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "complete").write_text(
        "Diagnostic only; no evolutionary fitness admitted.\n"
    )


if __name__ == "__main__":
    main()
