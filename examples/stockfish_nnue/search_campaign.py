"""Prepare fixed search work and qualify a v3 campaign before evolution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import uuid

import yaml

from shinka.launch.secure import SecureJobConfig
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.contracts import ArtifactRef
from shinka.secure.containers import DockerEngine
from shinka.secure.dependencies import DependencyManifest, DependencyPreparer
from shinka.secure.runtime import ContainerCandidateRunner

from .calibrate import control_copy
from .policy import load_manifest, sha256
from .run import scheduler
from .search_evaluate import validate_search_output
from .search_scoring import search_measurement_rejection, validate_search_aa


def evaluation_identity(campaign: Path) -> str:
    """Bind qualification to private code/data, baseline, and execution settings."""
    root = campaign / "evaluator"
    files = {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != "task-manifest.json"
        and "__pycache__" not in path.parts
    }
    job = yaml.safe_load((campaign / "shinka.yaml").read_text())["job"]
    data = json.dumps(
        {"files": files, "job": job}, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(data).hexdigest()


def write_manifest(campaign: Path, manifest: dict) -> None:
    data = json.dumps(manifest, indent=2) + "\n"
    path = campaign / "evaluator/task-manifest.json"
    path.write_text(data)
    (campaign / "inputs/task-manifest.json").write_text(data)
    dependency_path = campaign / "dependencies.json"
    dependencies = json.loads(dependency_path.read_text())
    for artifact in dependencies["artifacts"]:
        if artifact["name"] == "task-manifest.json":
            artifact.update(sha256=sha256(path), size=path.stat().st_size)
    dependency_path.write_text(json.dumps(dependencies, indent=2) + "\n")


def freeze_work(campaign: Path, output: Path) -> None:
    manifest = load_manifest(campaign / "evaluator/task-manifest.json")
    if manifest["schema"] != "stockfish-inference-v3":
        raise ValueError("Requires a v3 campaign")
    if manifest["campaign"].get("search_work_frozen"):
        raise ValueError("Work is already frozen; create a new campaign to change it")
    if (
        platform.system() != "Linux"
        or platform.machine()
        != manifest["targets"][manifest["campaign"]["target"]]["machine"]
    ):
        raise ValueError("Work calibration requires the native Linux target")
    output.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((campaign / "shinka.yaml").read_text())
    job = SecureJobConfig(**config["job"])
    store = ContentAddressedStore(output / "artifacts")
    baseline_meta = ArtifactRef(
        **json.loads((campaign / "evaluator/baseline.json").read_text())
    )
    baseline = store.put_file(
        campaign / "evaluator/baseline.tar", kind=baseline_meta.kind
    )
    if baseline.digest != baseline_meta.digest or baseline.size != baseline_meta.size:
        raise ValueError("Frozen baseline artifact changed")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest.from_dict(
            json.loads((campaign / "dependencies.json").read_text())
        )
    )
    engine = DockerEngine(allow_rootful_dedicated_vm=job.dedicated_container_vm)
    engine.preflight(images={job.runtime_image})
    cases = json.loads((campaign / "evaluator/search-cases.json").read_text())
    settings = manifest["search_benchmark"]
    probes = []
    with ContainerCandidateRunner(
        engine=engine,
        artifacts=store,
        runtime_artifact=baseline,
        dependency_artifact=bundle.runtime_artifact,
        image=job.runtime_image,
        command=tuple(job.candidate_command),
        limits=job.resources,
        job_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
        startup_timeout_seconds=job.startup_timeout_seconds,
        request_timeout_seconds=job.request_timeout_seconds,
        sandbox_user=job.sandbox_user,
    ) as reference:
        loaded = reference.request(
            {
                "op": "search_load",
                "cases": cases,
                "depth": settings["depth"],
                "hash_mb": settings["hash_mb"],
            }
        )
        if loaded.output != {"loaded": len(cases)}:
            raise ValueError("Reference did not load the search cases")
        warmup = reference.request({"op": "search", "passes": 1, "offset": 0})
        validate_search_output(warmup.output, len(cases))
        passes = settings["passes"]
        while True:
            probe = reference.request({"op": "search", "passes": passes, "offset": 0})
            validate_search_output(probe.output, len(cases) * passes)
            probes.append(
                {
                    "passes": passes,
                    "seconds": probe.elapsed_seconds,
                    "output": probe.output,
                }
            )
            (output / "work-probes.json").write_text(
                json.dumps(probes, indent=2) + "\n"
            )
            if probe.elapsed_seconds >= 2 * settings["minimum_sample_seconds"]:
                break
            if passes >= settings["maximum_passes"]:
                raise ValueError("Reference cannot reach the minimum duration")
            passes = min(2 * passes, settings["maximum_passes"])
    settings["passes"] = passes
    manifest["campaign"].update(
        search_work_frozen=True,
        work_probe_sha256=sha256(output / "work-probes.json"),
        search_cases_sha256=sha256(campaign / "evaluator/search-cases.json"),
        work_calibration_machine=platform.machine(),
    )
    write_manifest(campaign, manifest)
    print(
        json.dumps(
            {
                "passes": passes,
                "sample_seconds": probes[-1]["seconds"],
                "work_frozen": True,
            }
        )
    )


def make_control(seed: Path, destination: Path, control: str) -> None:
    if control != "mild":
        control_copy(seed, destination, control)
        return
    control_copy(seed, destination, "aa")
    path = destination / "src/nnue/nnue_feature_transformer.h"
    marker = "        return psqt;"
    code = path.read_text()
    if code.count(marker) != 1:
        raise ValueError("Pinned calibration insertion point changed")
    path.write_text(
        code.replace(
            marker,
            "        volatile unsigned calibration = 0;\n"
            "        for (unsigned i = 0; i < 128; ++i) calibration = calibration + i;\n"
            + marker,
        )
    )


def validate_control(control: str, record: dict, settings: dict) -> None:
    result, diagnostics = record["result"], record["diagnostics"]
    if result.get("job_failure") or "correct" not in result:
        raise RuntimeError(f"{control} had an infrastructure or measurement failure")
    correct = result["correct"].get("correct")
    if control == "wrong":
        rejection = diagnostics.get("rejection", "")
        if correct is not False or not any(
            value in rejection for value in ("Raw NNUE", "Exact raw")
        ):
            raise RuntimeError(
                "Incorrect control was not rejected by exact output verification"
            )
        return
    if correct is not True:
        raise RuntimeError(f"{control} failed correctness or resource limits")
    measurement = diagnostics["measurement"]
    rejection = search_measurement_rejection(measurement, settings)
    if rejection:
        raise RuntimeError(rejection)
    if control == "aa":
        validate_search_aa(measurement, settings)
    elif control == "mild":
        if measurement["upper_confidence_score"] >= 1:
            raise RuntimeError(
                "Injected mild slowdown was not resolved by the interval"
            )
    elif control == "slow":
        if measurement["geometric_speedup"] >= 0.95:
            raise RuntimeError("Gross slowdown was not detected")
    else:
        raise ValueError("Unknown control")


def controls(campaign: Path, output: Path, selected: list[str]) -> None:
    manifest = load_manifest(campaign / "evaluator/task-manifest.json")
    if not manifest["campaign"].get("search_work_frozen") or manifest["campaign"].get(
        "frozen"
    ):
        raise ValueError(
            "Controls require frozen work and a not-yet-qualified campaign"
        )
    output.mkdir(parents=True, exist_ok=False)
    records = {
        "manifest_sha256": sha256(campaign / "evaluator/task-manifest.json"),
        "evaluation_identity": evaluation_identity(campaign),
        "worker_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "worker_hostname": platform.node(),
        "controls": {},
        "qualified": False,
    }
    for control in selected:
        candidate = output / control
        make_control(campaign / "seed_repo", candidate, control)
        evaluation = scheduler(campaign, output / "state" / control, backend="local")
        result, seconds = evaluation.run(
            str(candidate), str(output / f"{control}-result")
        )
        record = {"result": result, "seconds": seconds, "diagnostics": {}}
        records["controls"][control] = record
        job_id = result.get("secure_identities", {}).get("evaluation_job_id")
        if job_id:
            persisted = evaluation.coordinator.get_result(job_id)
            for ref in persisted.artifacts:
                if ref.kind == "stockfish_measurements":
                    record["diagnostics"] = json.loads(
                        evaluation.coordinator.artifacts.verify(ref.digest).read_text()
                    )
        (output / "controls.json").write_text(json.dumps(records, indent=2) + "\n")
        if job_id:
            evaluation.acknowledge_persisted(job_id)
        validate_control(control, record, manifest["search_benchmark"])
        print(
            json.dumps(
                {
                    "control": control,
                    "passed": True,
                    "seconds": seconds,
                    "metrics": result.get("metrics"),
                }
            ),
            flush=True,
        )
    records["qualified"] = True
    (output / "controls.json").write_text(json.dumps(records, indent=2) + "\n")


def freeze(campaign: Path, primary: Path, replication: Path) -> None:
    manifest_path = campaign / "evaluator/task-manifest.json"
    manifest = load_manifest(manifest_path)
    if manifest["campaign"].get("frozen"):
        raise ValueError("Campaign is already frozen")
    primary_data, replication_data = (
        json.loads(path.read_text()) for path in (primary, replication)
    )
    worker_ids = [
        record.get("worker_boot_id") for record in (primary_data, replication_data)
    ]
    if (
        any(not isinstance(value, str) or not value for value in worker_ids)
        or worker_ids[0] == worker_ids[1]
    ):
        raise ValueError("A/A replication must come from a different worker boot")
    for record in (primary_data, replication_data):
        if (
            record["manifest_sha256"] != sha256(manifest_path)
            or record.get("evaluation_identity") != evaluation_identity(campaign)
            or not record["qualified"]
        ):
            raise ValueError("Controls did not qualify this exact campaign")
        for control, value in record["controls"].items():
            validate_control(control, value, manifest["search_benchmark"])
    if set(primary_data["controls"]) != {"aa", "mild", "slow", "wrong"} or set(
        replication_data["controls"]
    ) != {"aa"}:
        raise ValueError(
            "Require all four primary controls and independent A/A replication"
        )
    manifest["campaign"].update(
        frozen=True,
        calibration_policy="independent-process-search-controls-v1",
        calibration_sha256=sha256(primary),
        replication_sha256=sha256(replication),
        evaluation_identity=evaluation_identity(campaign),
    )
    write_manifest(campaign, manifest)
    shutil.copyfile(primary, campaign / "calibration.json")
    shutil.copyfile(replication, campaign / "replication.json")
    print("Campaign qualified and frozen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze-work", "controls", "freeze"))
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--controls",
        nargs="+",
        choices=("aa", "mild", "slow", "wrong"),
        default=["aa", "mild", "slow", "wrong"],
    )
    parser.add_argument("--primary", type=Path)
    parser.add_argument("--replication", type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    if args.action == "freeze-work":
        freeze_work(campaign, args.output.resolve())
    elif args.action == "controls":
        controls(campaign, args.output.resolve(), args.controls)
    else:
        freeze(campaign, args.primary.resolve(), args.replication.resolve())


if __name__ == "__main__":
    main()
