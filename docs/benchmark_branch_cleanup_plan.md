# Benchmark branch cleanup plan

Both source branches fork from `27a7dfe`, while the integration target starts at
`55ab494`. Treat each source as a file reservoir; do not merge or rebase its
single bundle commit wholesale. No benchmark execution is part of this plan.

## Paper and open-problem catalog

Create `codex/benchmark-catalog` from the then-current `main`. Import only:

- `examples/paper_tasks/**`
- `examples/open_problem_tasks/**`
- `tests/test_paper_tasks.py`

Do not import the source branch's runtime, container, root README, MkDocs,
general documentation, circle-packing, inference-pipeline, skills, scheduler,
database, or existing-test rewrites.

Split the catalog into reviewable commits:

1. Catalog indexes and shared task contract documentation.
2. AlphaEvolve construction tasks.
3. AlphaEvolve discrete and matrix-multiplication tasks.
4. ShinkaEvolve paper tasks.
5. Approved open-problem tasks and proposals.
6. Static/evaluator unit tests.

Before any task is considered runnable, migrate its configuration to the
current explicit evaluation boundary. A task with sealed/private/adversarial
inputs must use `evaluation_mode: secure`, keep evaluator/private assets outside
the candidate artifact, use pinned mutation/build/runtime image digests, and
declare a bounded public-feedback allowlist. `agent_hidden_paths`, immutable
paths, and worktree policy are not secrecy controls. Tasks that remain
`trusted_local` must be labeled public/cooperative only.

Validation for the catalog branch is limited to import, schema, seed-validity,
and deterministic evaluator unit tests. Any paper-result reproduction,
open-problem search, model-backed proposal, or performance experiment remains a
separate manual campaign.

## Stockfish NNUE

Create `codex/stockfish-nnue-benchmark` from the then-current `main`. Import
only:

- `examples/stockfish_nnue/**`
- `tests/test_stockfish_nnue_evaluator.py`
- the one-line root README link, rewritten against current README structure

Split the source bundle `2f0b627` into:

1. Task manifest, public corpus, evaluator, and replay harness.
2. Pinned Stockfish/network preparation tooling and generated-artifact ignores.
3. Evaluator unit tests.
4. Example documentation and the root catalog link.

Rework the task before merge: private corpus evaluation must use secure mode;
the evaluator and holdout must not be placed in the candidate artifact; all
container images must be digest-pinned; and only bounded aggregate metrics or
feedback may reach later proposals. Keep timing qualification serialized and
machine-specific. Do not clone Stockfish, download the NNUE network, generate a
private corpus, compile the harness, run a smoke test, or perform an NNUE
experiment during the rebase.
