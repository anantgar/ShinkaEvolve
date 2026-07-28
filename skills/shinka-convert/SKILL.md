---
name: shinka-convert
description: Convert an existing codebase into a repo-backed ShinkaEvolve task while preserving its multi-file runtime contract and adding an external repository evaluator.
---

# Shinka repository conversion

Convert an existing project into a Shinka task without reducing it to one
mutable source file. The result remains compatible with `shinka-run`.

## When to use

Use this skill when the user wants Shinka to optimize an existing script,
package, service, solver, or repository. Use `shinka-setup` for a new task built
only from a description.

## Default output

Create `./shinka_task/` unless the user chooses another location:

```text
shinka_task/
  evaluate.py
  seed_repo/
    ...minimal runnable candidate repository...
  shinka.yaml
  run_evo.py
```

Leave the original source tree unchanged unless the user explicitly requests an
in-place task.

## Workflow

1. Inspect the existing project:
   - entrypoints, package layout, dependencies, build and test commands;
   - input/output contracts and performance bottlenecks;
   - authoritative correctness checks and score direction;
   - files required at runtime.
2. Choose the smallest repository snapshot that remains honestly runnable.
   Preserve multi-file structure where the runtime depends on it.
3. Copy candidate-owned source, configuration, and public runtime assets into
   `seed_repo/`. Keep the evaluator and its scoring fixtures outside it.
4. Preserve the candidate's normal interface. Add only thin adapters needed for
   deterministic evaluation; do not introduce EVOLVE blocks or rename the
   project to `initial.<ext>`.
5. Implement `evaluate.py` with `--repo_path` and `--results_dir`.
   It may import, compile, start, or invoke files beneath the supplied
   repository path.
6. Define mutation policy:
   - default `mutable_paths: []` for whole-repository agent freedom;
   - use a non-empty allow-list only for an explicit user boundary;
   - keep evaluator code outside `seed_repo/` rather than relying on prompts.
7. Leave a newly created `seed_repo/` as a normal directory; Shinka initializes
   and commits it automatically at evolution startup. If conversion deliberately
   preserves an existing independent Git repository, require a clean working
   tree rather than committing the user's pending changes.
8. Copy and tailor the bundled `scripts/shinka.yaml` and
   `scripts/run_evo.py`.
9. Smoke-test the candidate seed:

```bash
smoke_dir=$(mktemp -d)
python3 evaluate.py --repo_path seed_repo --results_dir "$smoke_dir"
```

10. Verify `metrics.json`, `correct.json`, score direction, repeatability, and
    dependency documentation.
11. Hand off to `shinka-run` when the user wants evolution launched.

Git metadata is a startup concern, not a conversion prerequisite. A plain seed,
an unborn Git seed, or a seed directory tracked by an enclosing repository is
initialized and committed automatically. An existing independent repository
with `HEAD` is reused only when clean.

## Conversion principles

- Optimize the real project shape instead of a copied function divorced from
  its build and runtime context.
- Keep correctness checks stronger than the initial implementation.
- Prefer stable seeds and bounded evaluation time.
- Do not make generated caches or result directories part of the seed commit.
- Let coding agents add, modify, rename, or delete normal candidate files when
  the user has not requested a narrower policy.
- Treat `.shinka/individual.md` as Shinka-managed lineage context. The coding
  agent updates it for each proposal; it is not a seed source file.

## Evaluator contract

`metrics.json` should contain:

```json
{
  "combined_score": 0.0,
  "public": {},
  "private": {},
  "text_feedback": ""
}
```

`correct.json` should contain:

```json
{
  "correct": true,
  "error": ""
}
```

Only public metrics and intentionally enabled feedback should guide future
mutations. Higher `combined_score` must mean better.

## Safety boundary

Current mainline repo evaluation is trusted-local. `agent_hidden_paths`,
immutable paths, and worktrees do not secure secrets from an untrusted agent or
candidate process. Use public evaluators and non-sensitive data until the secure
runtime is integrated.
