"""Launch a bounded NNUE-only pilot after independent evaluator qualification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import yaml

from shinka.secure.contracts import validate_pinned_image

from .policy import load_manifest, sha256
from .search_campaign import evaluation_identity

MODEL = "headless/codex@gpt-6-astra?effort=high"


def configuration(campaign: Path, image: str, auth: Path, canary: Path) -> dict:
    validate_pinned_image(image)
    manifest = load_manifest(campaign / "evaluator/task-manifest.json")
    frozen = manifest["campaign"]
    if manifest["schema"] != "stockfish-inference-v3" or not frozen.get("frozen"):
        raise ValueError(
            "The campaign must pass controls and be frozen before evolution"
        )
    if frozen.get("evaluation_identity") != evaluation_identity(campaign):
        raise ValueError(
            "Private evaluator, data, baseline or execution settings changed after qualification"
        )
    for name, key in (
        ("calibration.json", "calibration_sha256"),
        ("replication.json", "replication_sha256"),
    ):
        if sha256(campaign / name) != frozen[key]:
            raise ValueError("Qualification evidence changed")
    proof = json.loads(canary.read_text())
    if (
        proof.get("model") != MODEL
        or proof.get("image") != image
        or any(
            proof.get(key) is not True
            for key in (
                "worktree_mutated",
                "session_resumed",
                "credentials_removed_from_session",
            )
        )
    ):
        raise ValueError(
            "Require the real edit/resume canary for this mutation image and model"
        )
    if not auth.is_dir() or auth.is_symlink():
        raise ValueError(
            "The private minimal auth profile must be an existing directory"
        )
    config = yaml.safe_load((campaign / "shinka.yaml").read_text())
    if config["evo"]["mutable_paths"] != manifest["mutable_paths"]:
        raise ValueError("Mutation policy differs from the frozen NNUE-only allowlist")
    config["evo"].update(
        llm_models=[MODEL],
        mutation_image=image,
        agent_auth_profiles={"codex": str(auth.resolve())},
        agent_credential_env_names={},
        dedicated_container_vm=True,
        sandbox_user="65532:65532",
        sandbox_cpus=2,
        sandbox_memory_bytes=4 * 1024**3,
        sandbox_pids=256,
        embedding_model=None,
        llm_dynamic_selection=None,
        meta_rec_interval=None,
        language="cpp",
        num_generations=5,
        generation_target_mode="proposal_ids",
        max_patch_attempts=1,
        max_patch_resamples=1,
        max_novelty_attempts=1,
        headless_proposal_timeout_seconds=1200,
        headless_cleanup_grace_seconds=60,
        enable_controlled_oversubscription=False,
        proposal_buffer_max=0,
        proposal_target_hard_cap=1,
        evolve_prompts=False,
        enable_wandb_logging=False,
        allow_deletions=False,
        allow_binary_files=False,
        allow_lockfile_changes=False,
    )
    config["db"].update(num_islands=1, archive_size=8)
    # A pipeline capacity of one counts the active evaluation as occupied,
    # so mutation/build cannot overlap a measurement on this dedicated host.
    config.update(max_evaluation_jobs=1, max_proposal_jobs=1, max_db_workers=1)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--mutation-image", required=True)
    parser.add_argument("--auth-profile", type=Path, required=True)
    parser.add_argument("--provider-canary", type=Path, required=True)
    args = parser.parse_args()
    campaign, results = args.campaign.resolve(), args.results.resolve()
    if results.exists():
        raise ValueError(
            "Use a new pilot results directory; inspect existing runs before resuming"
        )
    config = configuration(
        campaign, args.mutation_image, args.auth_profile, args.provider_canary
    )
    config_path = campaign / "mini-shinka.yaml"
    if config_path.exists():
        raise ValueError("A pilot config already exists; do not silently replace it")
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    command = [
        sys.executable,
        "-m",
        "shinka.cli.run",
        "--task-dir",
        str(campaign),
        "--config-fname",
        str(config_path),
        "--results_dir",
        str(results),
        "--num_generations",
        "5",
        "--max-evaluation-jobs",
        "1",
        "--max-proposal-jobs",
        "1",
        "--max-db-workers",
        "1",
    ]
    print(
        json.dumps(
            {
                "model": MODEL,
                "mutation_generations": 4,
                "seed": 1,
                "config_sha256": sha256(config_path),
                "private_evaluation_identity": evaluation_identity(campaign),
            }
        ),
        flush=True,
    )
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
