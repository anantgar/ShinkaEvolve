# Controlled pointer validation — October 5, 2026

Status: [x86 code identity passed](submission-evidence/2026-10-05-common-profile/x86-code-identity.json).
GCC pointer and independent-boot comparisons are complete. Clang controls pass.
Ordinary NEON remains pending. The PR remains pointer-only at `830c5c3`, based on `49ea5ded`.

## Why the builds changed

October 3/4 speed claims are withdrawn: patched trees enabled `GIT_DIFFINDEX`,
unlike baseline. Equal `GIT_SHA`, `GIT_DATE` and empty `GIT_DIFFINDEX` fixed this,
but the replacement Intel preflight still found code differences in five
functions after separate PGO training. It stopped before timing. ARM workers
were stopped and archived; their candidate timings are not substituted for a
controlled result. [The original plan](submission-evidence/2026-10-05/) is retained.

## Frozen common-profile protocol

The [plan, worker and exact PR patch](submission-evidence/2026-10-05-common-profile/)
record the replacement before timing:

- Generate one baseline PGO profile per compiler/architecture using single-thread
  `bench`. Preserve its files and SHA-256 hashes. Compile baseline and pointer
  candidate at the same source path from this profile; reject missing profiles, changed CFG/counter counts
  or changed hashes. Source-line warnings are recorded and permitted under
  the [GCC compatibility policy](submission-evidence/2026-10-05-common-profile/profile-policy.md). Independent boots generate independent profiles.
- Verify equal version macros in actual compile commands. Use normal production
  network embedding and checksum-pinned external EvalFile. Every production
  build must retain bench `1714434`. Replay builds use embedding-off flags.
- Require identical full disassembly, after only the objdump file-path header is
  removed, on excluded x86 and Clang paths before timing. Any unexplained
  difference stops the worker.
- Retain the same fixed 48 search cases, 128 fresh-process ABBA/BAAB blocks,
  warmups, core isolation, baseline-only duration calibration and rejection
  rules. Run pointer main/deeper comparisons with matching A/A controls, plus
  ordinary NEON and Clang comparisons. Rejected controls remain inconclusive;
  no favorable retries or sample trimming.
- Check exact replay and ASan/UBSan with recovery disabled before timing.

Source, corpus and network identities are pinned in the plan. Common baseline
PGO isolates the source change under one profile; it does not measure the effect
of independently retraining each release binary's profile. Historical component
comparisons remain diagnostic; the submission contains only pointer materialization.

Native builds and engines run on bounded on-demand AWS workers. Encrypted volumes
are auto-deleted; workers upload private archives and terminate. Concrete cases
and operator credentials remain private. No game test or evolution fitness
admission is included. Fishtest requires a maintainer/account holder because the
user has no account.

Baseline-only calibration produced longer deeper samples on Graviton4. The
[ARM runtime bound](submission-evidence/2026-10-05-common-profile/runtime-bound.json)
was extended to 7h45 for the worker and 7h50 for archive handling, without
restarting it. Repeat workers are bounded to 5h45 plus archive handling.
Comparison budgets, samples and statistical rejection rules are unchanged.

The [complete Graviton3 repeat-boot archive](submission-evidence/2026-10-05-pointer-repeat/completed-results.json)
measured +0.918% at depth 11 (95% interval +0.843% to +0.994%) and +1.108% at
depth 13 (+1.048% to +1.167%), with accepted matching A/A controls and verified
immutable profiles. Other comparisons remain pending; cross-boot variation
will be reported rather than replaced by one pooled estimate.

The [complete Intel receipts](submission-evidence/2026-10-05-common-profile/completed-results.json)
pass the predefined equivalence band at both depths: −0.042% at depth 11
(95% interval −0.170% to +0.086%) and −0.012% at depth 13
(−0.130% to +0.107%). Matching A/A controls pass and the paired executables
are byte-identical. The archive is collected and the owned Intel worker terminated.

The primary Graviton3 worker stopped during NEON A/A at 103/128 blocks,
before NEON candidate timing. Its six completed comparisons are archived.
The cause is not recorded; the partial control yields no estimate. A
[bounded NEON-only completion](submission-evidence/2026-10-05-neon-completion/PLAN.json)
uses unchanged corpus, sample budget and rejection rules on a new boot.
Both independent repeat archives are collected and their AWS resources cleaned.
