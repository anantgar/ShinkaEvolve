"""Evaluate a candidate without an LLM, locally or through the AWS secure scheduler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import yaml

from shinka.launch.secure import SecureEvaluationScheduler, SecureJobConfig


def scheduler(campaign: Path, state: Path, *, backend: str | None = None):
    config = yaml.safe_load((campaign / "shinka.yaml").read_text())
    values = dict(config["job"])
    for name in ("evaluator_repo_path", "dependency_manifest_path"):
        values[name] = str((campaign / values[name]).resolve())
    if backend:
        values["backend"] = backend
    kind = SecureEvaluationScheduler
    if values.get("backend") == "aws":
        from shinka.launch.aws import AwsSecureEvaluationScheduler

        kind = AwsSecureEvaluationScheduler
    # The mutation image is not executed by this LLM-free CLI; the runtime image
    # serves as its required contract identity if no mutation image was configured.
    return kind(
        config=SecureJobConfig(**values),
        state_root=str(state),
        candidate_source=str(campaign / "seed_repo"),
        mutation_image=config["evo"].get("mutation_image") or values["runtime_image"],
        task_id=campaign.name,
        objective=config["evo"]["task_sys_msg"],
        mutable_paths=config["evo"]["mutable_paths"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--backend", choices=("local", "aws"))
    parser.add_argument("--submit-only", action="store_true")
    parser.add_argument("--job-id", help="Collect an already submitted AWS job")
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    evaluation = scheduler(campaign, args.state.resolve(), backend=args.backend)
    output = args.state.resolve() / "public"
    if args.job_id:
        deadline = time.monotonic() + evaluation.config.aws_job_timeout_seconds
        while evaluation.check_job_status(args.job_id):
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "Job is still queued/running; its durable id remains valid"
                )
            time.sleep(evaluation.config.aws_poll_seconds)
        result = evaluation.get_job_results(args.job_id, str(output))
    elif args.submit_only:
        print(
            evaluation.submit_async(
                str(args.candidate or campaign / "seed_repo"), str(output)
            )
        )
        return
    else:
        result, seconds = evaluation.run(
            str(args.candidate or campaign / "seed_repo"), str(output)
        )
        result["elapsed_seconds"] = seconds
    print(json.dumps(result, indent=2))
    job_id = result.get("secure_identities", {}).get("evaluation_job_id")
    if job_id:
        evaluation.acknowledge_persisted(job_id)
    if result.get("job_failure") or not result.get("correct", {}).get("correct"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
