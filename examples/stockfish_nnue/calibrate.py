"""A/A, known-slower, and deliberately incorrect controls for a prepared campaign."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil

from shinka.secure.contracts import ArtifactRef

from .policy import load_manifest, sha256

from .run import scheduler


def control_copy(seed: Path, destination: Path, mode: str) -> None:
    shutil.copytree(seed, destination, ignore=shutil.ignore_patterns(".git", ".shinka"))
    header = destination / "src/nnue/nnue_feature_transformer.h"
    code = header.read_text()
    marker = "        return psqt;"
    if code.count(marker) != 1:
        raise ValueError("Pinned reference changed; calibration mutation needs review")
    if mode == "wrong":
        code = code.replace(marker, "        return psqt + 10000;")
    elif mode == "slow":
        code = code.replace(
            marker,
            "        volatile unsigned calibration = 0;\n"
            "        for (unsigned i = 0; i < 4000; ++i) calibration = calibration + i;\n"
            + marker,
        )
    elif mode != "aa":
        raise ValueError("Unknown calibration control")
    header.write_text(code)


def validate_aa_bias(measurement: dict, settings: dict) -> None:
    """A stable weighted average cannot hide biased component workloads."""
    workloads = measurement.get("workload_speedups")
    if not isinstance(workloads, dict) or set(workloads) != set(
        settings["workload_weights"]
    ):
        raise ValueError("A/A requires a complete set of workload speedups")
    for name, speed in {
        "aggregate": measurement.get("geometric_speedup"),
        **workloads,
    }.items():
        if type(speed) not in {int, float} or not math.isfinite(speed) or speed <= 0:
            raise ValueError(f"Invalid A/A speedup for {name}")
        if abs(math.log(speed)) > settings["max_aa_log_bias"]:
            raise RuntimeError(
                f"A/A {name} exceeds the campaign's bias limit; investigate the worker"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("local", "aws"), default="local")
    parser.add_argument(
        "--controls",
        nargs="+",
        choices=("aa", "slow", "wrong"),
        default=("aa", "slow", "wrong"),
    )
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="Freeze A/A-calibrated pass counts after all three controls pass",
    )
    args = parser.parse_args()
    settings = load_manifest(args.campaign / "evaluator/task-manifest.json")[
        "benchmark"
    ]
    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    aa_measurement = None
    for control in args.controls:
        candidate = args.output / control
        control_copy(args.campaign / "seed_repo", candidate, control)
        evaluator = scheduler(
            args.campaign.resolve(),
            (args.output / "state" / control).resolve(),
            backend=args.backend,
        )
        result, seconds = evaluator.run(
            str(candidate), str(args.output / f"{control}-result")
        )
        results[control] = {"result": result, "seconds": seconds}
        (args.output / "calibration.json").write_text(
            json.dumps(results, indent=2) + "\n"
        )
        job_id = result.get("secure_identities", {}).get("evaluation_job_id")
        if control == "aa" and job_id and result.get("correct", {}).get("correct"):
            if args.backend == "aws":
                record = evaluator._result(job_id)
                refs = [
                    ArtifactRef(**value) for value in record["manifest"]["artifacts"]
                ]
                for ref in refs:
                    evaluator.transport.download(evaluator.coordinator.artifacts, ref)
            else:
                refs = evaluator.coordinator.get_result(job_id).artifacts
            ref = next(ref for ref in refs if ref.kind == "stockfish_measurements")
            aa_measurement = json.loads(
                evaluator.coordinator.artifacts.verify(ref.digest).read_text()
            )
        if job_id:
            evaluator.acknowledge_persisted(job_id)
    for control, record in results.items():
        result = record["result"]
        correct = result.get("correct", {}).get("correct", False)
        if control == "wrong":
            if result.get("job_failure") or "correct" not in result or correct:
                raise RuntimeError("Incorrect control passed exact verification")
        else:
            if not correct:
                raise RuntimeError(f"{control} control failed: {result}")
            speed = result["metrics"]["public"]["geometric_speedup"]
            if control == "aa":
                if aa_measurement is None:
                    raise ValueError("A/A measurement artifact is required")
                validate_aa_bias(aa_measurement["measurement"], settings)
            if control == "slow" and speed >= 0.95:
                raise RuntimeError("Known-slower control was not detected")
    if args.freeze:
        if set(results) != {"aa", "slow", "wrong"} or aa_measurement is None:
            raise ValueError("Freeze requires all three successful controls")
        freeze_campaign(args.campaign, aa_measurement, args.output / "calibration.json")
    print(json.dumps(results, indent=2))


def freeze_campaign(campaign: Path, measurement: dict, controls: Path) -> None:
    manifest_path = campaign / "evaluator/task-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["campaign"].get("frozen"):
        raise ValueError(
            "Campaign is already frozen; create a new campaign to change it"
        )
    validate_aa_bias(measurement["measurement"], manifest["benchmark"])
    manifest["benchmark"]["passes_by_workload"] = measurement["measurement"][
        "passes_by_workload"
    ]
    manifest["campaign"].update(
        frozen=True,
        calibration_sha256=sha256(controls),
        calibration_machine=measurement["machine"],
        calibration_policy="aggregate-and-workload-aa-bias-v1",
    )
    data = json.dumps(manifest, indent=2) + "\n"
    manifest_path.write_text(data)
    (campaign / "inputs/task-manifest.json").write_text(data)
    dependency_path = campaign / "dependencies.json"
    dependencies = json.loads(dependency_path.read_text())
    for artifact in dependencies["artifacts"]:
        if artifact["name"] == "task-manifest.json":
            artifact.update(
                sha256=sha256(manifest_path), size=manifest_path.stat().st_size
            )
    dependency_path.write_text(json.dumps(dependencies, indent=2) + "\n")
    shutil.copyfile(controls, campaign / "calibration.json")


if __name__ == "__main__":
    main()
