"""Private ASan/UBSan correctness verification; never emits timing fitness."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import random
import uuid

import yaml

from shinka.launch.secure import SecureJobConfig
from shinka.secure.archive import DEFAULT_EXCLUDES
from shinka.secure.artifacts import ContentAddressedStore
from shinka.secure.build import SecureBuildBackend
from shinka.secure.containers import DockerEngine
from shinka.secure.contracts import ArtifactRef
from shinka.secure.dependencies import DependencyManifest, DependencyPreparer
from shinka.secure.runtime import ContainerCandidateRunner

from .evaluate import check_exact
from .policy import load_manifest
from .search_evaluate import require_search_match, validate_search_output


def verify(campaign: Path, candidate: Path, output: Path) -> dict:
    manifest = load_manifest(campaign / "evaluator/task-manifest.json")
    if (
        manifest["schema"] != "stockfish-inference-v3"
        or platform.system() != "Linux"
        or platform.machine()
        != manifest["targets"][manifest["campaign"]["target"]]["machine"]
    ):
        raise ValueError("Sanitizer verification requires a v3 native Linux campaign")
    output.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load((campaign / "shinka.yaml").read_text())
    job = SecureJobConfig(**config["job"])
    store = ContentAddressedStore(output / "artifacts")
    bundle = DependencyPreparer(store).prepare(
        DependencyManifest.from_dict(
            json.loads((campaign / "dependencies.json").read_text())
        )
    )
    source, _ = store.put_tree(candidate, kind="candidate", excludes=DEFAULT_EXCLUDES)
    baseline_meta = ArtifactRef(
        **json.loads((campaign / "evaluator/baseline.json").read_text())
    )
    baseline = store.put_file(
        campaign / "evaluator/baseline.tar",
        kind=baseline_meta.kind,
        expected_digest=baseline_meta.digest,
    )
    engine = DockerEngine(allow_rootful_dedicated_vm=job.dedicated_container_vm)
    engine.preflight(images={job.build_image, job.runtime_image})
    build = SecureBuildBackend(
        engine=engine,
        artifacts=store,
        image=job.build_image,
        limits=job.resources,
        sandbox_user=job.sandbox_user,
    ).build(
        candidate=source,
        dependencies=bundle.artifact,
        command=tuple([*job.build_command, "--sanitize"]),
        job_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
        timeout_seconds=1800,
    )
    (output / "build.log").write_bytes(build.stdout + build.stderr)
    (output / "runtime.json").write_text(
        json.dumps(asdict(build.runtime_artifact), indent=2)
    )
    traces = json.loads((campaign / "evaluator/corpus.json").read_text())["traces"]
    edges = json.loads((campaign / "evaluator/edge-cases.json").read_text())
    cases = json.loads((campaign / "evaluator/search-cases.json").read_text())

    def runner(artifact):
        return ContainerCandidateRunner(
            engine=engine,
            artifacts=store,
            runtime_artifact=artifact,
            dependency_artifact=bundle.runtime_artifact,
            image=job.runtime_image,
            command=tuple(job.candidate_command),
            limits=job.resources,
            job_id=str(uuid.uuid4()),
            attempt_id=str(uuid.uuid4()),
            startup_timeout_seconds=180,
            request_timeout_seconds=300,
            sandbox_user=job.sandbox_user,
        )

    with runner(baseline) as reference, runner(build.runtime_artifact) as sanitized:
        checked = check_exact(
            reference,
            sanitized,
            traces + edges,
            manifest["benchmark"],
            lambda _: None,
            random.Random(manifest["benchmark"]["seed"]),
            rounds=2,
        )
        depth = min(11, manifest["search_benchmark"]["depth"])
        for service in (reference, sanitized):
            loaded = service.request(
                {
                    "op": "search_load",
                    "cases": cases,
                    "depth": depth,
                    "hash_mb": manifest["search_benchmark"]["hash_mb"],
                }
            )
            if loaded.output != {"loaded": len(cases)}:
                raise ValueError("Sanitizer search setup failed")
        expected = reference.request({"op": "search", "passes": 1, "offset": 0}).output
        actual = sanitized.request({"op": "search", "passes": 1, "offset": 0}).output
        validate_search_output(expected, len(cases))
        require_search_match(expected, actual, len(cases))
        if reference.stderr or sanitized.stderr:
            raise ValueError("Sanitizer or reference emitted unexpected diagnostics")
    result = {
        "passed": True,
        "candidate_digest": source.digest,
        "sanitizers": ["address", "undefined"],
        "exact_values_checked": checked,
        "searches": len(cases),
        "search_depth": depth,
        "search_fingerprint": expected["checksum"],
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    print(
        json.dumps(
            verify(
                campaign,
                (args.candidate or campaign / "seed_repo").resolve(),
                args.output.resolve(),
            )
        )
    )


if __name__ == "__main__":
    main()
