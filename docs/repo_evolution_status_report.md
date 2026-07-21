# Repo-Level Transformation Status Report

## Status At A Glance

The repo-only core on `main` is implemented and tested (`777 passed` on 2026-07-21). The `codex/secure-runtime-integration` branch adds the smallest secure evaluation slice and durable Headless proposal sessions without importing the stale branch's benchmark catalogs, NNUE work, or unrelated refactors.

The new path has automated fake-agent and contract coverage. It has not run a real agent, built or published an image, executed a benchmark, or been qualified as an operational deployment.

## Evaluation Modes

| Mode | Intended use | Boundary |
|---|---|---|
| `secure` | Required for sealed, private, or adversarial evaluation. | Sanitized immutable artifacts, isolated mutation/candidate runtimes, trusted evaluator and result validation, bounded public feedback, and durable job recovery. |
| `trusted_local` | Compatibility behavior for public evaluators and cooperative candidates only. | Evaluator receives a local `repo_path`; candidate and evaluator are not separated as a security boundary. |

`agent_hidden_paths`, immutable paths, and Git worktrees remain policy and prompt-scope controls. They are not secrecy boundaries and do not protect evaluator assets, credentials, private inputs, or result stores from hostile code.

## Implemented Secure Slice

1. Candidate repositories are normalized, sanitized, and stored as content-addressed immutable artifacts.
2. Headless mutation and candidate execution use restricted container interfaces; evaluator code and private inputs remain trusted-side.
3. Result files are checked against the exact job/candidate identity, schema, metric allow-list, and bounded public-feedback contract before publication.
4. Local SQLite job state records launch intent and supports restart reconciliation, acknowledgement, cancellation, and cleanup.
5. Configuration and CLI wiring require an explicit evaluation mode and validate pinned images, evaluator setup, auth profiles, credential environment names, network policy, and resource limits.
6. Each proposal chain has a durable private Headless session home. Persisted session metadata contains only opaque proposal/session identifiers, a home key, schema version, and creation time; credentials are runtime-only.
7. Trusted-local `repo_path` evaluation remains available and is explicitly labeled public/cooperative compatibility behavior.

## Automated Evidence

Focused secure-runtime, Headless, CLI, recovery, and existing compatibility tests use fake agents and fake container runners: `137 passed, 1 skipped`. The full non-integration Python suite completed with `821 passed, 2 deselected` on 2026-07-21.

The Docker qualification test was skipped because no Docker executable or pre-existing pinned qualification image was available. No test triggered a download, credential setup, or external provider call.

## Benchmark Branches

The paper/open-problem catalog and Stockfish NNUE work are not present on this branch and were not run. Their clean split/rebase sequence is documented in [Benchmark Branch Cleanup Plan](benchmark_branch_cleanup_plan.md).

## Required Manual Follow-Up

1. Build, publish, and pin the universal Headless image by immutable digest.
2. Configure operator credentials/auth profiles without placing secrets in proposal metadata or candidate artifacts.
3. Run a deliberately bounded real-agent canary against a public evaluator.
4. Review, rebase, and run the paper/open-problem and NNUE benchmark experiments separately.

Until those gates are complete, describe the feature as implemented and automatically tested, not operationally qualified.
