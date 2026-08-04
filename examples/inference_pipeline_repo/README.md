# Secure Inference-Shaped Service Example

This task exercises the secure persistent-service architecture without importing
candidate code into the evaluator. The evaluator repository contains private
cases; the candidate container receives only request values over framed stdio.
Latency is measured by the trusted evaluator.

The checked-out `seed_repo/` may be a plain directory. Shinka initializes its
Git repository and creates the baseline commit automatically when the run
starts. If it is already an independent Git repository with history, keep its
working tree clean.

Before a secure run, configure the pinned mutation/build/runtime images,
provider network, and agent authentication in `shinka.yaml` for the local
deployment.

```bash
shinka_run \
  --task-dir examples/inference_pipeline_repo \
  --config-fname shinka.yaml \
  --evaluation-mode secure \
  --results_dir results/inference_pipeline_repo \
  --num_generations 2
```

The initial candidate intentionally returns the identity transform. A successful
mutation changes only `src/pipeline.js` to satisfy the private affine cases.
