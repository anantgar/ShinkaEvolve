# Repo-Level Transformation Status Report

## Status At A Glance

The repo-only core on `main` is implemented and tested (`777 passed` on 2026-07-21). The `codex/secure-runtime-integration` branch adds the smallest secure evaluation slice and durable Headless proposal sessions without importing the stale branch's benchmark catalogs, NNUE work, or unrelated refactors.

The new path has automated fake-agent and contract coverage. The universal
Headless image is published and its container boundary is qualified, but no
real agent, benchmark, or production deployment has run yet.

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

Focused secure-runtime, Headless, CLI, recovery, and existing compatibility tests use fake agents and fake container runners. On 2026-07-25, the rebased branch passed the focused suite (`152 passed, 2 deselected`) and the full non-integration Python suite (`827 passed, 1 skipped, 2 deselected`).

The Docker qualification passed locally on 2026-07-25 against a freshly built
arm64 image using Docker Desktop. The publish workflow now pulls the exact
multi-architecture manifest digest it produced and reruns the same qualification
on a dedicated Linux runner; the published reference is
`ghcr.io/anantgar/shinka-headless-agents@sha256:7624da6fd6e8138d15b9553732e683d30832d3f090dfb00ad426e528c3dcfc7f`.
No test triggered provider credentials or a real agent call.

## Benchmark Branches

The paper/open-problem catalog and Stockfish NNUE work are not present on this branch and were not run. Their clean split/rebase sequence is documented in [Benchmark Branch Cleanup Plan](benchmark_branch_cleanup_plan.md).

## Required Manual Follow-Up

1. Configure the published image by immutable digest in the secure runtime.
2. Configure operator credentials/auth profiles without placing secrets in proposal metadata or candidate artifacts.
3. Run a deliberately bounded real-agent canary against a public evaluator.
4. Review, rebase, and run the paper/open-problem and NNUE benchmark experiments separately.

Until those gates are complete, describe the feature as implemented and automatically tested, not operationally qualified.
