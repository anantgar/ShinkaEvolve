# Repo-Level Transformation Status Report

## Status At A Glance

`main` now contains the repo-only core, the secure evaluation slice, and durable
Headless proposal sessions. The secure work was merged through
`codex/secure-runtime-integration` without importing the stale branch's benchmark
catalogs, NNUE work, or unrelated refactors.

The path has automated fake-agent and contract coverage. The universal Headless
image, container boundary, direct-workspace mutation path, and durable Docker
session reuse are locally qualified. No secure benchmark campaign or production
deployment has run yet.

Repo-only runs accept a seed candidate directory, initialize and commit its Git
baseline automatically when needed, and preserve existing clean Git history.

## Evaluation Modes

| Mode | Intended use | Boundary |
|---|---|---|
| `secure` | Required for sealed, private, or adversarial evaluation. | Sanitized immutable artifacts, isolated mutation/candidate runtimes, trusted evaluator and result validation, bounded public feedback, and durable job recovery. |
| `trusted_local` | Compatibility behavior for public evaluators and cooperative candidates only. | Evaluator receives a local `repo_path`; candidate and evaluator are not separated as a security boundary. |

`agent_hidden_paths`, immutable paths, and Git worktrees remain policy and prompt-scope controls. They are not secrecy boundaries and do not protect evaluator assets, credentials, private inputs, or result stores from hostile code.

## Implemented Secure Slice

1. Candidate repositories are normalized, sanitized, and stored as content-addressed immutable artifacts.
2. Headless mutation and candidate execution use restricted container interfaces; evaluator code and private inputs remain trusted-side.
3. Result files are checked against the exact job/candidate identity, schema, metric allow-list, and bounded public-feedback contract before publication. The evolution-facing metrics and `public_result.json` omit private metrics, evaluator diagnostics/state, timing/resource details, and private artifact contents; separate non-secret lineage digests are retained only for audit and cleanup.
4. Local SQLite job state records launch intent and supports restart reconciliation, acknowledgement, cancellation, and cleanup.
5. Configuration and CLI wiring require an explicit evaluation mode and validate pinned images, evaluator setup, auth profiles, credential environment names, network policy, and resource limits.
6. Each proposal chain has a durable private Headless session home. Persisted session metadata contains only opaque proposal/session identifiers, a home key, schema version, and creation time; credentials are copied only into the runtime container, redacted from returned logs, removed from known durable paths, and cause the session home to be purged if an exact credential copy is detected.
7. Trusted-local `repo_path` evaluation remains available and is explicitly labeled public/cooperative compatibility behavior.

## Automated Evidence

Focused secure-runtime, Headless, CLI, recovery, and existing compatibility
tests use fake agents and fake container runners. On 2026-07-28, the exact CI
commands passed Ruff and Mypy, and Pytest passed (`860 passed, 1 skipped,
2 deselected`). Both hosted CI runs for the integration pull request passed.

The Docker qualification passed again locally on 2026-07-28 using Docker
Desktop's dedicated Linux VM and the published multi-architecture image. It
verified the read-only candidate boundary, secret/environment isolation, network
and Docker-socket denial, resource limits, and cleanup. The publish workflow
pulls the exact manifest digest it produced and reruns the same qualification
on a dedicated Linux runner; the published reference is
`ghcr.io/anantgar/shinka-headless-agents@sha256:7624da6fd6e8138d15b9553732e683d30832d3f090dfb00ad426e528c3dcfc7f`.

A bounded two-turn Docker canary used Antigravity with Gemini 3.5 Flash Low.
Both turns edited the proposal repository directly, and the second turn resumed
the same named durable session. Captured assistant text was not used as proposal
output.

## Benchmark Branches

The paper/open-problem catalog and Stockfish NNUE work are not present on
`main` and were not run. Their clean split/rebase sequence is documented in
[Benchmark Branch Cleanup Plan](benchmark_branch_cleanup_plan.md).

## Required Manual Follow-Up

1. Configure an operator-specific secure job with the published image digest,
   minimal auth profiles, and reviewed provider egress.
2. Run a bounded end-to-end secure evaluation campaign against a public task.
3. Review, rebase, and run the paper/open-problem and NNUE benchmark experiments
   separately.

Until those gates are complete, describe the feature as implemented,
automatically tested, and locally container-qualified, not production-qualified.
