# Controlled pointer validation — October 5, 2026

All planned checks are complete for [draft PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208),
commit `830c5c3` against `49ea5ded`. Fishtest is deferred by the user.

## Results

GCC dot-product gains are positive at both depths on both Graviton families and
independent boots. Graviton3 deeper gains vary across boots; no pooled estimate
is substituted. Ordinary NEON also passes. Intel and Clang timing intervals are
inside the predefined ±0.3% equivalence band. All **15 completed A/A controls** pass.

| Hardware | Compiler / ISA | Boot | Depth | Gain | 95% interval |
|---|---|---|---:|---:|---:|
| Graviton3 | GCC / dotprod | primary | 11 | [+0.835%](submission-evidence/2026-10-05-common-profile/c7g-gcc-pointer.json) | +0.761% to +0.909% |
| Graviton3 | GCC / dotprod | primary | 13 | [+0.905%](submission-evidence/2026-10-05-common-profile/c7g-gcc-deeper-pointer.json) | +0.841% to +0.969% |
| Graviton3 | GCC / dotprod | repeat | 11 | [+0.918%](submission-evidence/2026-10-05-pointer-repeat/c7g-gcc-pointer.json) | +0.843% to +0.994% |
| Graviton3 | GCC / dotprod | repeat | 13 | [+1.108%](submission-evidence/2026-10-05-pointer-repeat/c7g-gcc-deeper-pointer.json) | +1.048% to +1.167% |
| Graviton3 | GCC / NEON | NEON completion | 11 | [+0.676%](submission-evidence/2026-10-05-neon-completion/c7g-neon-pointer.json) | +0.620% to +0.733% |
| Graviton3 | Clang / dotprod | primary | 11 | [-0.039%](submission-evidence/2026-10-05-common-profile/c7g-clang-pointer.json) | -0.090% to +0.012% |
| Graviton4 | GCC / dotprod | primary | 11 | [+0.484%](submission-evidence/2026-10-05-common-profile/c8g-gcc-pointer.json) | +0.449% to +0.518% |
| Graviton4 | GCC / dotprod | primary | 13 | [+0.546%](submission-evidence/2026-10-05-common-profile/c8g-gcc-deeper-pointer.json) | +0.457% to +0.636% |
| Graviton4 | GCC / dotprod | repeat | 11 | [+0.518%](submission-evidence/2026-10-05-pointer-repeat/c8g-gcc-pointer.json) | +0.478% to +0.557% |
| Graviton4 | GCC / dotprod | repeat | 13 | [+0.529%](submission-evidence/2026-10-05-pointer-repeat/c8g-gcc-deeper-pointer.json) | +0.486% to +0.571% |
| Graviton4 | GCC / NEON | NEON completion | 11 | [+0.621%](submission-evidence/2026-10-05-neon-g4-completion/c8g-neon-pointer.json) | +0.560% to +0.682% |
| Graviton4 | Clang / dotprod | primary | 11 | [+0.013%](submission-evidence/2026-10-05-common-profile/c8g-clang-pointer.json) | -0.032% to +0.058% |
| Intel | GCC / AVX2 | primary | 11 | [-0.042%](submission-evidence/2026-10-05-common-profile/c7i-gcc-pointer.json) | -0.170% to +0.086% |
| Intel | GCC / AVX2 | primary | 13 | [-0.012%](submission-evidence/2026-10-05-common-profile/c7i-gcc-deeper-pointer.json) | -0.130% to +0.107% |

## Method and scope

Each pair uses the same immutable, baseline-generated PGO profile, source path,
compiler and version macros. Raw profile hashes are checked; missing profiles or
CFG/counter-count mismatches stop validation. Only recorded source-line warnings
are permitted under the [GCC policy](submission-evidence/2026-10-05-common-profile/profile-policy.md).
Intel executables are byte-identical; Clang full disassemblies are identical.

The fixed corpus contains 48 positions sampled across three game phases from
games already used for correctness replay. Each comparison uses 128 fresh-process
ABBA/BAAB blocks, 256 paired rounds, warmups, pinned engine cores and baseline-only
duration calibration. Budgets and rejection rules are unchanged; no sample trimming.
Plans, exact patches, workers, calibration and receipts are retained in the
[primary](submission-evidence/2026-10-05-common-profile/),
[repeat](submission-evidence/2026-10-05-pointer-repeat/),
[G3 NEON](submission-evidence/2026-10-05-neon-completion/) and
[G4 NEON](submission-evidence/2026-10-05-neon-g4-completion/) records.

Every production bench is **1714434**. Exact pointer replay, ordinary NEON/Clang
replay and ASan/UBSan pass. clang-format 20 and
[full fork CI](https://github.com/anantgar/Stockfish/actions/runs/37245204905)
pass (55 jobs successful; four publication jobs skipped). [Assembly evidence](submission-evidence/2026-10-05-common-profile/accumulator-assembly.json)
records paired-load changes as static counts, without inferring dynamic load counts.

These are fixed-corpus, fixed-network, common-profile speed measurements, not Elo,
independently retrained release-PGO gains or evolution worker-pool qualification.
October 3/4 speed claims remain withdrawn because version metadata differed.
Equal metadata initially exposed separate-PGO differences on x86; that attempt
stopped before timing. Historical receipts remain in the audit records.

## Interrupted attempts and cleanup

The primary G3 NEON control stopped at 103/128 blocks; its cause is unrecorded.
The [G4 journal](submission-evidence/2026-10-05-common-profile/c8g-service-journal.txt)
records a worker stop/restart during NEON candidate timing at 9/128, followed by
failure because the source directory existed. Its initiator is unrecorded.
Neither partial comparison yields an estimate. Bounded fresh boots complete only
NEON with the same rules; the prior accepted G4 A/A control is retained too.

All seven primary/repeat/completion workers are terminated, encrypted archives
collected and hash-verified, and temporary keys/security groups/private keys
removed. Failed earlier cohorts are also archived and cleaned. Fishtest remains
a future maintainer/account-holder test or an explicit maintainer exception;
no game result or exemption is claimed.
