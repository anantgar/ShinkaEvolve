# ShinkaEvolve Backlog

## Purpose

This is the only project backlog. It contains unresolved work for the repo-agent
fork; completed transformation history and experiment-debugging chronology have
been removed. Current behavior belongs in
[System Architecture](system_architecture.md).

Priority meanings:

- **P0** blocks production qualification or a defensible experiment.
- **P1** is the next integration or durability work.
- **P2** is targeted product/code cleanup.

## P0: Qualify Secure Operation

### Run A Bounded End-To-End Secure Campaign

The secure slice is implemented and locally container-qualified, but it has not
run a complete evaluation campaign.

- [ ] Choose a public, non-sensitive task with a reviewed secure evaluator.
- [ ] Pin mutation, build, and runtime image digests.
- [ ] Create an operator configuration with minimal agent auth profiles,
      explicit credential environment names, reviewed provider egress, and
      bounded CPU, memory, process, file, output, and timeout limits.
- [ ] Run seed evaluation, at least two real proposal generations, evaluator
      completion, persistence, restart reconciliation, acknowledgement, and
      cleanup.
- [ ] Verify that candidate execution containers cannot read evaluator/private
      state, credentials, the Docker socket, or unrelated host paths; mutation
      containers receive only the provider credentials they require.
- [ ] Archive the run manifest, database, public results, candidate digests,
      summaries, diffs, logs, and container qualification output.
- [ ] Record any failure as a new versioned run; do not patch the framework and
      continue the same controlled campaign.

Done when the run can be reproduced from archived artifacts and a reviewer can
trace every accepted candidate from parent artifact to validated public result.

### Qualify Every Configured Headless Route

The existing real canary covers an Antigravity route. Each route admitted to a
campaign needs its own evidence.

- [ ] Run an authenticated throwaway-workspace mutation for every configured
      provider/model.
- [ ] Verify named-session resume with a second turn.
- [ ] Verify worktree-result mode does not depend on final prose extraction.
- [ ] Verify timeout cleanup terminates only owned processes and leaves
      concurrent/unrelated processes untouched.
- [ ] Record native CLI/Headless/image versions, timeout, usage/pricing status,
      and route-health failure class in the run evidence.

## P0: Run A Defensible Original-Versus-Repo Experiment

- [ ] Choose and pre-register the claim: historical reproduction, modern
      re-benchmark, component ablation, or native end-to-end comparison.
- [ ] Pin the original Shinka commit instead of using moving `upstream/main`.
- [ ] Freeze task, evaluator and dataset hashes, dependencies, model versions,
      prompts/tool policy, editable paths, hardware, network, worker counts,
      seeds, retry rules, and budgets.
- [ ] Keep authoritative evaluation identical across arms. Use secure mode for
      sealed/private/adversarial inputs.
- [ ] Define primary metrics, quality thresholds, sample size, uncertainty
      method, and stopping rules before final runs.
- [ ] Report raw requests, valid proposals, novelty acceptances, evaluations,
      correct candidates, and target-reaching candidates.
- [ ] Report quality against evaluations, raw requests, total cost, and wall
      time, including unknown agent usage rather than treating it as free.
- [ ] Preserve every run manifest and artifact needed to audit the comparison.

If only the original modern loop and the complete repo-agent system are run,
label the result a native end-to-end comparison. It cannot isolate the coding
agent, repository representation, or summary-based novelty as the cause.

## P1: Integrate Benchmark Catalogs

### Paper And Open-Problem Tasks

Candidate branch: `codex/example-tasks` at `c061806`.

- [ ] Rebase the four reviewable catalog commits onto current `main`.
- [ ] Split the large catalog payload into reviewable commits for shared
      indexes/contracts, AlphaEvolve construction tasks, AlphaEvolve
      discrete/matrix tasks, ShinkaEvolve paper tasks, open-problem tasks, and
      evaluator/static tests.
- [ ] Import the new `examples/paper_tasks/**`,
      `examples/open_problem_tasks/**`, and their focused tests without
      overwriting unrelated current runtime or documentation work.
- [ ] Review the branch's edits to existing circle-packing and inference
      examples separately; keep only changes required by the current contracts.
- [ ] Classify each task as public/cooperative or sealed/private/adversarial.
- [ ] Move evaluator and holdout assets outside candidate artifacts for every
      secure task.
- [ ] Require digest-pinned images, bounded public feedback, and explicit
      resource/network policies.
- [ ] Validate imports, schemas, seed validity, and deterministic evaluator
      tests before merge.
- [ ] Treat actual reproduction/search runs as separate campaigns; merging a
      catalog makes no benchmark claim.

### Stockfish NNUE

Candidate branch: `codex/assess-shinkaevolve-for-nnue` at `2f0b627`, based on an
old fork point.

- [ ] Rebase or transplant only `examples/stockfish_nnue/**`, its focused test,
      and a current README catalog link.
- [ ] Split the old bundle into task/evaluator/replay, pinned preparation
      tooling, evaluator tests, and documentation commits.
- [ ] Keep the private corpus and evaluator outside the candidate artifact.
- [ ] Convert private-corpus evaluation to secure mode.
- [ ] Pin Stockfish/network preparation and all container images by immutable
      digest.
- [ ] Validate deterministic replay, malformed candidate output, correctness
      gating, and resource limits.
- [ ] Keep timing qualification serialized and machine-specific.
- [ ] Do not clone Stockfish, download a network, generate private data, compile
      the harness, or run an experiment as part of the rebase.
- [ ] Merge the benchmark definition before running a separate, frozen
      experiment campaign.

## P1: Re-run The Circle-Packing Experiment

The earlier 150-target run was an integration stress test performed across live
framework changes. Its results are diagnostic and must not be treated as one
controlled experiment.

- [ ] Confirm that the 150 target means 150 persisted evaluated candidates,
      including generation zero, rather than 150 allocated proposal IDs.
- [ ] Pass a 5–10 evaluated-candidate multi-agent/W&B canary from a frozen
      framework, config, evaluator, Headless, provider, and environment
      manifest.
- [ ] Verify every configured route with authenticated workdir/session canaries
      before the multi-agent run.
- [ ] Run the full 150-target campaign without framework edits.
- [ ] Reuse one logical W&B run ID across process resume; start a new versioned
      W&B run if code or configuration changes.
- [ ] Preserve failed-proposal evidence and report the complete proposal funnel,
      route health, costs, quotas, timing, and requested/effective concurrency.
- [ ] Retain detailed file logs while keeping interactive console verbosity
      bounded.

## P1: Artifact Retention And Operations

- [ ] Define retention periods for candidate artifacts, workspaces, agent
      session homes, evaluator outputs, failed proposals, logs, and job records.
- [ ] Add external/object storage for large diffs, artifacts, and long-running
      run state; keep content digests in SQLite.
- [ ] Make garbage collection acknowledgement-aware so it cannot remove an
      artifact referenced by an unpersisted or unreconciled job.
- [ ] Add export/restore tests covering a database, summaries, secure artifacts,
      run manifest, and final candidates.
- [ ] Document operator recovery for disk exhaustion, lost workers, corrupt
      artifacts, expired credentials, and interrupted cleanup.

## P1: Complete The Inference-Pipeline Benchmark Template

The current example measures correctness and latency, but
`peak_memory_bytes` and `compile_seconds` are placeholders and it is not yet a
qualified ML inference benchmark.

- [ ] Integrate the reviewed secure-service fixture from
      `codex/example-tasks` or bring the current Python fixture to the same
      evaluator/candidate process boundary.
- [ ] Add real peak-memory and compile/graph-capture measurements.
- [ ] Add warm-up, repeated measurements, controlled seeds, hardware/runtime
      metadata, and stability reporting.
- [ ] Keep correctness/tolerance failures as a hard gate before performance
      scoring.
- [ ] Add regression tests for malformed output, correctness regression, noisy
      timing, and metric serialization.
- [ ] Run performance qualification separately from merging the template.

## P2: Code TODO Inventory

This inventory covers every explicit `TODO` comment outside documentation and
notebooks as of `main` at `7a66333`. `TODO_AGENT_SUMMARY` is a required template
sentinel, not an engineering TODO.

### Simplify The Individual Summary Schema

Source: `shinka/repo/summary.py:11`

- [ ] Decide the smallest summary needed for parent context, novelty, audit, and
      reproduction.
- [ ] Introduce a versioned schema and migration/compatibility behavior rather
      than silently changing `repo-individual-v1`.
- [ ] Update templates, validators, prompts, tests, and persisted-summary
      readers together.

### Make Model Environment Validation Harness-Aware

Source: `shinka/core/async_runner.py:300`

- [ ] Validate mutation, meta, novelty, prompt-evolution, and embedding models
      through their actual provider/harness requirements.
- [ ] Keep secure Headless validation container-aware and auxiliary text-model
      validation host-aware.
- [ ] Add mixed-provider and mixed-harness startup tests.

### Resolve The Redundant `EvolutionConfig.agent_model`

Source: the stable repo-contract backlog; `shinka/core/config.py` still declares
`agent_model`, while mutation routing uses `llm_models` and the selected
Headless route.

- [ ] Remove the unused configuration field, or define and validate a single
      compatibility mapping to `llm_models`.
- [ ] Keep the persisted `Program.agent_model` field: it records the route that
      actually produced an individual and is not the redundant setting.

### Add Per-File Repository Complexity Analysis

Source: the stable repo-contract backlog.

- [x] Stop passing Markdown `repo_summary` text with language `repo` through
      source-code complexity analysis.
- [x] Select candidate, mutable source files only; exclude summaries, docs,
      data, generated/vendor files, lockfiles, and binaries. Mutable test
      sources are included.
- [x] Analyze supported source files independently by language and aggregate
      repository LOC, logical LOC, cyclomatic complexity, nesting, Halstead
      volume, weighted maintainability, and file-level score distribution.
- [x] Store a versioned structured repository-complexity record in metadata,
      while retaining a single display-compatible `Program.complexity` value.
- [x] Update sync/async database paths and complexity tests consistently. Keep
      complexity out of archive selection until the metric is qualified.
- [ ] Defer: cache per-file metrics by Git blob/content hash plus analyzer
      version, so inherited files are not reanalyzed across worktrees.
- [ ] Defer: add child-versus-parent complexity deltas from before/after metrics
      for each changed source file. Do not analyze raw unified diff text as
      source code; retain diff churn separately as an edit-size signal.

### Remove The Redundant Seed Summary Directory Creation

Source: `shinka/core/async_runner.py:1837`

- [ ] Confirm every generation-zero path creates the `.shinka` parent through
      the worktree/policy layer.
- [ ] Remove the defensive `summary_path.parent.mkdir(...)` only after tests
      cover plain seeds, existing Git seeds, trusted-local mode, and secure
      mode.

### Audit Legacy Generation File Paths

Source: `shinka/core/async_runner.py:2641`

- [ ] Trace `exec_fname`, generation result directories, and lock-file usage
      through proposal submission and both schedulers.
- [ ] Remove only repo-inactive single-file plumbing; retain generation
      directories, results, and duplicate-generation locking where still used.
- [ ] Add a regression test proving trusted-local and secure repo evaluation no
      longer depend on a generated `main.<ext>` path.

### Use Diversity-Aware Crossover Inspiration Selection

Source: `shinka/prompts/prompts_cross.py:43`

- [ ] Replace uniform random inspiration choice with a documented policy using
      parent/inspiration embedding distance and quality.
- [ ] Define behavior when embeddings are absent.
- [ ] Add deterministic tests for diversity, quality tie-breaking, and empty
      inspiration sets.

### Remove The Obsolete Diff Summary Helper

Source: `shinka/edit/summary.py:7`

- [ ] Confirm `summarize_diff` has no supported runtime consumer.
- [ ] Remove the module, its `shinka.edit` export, and the dedicated Unicode
      compatibility test, or move a real consumer to a maintained implementation
      first.

### Quarantine Repo-Incompatible Legacy Edit Paths

Source: the former transformation backlog's `PaperEdit`/single-file
compatibility objective.

- [ ] Inventory `shinka/edit`, `shinka/edit/async_apply.py`, and their callers.
- [ ] Move generic async file/embedding helpers used by the repo runner out of
      the legacy patch-application module.
- [ ] Ensure the active runner cannot enter code-string diff/full-rewrite paths.
- [ ] Either retain the legacy public helpers as an explicitly isolated
      upstream-compatibility surface or remove them with a migration note; do
      not leave ambiguous half-supported behavior.

### Document The Provider-Specific Headless Contract

Source: the circle-packing audit's unresolved Headless documentation items.

- [ ] Enumerate generic API-style `llm_kwargs` that Headless ignores instead of
      forwarding to native agents, and warn or reject misleading settings.
- [ ] Document Cursor's normal session-minting/`create-chat` behavior.
- [ ] Document Antigravity transcript-backed resume and the durable-home
      requirements for each provider.
- [ ] Keep worktree-result mode, normalized usage/pricing limitations, and
      supported output modes explicit in the configuration and LLM references.

### Finish Repo-Only Internal Cleanup

Source: the former transformation plan's cleanup section.

- [ ] Remove `shinka/agents/validation.py` if it remains unused, or integrate it
      as the canonical validation-tier implementation.
- [ ] Rename internal database parameters and locals from `repo` back to
      `program` where they refer to `Program`; reserve repo names for actual
      repository artifacts and keep compatibility aliases only at stable
      boundaries.
- [ ] Classify legacy edit/marker tests as an intentional upstream-compatibility
      suite or remove them with the isolated legacy surface.
- [ ] Add a migration note for opening pre-repo and earlier repo-mode run
      databases, including which schema upgrades are automatic and which
      artifacts cannot be reconstructed.

### Replace Misleading Island Preset Provenance

Sources:

- `shinka/configs/database/island_small.yaml:1`
- `shinka/configs/database/island_medium.yaml:1`
- `shinka/configs/database/island_large.yaml:1`

- [ ] Validate each preset against current island/archive behavior and intended
      small, medium, and large budgets.
- [ ] Replace the copied-example comments with rationale for the actual values.
- [ ] Add config-composition tests that instantiate all three presets.

## P2: Branch Hygiene

- [ ] After confirming their functionality exists on `main`, archive or delete
      obsolete integration branches such as `universal-headless-image` and
      `codex/secure-eval-single-file`.
- [ ] Keep benchmark definition branches separate from experiment-result
      branches.
- [ ] Do not merge old bundle commits wholesale; transplant and review
      purpose-specific commits against current security contracts.

## Controlled-Run Exit Criteria

Before calling any run a benchmark or production qualification:

- [ ] The framework commit, config, evaluator, dependencies, images, agents,
      models, quotas, and W&B identity are frozen and recorded.
- [ ] Unit, integration, fake-agent end-to-end, secure boundary, recovery, and
      route canaries pass for the exact configuration.
- [ ] The run target has an explicit denominator.
- [ ] Minimum request demand fits declared quotas.
- [ ] Failed proposals retain enough evidence for diagnosis before cleanup.
- [ ] No framework edits occur during the run.
- [ ] Public results contain no private metrics, evaluator state, secrets, or
      unbounded feedback.
