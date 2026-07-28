# ShinkaEvolve AIME agent-scaffold task

This reproduces the paper protocol: all 30 AIME 2024 problems, `gpt-4.1-nano`
as the base model, at most 10 model calls per problem, and three complete runs
per candidate. The mutable `seed_repo/agent.py` is the original one-call
scaffold. The external evaluator and dataset stay outside the evolved repo.
AIME 2023 and 2025 are held-out transfer evaluations, not evolution fitness.

The evaluator downloads the paper repository's dataset at a pinned commit by
default. Pass `--dataset_path` for an offline copy. `--limit` exists only for
smoke tests and is not paper-faithful.

```bash
python evaluate.py --repo_path seed_repo --results_dir /tmp/shinka-aime
```

This is expensive: the paper evaluation makes up to 900 base-model calls for
one candidate (30 problems × 10 calls × 3 runs).

Initialize `seed_repo/` as its own git repository before launching evolution.
