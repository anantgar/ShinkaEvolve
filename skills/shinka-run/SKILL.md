---
name: shinka-run
description: Preflight, launch, resume, and review repo-backed ShinkaEvolve runs from a task containing `evaluate.py` and a candidate `seed_repo/` directory.
---

# Run repo-agent evolution

Run Shinka batches through `shinka_run`. Mutation models are Headless coding
agents that edit isolated git worktrees; the repository state is the proposal.

## When to use

Use this skill when a repo-mode task already exists and the user wants to start,
resume, or extend evolution. Use `shinka-setup` or `shinka-convert` first when
the task contract is missing.

## Preflight

1. Inspect the task and effective config:

```bash
task_dir=/path/to/task
shinka_run --help
test -d "$task_dir/seed_repo"
```

Require:

- external `<task_dir>/evaluate.py`;
- a candidate `<task_dir>/seed_repo` directory;
- evaluator support for `--repo_path`;
- only `headless/<agent>[@model][?options]` entries in
  `evo.llm_models`;
- an explicit budget or total generation target.

Empty `mutable_paths` means whole-repository mutation. Do not invent a narrow
allow-list.

Shinka initializes a plain seed directory and creates its baseline commit when
the run starts. It does the same for an independent Git repository without a
`HEAD`. If an existing seed is already an independent repository with history,
startup preserves that history and rejects uncommitted changes instead of
silently capturing them.

2. Run the baseline evaluator before spending model budget:

```bash
smoke_dir=$(mktemp -d)
python3 <task_dir>/evaluate.py \
  --repo_path <task_dir>/seed_repo \
  --results_dir "$smoke_dir"
```

3. Validate models:

- Run `shinka_models --verbose` for direct API auxiliary models and embeddings.
- Headless route strings are not required to appear in that catalog.
- For every mutation route, validate its native CLI authentication and run the
  real worktree/session canary:

```bash
python3 -m shinka.headless_canary \
  --model 'headless/codex@gpt-5.5?effort=high'
```

The canary must edit the requested repository and resume the same named session.
Do not substitute `headless --check` for this.

4. For a controlled experiment, also record:

- framework commit and dirty state;
- evaluator and config hashes;
- Headless and native CLI versions;
- proposal timeout and cleanup grace;
- worker counts, rate limits, daily quotas, and expected auxiliary demand.

Run the full Shinka test suite and fake-agent end-to-end test only when operating
from a Shinka source checkout or validating framework changes. They are not
prerequisites for ordinary use of an installed release.

## Launch

If the user supplied a complete configuration, launch it without asking them to
repeat it. Ask before launch only when a missing choice changes cost, provider,
mutation scope, or output ownership.

```bash
shinka_run \
  --task-dir <task_dir> \
  --config-fname shinka.yaml \
  --results_dir <results_dir> \
  --num_generations 40 \
  --set evo.llm_models='["headless/codex@gpt-5.5?effort=high"]' \
  --set evo.embedding_model=null \
  --max-evaluation-jobs 2 \
  --max-proposal-jobs 2 \
  --max-db-workers 2
```

Use only namespaced `evo`, `db`, and `job` overrides. Put long task prompts in
the YAML file instead of shell-escaping them.

## Target and continuation semantics

`--num_generations` is the total persisted-candidate target for the results
directory, not the number to add in this invocation. To extend a completed
40-candidate run by 20, reuse the results directory and set the new target to
60:

```bash
shinka_run \
  --task-dir <task_dir> \
  --config-fname shinka.yaml \
  --results_dir <same_results_dir> \
  --num_generations 60
```

Use a new results directory only for an intentional independent run or fork.
Do not resume against an unintentionally changed seed, evaluator, or config.

## Monitor and handoff

After a batch:

1. Check the terminal outcome, `run_manifest.json`, `programs.sqlite`,
   generation/attempt artifacts, and failed proposal records.
2. Use `shinka-inspect` to summarize top repository individuals.
3. Report score/correctness trends, route failures, costs when known, and the
   current total candidate count.
4. Before spending on another unrequested batch, ask for or infer authorization
   from the user's stated budget/autonomy. A request for fully autonomous
   execution authorizes continuation within that scope.
5. Convert new search directions into `evo.task_sys_msg` for the next target.

Do not patch the framework during a controlled run. Stop and label the run
diagnostic if a framework defect requires code changes.

## Safety boundary

Mainline evaluation is trusted-local. Path policy controls what Shinka accepts;
it does not seal evaluator data or host credentials from a malicious agent or
candidate.
