---
name: shinka-inspect
description: Inspect top repo-backed ShinkaEvolve individuals and build a compact agent context bundle from repository summaries, lineage, commits, changed files, public metrics, and feedback.
---

# Inspect repo evolution

Extract the strongest repository individuals from a Shinka run and turn their
persisted summaries into compact, actionable context for the next batch.

## When to use

Use this skill after a run has produced `programs.sqlite`. Use `shinka-run` to
start or continue evolution.

## What it extracts

- top correct individuals by `combined_score`, with an explicit fallback when no
  correct rows exist;
- repository summary, commit, parent, generation, and changed files;
- public metrics and optional evaluator feedback;
- agent route/session metadata;
- compact diff statistics without dumping full repository diffs;
- cross-candidate ideas, performance hypotheses, risks, and frequently changed
  paths parsed from `.shinka/individual.md` summaries.

The executable artifact is the repository commit; summaries are only its
compact context representation.

## Workflow

1. Confirm the run database exists:

```bash
ls -la <results_dir>/programs.sqlite
```

2. Generate the default top-five bundle:

```bash
python3 skills/shinka-inspect/scripts/inspect_best_programs.py \
  --results-dir <results_dir> \
  --k 5
```

3. Optionally tune selection and context size:

```bash
python3 skills/shinka-inspect/scripts/inspect_best_programs.py \
  --results-dir <results_dir> \
  --k 8 \
  --min-generation 10 \
  --max-summary-chars 6000 \
  --out <results_dir>/inspect/top_repositories.md
```

4. Read `<results_dir>/shinka_inspect_context.md` and use its extracted ideas,
   hypotheses, risks, and lineage to plan the next `evo.task_sys_msg`.
5. If exact implementation is needed, inspect the recorded commit or persisted
   diff rather than asking the database summary to stand in for source. Resolve
   a commit in the seed Git history created or reused for that run. If that
   runtime history is unavailable, use the persisted diff and artifacts.

## Arguments

- `--results-dir`: results directory or direct SQLite path;
- `--k`: number of individuals to include, default `5`;
- `--out`: output Markdown path;
- `--max-summary-chars`: summary cap per individual, default `6000`;
- `--min-generation`: optional lower generation bound;
- `--include-feedback` / `--no-include-feedback`: include or omit evaluator
  feedback.

## Privacy and integrity

The bundle includes public metrics and intentionally persisted feedback, but not
private metrics. Summaries are agent-authored claims; treat commits, diffs, and
authoritative evaluator results as the evidence.
