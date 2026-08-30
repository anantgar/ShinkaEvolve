# Clean checkpoints and deterministic resume

Shinka can preserve its local pseudo-random streams at a clean async boundary.
This is useful for long campaigns, planned machine shutdowns, and provider quota
boundaries.

## Create a checkpoint

Set a seed when starting the campaign:

```bash
shinka_run \
  --task-dir examples/circle_packing \
  --results_dir results/circle_checkpointed \
  --num_generations 100 \
  --random-seed 1234 \
  --checkpoint-resume-mode strict
```

Press **Ctrl-C once** to request a clean checkpoint. Shinka immediately pauses
new proposal admission, lets active provider requests and evaluations finish,
drains database retries and selection-relevant side effects, commits the SQLite
databases, publishes `checkpoint.pkl`, and exits successfully. Pressing Ctrl-C
a second time keeps the usual immediate interrupt behavior and may prevent a
clean checkpoint.

The selection-state checkpoint is published before bounded, best-effort W&B
shutdown, so an observability stall does not delay checkpoint correctness.

Python callers can request the same workflow without a signal:

```python
runner.request_checkpoint_and_exit()
```

Checkpoint publication is atomic. Shinka retains the prior valid generation as
`checkpoint.previous.pkl` and verifies a SHA-256 checksum when loading. A
`checkpoint_resume.json` audit record reports whether a resume was deterministic
and which checkpoint it used.

## Resume modes

Use the same results directory and selection-relevant configuration:

```bash
shinka_run \
  --task-dir examples/circle_packing \
  --results_dir results/circle_checkpointed \
  --num_generations 100 \
  --random-seed 1234 \
  --checkpoint-resume-mode strict
```

`num_generations` remains the total target and may be increased when resuming.

| Mode | Behavior |
|------|----------|
| `strict` | Requires a valid clean checkpoint whose source, configuration, runtime, RNG implementations, and database watermark match. Any mismatch fails before RNG restoration. Recommended for scientific campaigns. |
| `if_available` | Restores a matching checkpoint; otherwise logs and records a best-effort legacy resume. This is the default for compatibility. |
| `reseed` | Intentionally ignores the checkpoint RNG position and starts Shinka-owned streams from the configured seed. The resume is recorded as non-deterministic. |

Strict resume also rejects a database that is ahead of or behind the checkpoint.
This prevents pairing an old random-stream position with newer or incomplete
program and prompt state.

## Guarantee boundary

A clean checkpoint contains Python's global `random` state, NumPy's legacy
global RNG state, every Shinka-owned NumPy `Generator` (including dynamic model
selection), runner counters, bandit/meta state, and exact program/prompt
database watermarks.

The guarantee is deliberately limited: it preserves Shinka-owned random state
at a drained boundary. It does not serialize live coroutines, provider requests,
or evaluator processes, and it cannot make external LLM responses deterministic.
Exact replay tests therefore require deterministic fake providers and
evaluators. Python, NumPy, Shinka source, evaluator, or selection-configuration
changes are rejected by strict mode.

!!! warning "Trusted results directories only"

    Checkpoints use Python pickle. Loading a pickle can execute code, so only
    resume from a results directory you trust.
