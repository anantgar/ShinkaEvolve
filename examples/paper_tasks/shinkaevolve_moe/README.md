# ShinkaEvolve MoE load-balancing-loss task

The mutable `seed_repo/loss.py` contains the global-batch load-balancing loss
used to seed the paper's search. The external `small_moe_556m.json` records the
evolution model and training configuration from the appendix. Fitness is exactly
`-(final CE averaged over the last 10M tokens + L1 load imbalance)`.

The paper did not release its 556M-parameter trainer, FineWeb preprocessing, or
distributed infrastructure. Consequently, `evaluate.py` is an honest adapter:
`--trainer` must name an external executable that trains the configured model
and writes the requested JSON summary. It does not substitute a synthetic proxy.

The trainer is invoked as:

```text
TRAINER --program_path CANDIDATE --config small_moe_556m.json --output training_metrics.json
```

For a cheap contract check only (not fitness):

```bash
python evaluate.py --repo_path seed_repo --validate_only --results_dir /tmp/shinka-moe-check
```

Shinka initializes the plain `seed_repo/` directory and creates its baseline
Git commit automatically when evolution starts.
