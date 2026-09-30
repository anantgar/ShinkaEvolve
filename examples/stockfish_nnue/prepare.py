"""Freeze source, net, harness, corpus and baseline into a new campaign directory."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import uuid

import yaml

from shinka.secure.archive import DEFAULT_EXCLUDES
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.build import SecureBuildBackend
from shinka.secure.containers import DockerEngine
from shinka.secure.contracts import ResourceLimits, validate_pinned_image
from shinka.secure.dependencies import DependencyManifest, DependencyPreparer

from .corpus import read_tsv, validate_traces
from .policy import fingerprints, load_manifest, sha256
from .seed import prepare_seed

HERE = Path(__file__).resolve().parent


def fetch_inputs(cache: Path, manifest: dict) -> tuple[Path, Path]:
    cache.mkdir(parents=True, exist_ok=True)
    source = cache / "stockfish"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                manifest["stockfish_repository"],
                str(source),
            ],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise ValueError("Cached reference checkout has local edits; use a new cache")
    if head != manifest["stockfish_commit"]:
        subprocess.run(
            ["git", "-C", str(source), "fetch", "origin", manifest["stockfish_commit"]],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "checkout",
                "--detach",
                manifest["stockfish_commit"],
            ],
            check=True,
        )
    network = cache / manifest["network_filename"]
    if not network.exists():
        temporary = network.with_suffix(".download")
        with urllib.request.urlopen(manifest["network_url"], timeout=60) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        if sha256(temporary) != manifest["network_sha256"]:
            temporary.unlink()
            raise ValueError("Downloaded network digest mismatch")
        temporary.replace(network)
    if sha256(network) != manifest["network_sha256"]:
        raise ValueError("Cached network digest mismatch")
    return source, network


def prepare(args) -> None:
    validate_pinned_image(args.image)
    if args.mutation_image:
        validate_pinned_image(args.mutation_image)
    manifest = load_manifest(args.manifest)
    full_search = manifest["schema"] == "stockfish-inference-v3"
    pilot = getattr(args, "pilot", False)
    if pilot and (not full_search or not args.corpus):
        raise ValueError("A private pilot requires a v3 manifest and --corpus")
    source, network = fetch_inputs(args.cache, manifest)
    if args.corpus:
        if args.smoke:
            raise ValueError("Use either --corpus or --smoke")
        corpus = json.loads(args.corpus.read_text())
        validate_traces(corpus["traces"])
        provenance = corpus.get("provenance", {})
        if pilot and (
            provenance.get("kind") != "private_synthetic_pilot"
            or provenance.get("split") != "pilot"
        ):
            raise ValueError(
                "Pilot provenance must identify private synthetic fixtures"
            )
        if not pilot and provenance.get("split") != "holdout":
            raise ValueError(
                "Production corpus must be the holdout of a game-level split"
            )
    elif args.smoke:
        corpus = {
            "traces": read_tsv(HERE / "corpus/public_traces.tsv"),
            "provenance": {
                "kind": "public_smoke",
                "split": "public",
                "source_sha256": sha256(HERE / "corpus/public_traces.tsv"),
            },
        }
        if not args.strict_timing:
            manifest["benchmark"].update(
                pairs=8,
                minimum_sample_seconds=0.25,
                max_log_standard_error=0.02,
                max_workload_log_standard_error=0.05,
                max_aa_log_bias=0.03,
            )
    else:
        raise ValueError(
            "Supply --corpus for production or --smoke for a public smoke test"
        )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    seed = output / "seed_repo"
    inputs = output / "inputs"
    inputs.mkdir()
    prepare_seed(source, seed, inputs / "stockfish-build-context.json")
    # Public contract only: no concrete test inputs, expected values or test code.
    (seed / "SHINKA_TASK.md").write_text(
        f"Target: {args.target}. Fixed Stockfish build: "
        f"ARCH={manifest['targets'][args.target]['arch']}, "
        f"COMP={manifest['targets'][args.target]['compiler']}. "
        "Optimize only the allowed NNUE implementation files. Preserve every integer output, "
        "architecture, feature definition, quantization, interface, and network format. "
        "No approximations, alternate nets, IO, clocks, benchmark detection, result "
        "memoization, extra threads, build-flag changes or changes to search. "
        "The trusted evaluator tests make/evaluate/undo/sibling/reorder/fresh paths, then "
        "scores a paired lower confidence bound on runtime. Tests, benchmark cases, "
        "harnesses and expected outputs are withheld. Surrounding engine source is "
        "readable but immutable; only the NNUE allowlist may change. The pinned "
        "network is available read-only at "
        "/dependencies/files/network.nnue. Keep the final repository free of build artifacts.\n"
    )
    (inputs / "seed-fingerprints.json").write_text(
        json.dumps(fingerprints(seed), sort_keys=True)
    )
    manifest["campaign"] = {
        "target": args.target,
        "image": args.image,
        "corpus_sha256": sha256(args.corpus)
        if args.corpus
        else corpus["provenance"]["source_sha256"],
        "harness_sha256": sha256(HERE / "harness/nnue_replay.cpp"),
        "smoke_only": args.smoke,
        "pilot_only": pilot,
    }
    (inputs / "task-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Large artifacts stay outside the repository and are uploaded once, by content digest.
    shutil.copyfile(network, inputs / "network.nnue")
    dependencies = {
        "artifacts": [
            {
                "name": path.name,
                "url": path.as_uri(),
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "runtime": path.name == "network.nnue",
            }
            for path in sorted(inputs.iterdir())
        ]
    }
    (output / "dependencies.json").write_text(json.dumps(dependencies, indent=2) + "\n")
    # This preparation store is outside candidate and evaluator snapshots.
    store = ContentAddressedStore(args.cache / "preparation-artifacts")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest.from_dict(dependencies)
    )
    candidate, _ = store.put_tree(seed, kind="candidate", excludes=DEFAULT_EXCLUDES)
    engine = DockerEngine(allow_rootful_dedicated_vm=args.dedicated_container_vm)
    engine.preflight(images={args.image})
    build_command = [
        "python3",
        "/opt/nnue/build.py",
        "--target",
        args.target,
        "--jobs",
        "2",
    ]
    if args.sanitize:
        build_command.append("--sanitize")
    built = SecureBuildBackend(
        engine=engine,
        artifacts=store,
        image=args.image,
        limits=ResourceLimits(cpus=2, memory_bytes=4 * 1024**3, pids=128),
        sandbox_user="65532:65532",
    ).build(
        candidate=candidate,
        dependencies=bundle.artifact,
        command=tuple(build_command),
        job_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
        timeout_seconds=1800,
    )
    evaluator = output / "evaluator"
    evaluator.mkdir()
    for name in ("evaluate.py", "policy.py", "corpus.py", "scoring.py"):
        shutil.copyfile(HERE / name, evaluator / name)
    if full_search:
        for name in ("search_evaluate.py", "search_scoring.py"):
            shutil.copyfile(HERE / name, evaluator / name)
        cases = corpus.get("search_cases", corpus["traces"][:12])
        validate_traces(cases)
        if not 1 <= len(cases) <= 256:
            raise ValueError("Search corpus must contain 1–256 cases")
        (evaluator / "search-cases.json").write_text(json.dumps(cases))
    shutil.copyfile(
        store.verify(built.runtime_artifact.digest), evaluator / "baseline.tar"
    )
    (evaluator / "baseline.json").write_text(json.dumps(asdict(built.runtime_artifact)))
    (evaluator / "corpus.json").write_text(json.dumps(corpus))
    (evaluator / "edge-cases.json").write_text(
        json.dumps(read_tsv(HERE / "corpus/public_traces.tsv"))
    )
    shutil.copyfile(inputs / "task-manifest.json", evaluator / "task-manifest.json")
    (evaluator / "private-data.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "shinka-private-data-v1",
                "inputs": {
                    "baseline": "baseline.tar",
                    "baseline_metadata": "baseline.json",
                    "manifest": "task-manifest.json",
                    "corpus": "corpus.json",
                    "edge_cases": "edge-cases.json",
                    **({"search_cases": "search-cases.json"} if full_search else {}),
                },
            }
        )
    )
    configuration = yaml.safe_load((HERE / "shinka.yaml").read_text())
    configuration["evo"]["mutation_image"] = args.mutation_image
    configuration["evo"]["mutable_paths"] = manifest["mutable_paths"]
    job = configuration["job"]
    job.update(
        build_image=args.image,
        runtime_image=args.image,
        build_command=build_command,
        dedicated_container_vm=args.dedicated_container_vm,
    )
    if full_search:
        job["evaluator_entrypoint"] = "search_evaluate.py"
    (output / "shinka.yaml").write_text(yaml.safe_dump(configuration, sort_keys=False))
    (output / "build.log").write_bytes(built.stdout + built.stderr)
    print(
        f"Prepared {output}; smoke_only={args.smoke}. Run calibration before evolution."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "campaign")
    parser.add_argument("--cache", type=Path, default=HERE / ".work")
    parser.add_argument("--manifest", type=Path, default=HERE / "task_manifest.json")
    parser.add_argument("--target", choices=("graviton", "avx2"), default="graviton")
    parser.add_argument("--image", required=True)
    parser.add_argument("--mutation-image")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Use private synthetic pilot data; no production claim",
    )
    parser.add_argument(
        "--strict-timing",
        action="store_true",
        help="Keep production timing gates with the public smoke corpus; this does not make it production data",
    )
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--dedicated-container-vm", action="store_true")
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
