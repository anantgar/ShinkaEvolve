# Real-game NNUE candidate validation — October 3, 2026

Status snapshot at 04:44 UTC on October 4: correctness and ARM timing are complete;
final Intel timing is running.
Live results are written to `.work/aws-validation-20261003/RESULTS.md` after archives
arrive. This dated launch record does not assert the run remains pending forever.
The measurements below support speedups within the declared ARM workload.
They do not establish general workload performance, Elo or fitness admission.
No upstream submission has been made.
The Shinka tree was clean at `8eab511` before starting. Local work is limited to
orchestration, packaging and receipts; compilation and engine tests run on AWS.

Candidate `233068f32740aa08433bad2c4af89416e9b65bb4` changes only three NNUE files
against Stockfish `49ea5ded38315cff8e67f4a677a9e7811612fbf6`. Network:
`nn-252f33942263.nnue`, SHA-256
`252f33942263bc8b8f740ba8aec3fed5a159ff148113c47a55c18c33d6627ab3`.

The same seeded sample of 1,000 distinct legal games is selected from
[Lichess September 2025 broadcasts](https://database.lichess.org/broadcast/lichess_db_broadcast_2025-09.pgn.zst).
Compressed SHA-256:
`da3a261e926a2c0f5e79c393818e643d76448edf94cfdaefa3892258a6f9e7ba`.
The parser counts 28,536 games: 25,340 eligible distinct games, 3,069 rejected
(malformed/unsupported/short), and 127 duplicates. Source license: CC BY-SA 4.0,
with attribution to Lichess broadcasts. Curated PGN SHA-256:
`28da73d91d5981d45604045b895f3c2994ace11caab4c42091d370d33fbfeca0`.
Game-disjoint splits and public special-move traces produce 1,363 replay segments.
Finalist search corpus SHA-256:
`8f89e554f1a1e2003dd9518a2661f4d5e6613c6947a1ea26084de0bf8b00f2a6`.

## Completed ARM measurements

| CPU | Candidate speedup | 95% interval | A/A control | Exact search outputs |
|---|---:|---:|---|---|
| Graviton 3 | +1.760% | +1.663% to +1.857% | accepted | matched |
| Graviton 4 | +1.651% | +1.563% to +1.738% | accepted | matched |

Each row uses 128 complete blocks on twelve held-out positions with production
GCC PGO/LTO builds. Minimum measured samples were 1.939 and 2.065 seconds;
log standard errors were 0.000481 and 0.000435. The A/A 95% intervals were
[0.998926, 1.001047] and [0.999133, 1.000421], both inside the declared noise
bound. Every warmup and measured search matched scores, PV, best/ponder moves,
nodes and fingerprints. This is one boot per CPU family with one network and
compiler performance configuration; broader workload and repeat-boot coverage
remain open. Neither result is a qualified Shinka fitness or Elo claim.

The ARM evidence archives are encrypted in S3, downloaded locally as small
receipts, and SHA-256 checked. Both ARM workers are terminated. Intel correctness
passed, but its final matched A/A and candidate timing is still running. The
collector retains that result, terminates the last owned worker and removes the
shared SSH key/security group when complete.

## Declared checks

- Release exact NNUE streams: three GCC SIMD rounds, one Clang round, and one
  plain-NEON round on ARM or scalar round on Intel. Raw/final, fresh/incremental,
  eager/lazy and branching paths must match integer for integer.
- ASan/UBSan with recovery disabled: all 12 public special traces plus a seeded
  sample of 64 real-game segments. These checks have narrower coverage than the
  full release corpus; they do not establish universal equivalence.
- Production GCC PGO/LTO binaries: matching default bench signature, separate
  CPU-clock search profiles, and exact fixed-depth search fingerprints.
- Timing: 12 held-out positions, one thread, 16 MiB hash, engine CPU 2. Baseline-only
  calibration tries depths 9/11/13/15/17 and freezes 1–16 repetitions targeting two
  seconds. Each worker runs 128 fresh-process balanced A/A blocks, then 128 candidate
  blocks, without sample trimming or favorable retries. Startup is excluded.

Graviton 3 (`c7g.xlarge`), Graviton 4 (`c8g.xlarge`) and Intel (`c7i.xlarge`) use
one-time Spot, price cap $0.15/hour each, encrypted auto-deleted disks, operator-IP
SSH, IMDSv2, and no IAM role. Approximately six-hour launch deadlines cap total
compute at about $2.70 plus storage. Evidence uploads and worker termination run
automatically, including Spot interruption handling. Unrelated instances untouched.

Intel completed all 128 A/A blocks: accepted, ratio 1.0000527, 95% interval
[0.9982616, 1.0018470], minimum sample 2.126 seconds. Its candidate timing did not
start: repeated in-process use of the driver retained the excluded engine CPU in
controller affinity, and the next call failed preflight. The worker preserved that
operator failure and terminated. That attempt has no Intel speed comparison; its correctness, compiler, sanitizer
and benchmark evidence remains valid. A final bounded Intel Spot worker reuses
those hash-verified production executables and the same corpus, repeats the bench,
and runs a fresh matched 128-block A/A and 128-block candidate comparison through
separate CLI processes. The prior result and failure remain archived. This is a
new boot, so its earlier correctness preflight is reused evidence, not a fresh run.

The driver now restores the caller's affinity on success and errors; nine small
Python tests passed. Frozen AWS protocol files are unchanged. ARM workers retain
their original A/A runs and execute the candidate phase through that frozen CLI in
a fresh process after A/A exits, with the same declared depths/passes and no retry.
The private receipts preserve both the caller failure and this continuation plan.

The operator driver never admits fitness or asserts a general speed claim.
New mild/gross control qualification, production peak-memory checks, additional
networks, Clang search-speed tests and Elo games are outside this run. Intel uses
SMT; exclusion of the engine logical CPU does not reserve its whole physical core.
Compare baseline/candidate within a worker, not absolute NPS between CPU models.

## Confirmed preflight and live completion

All three hardware preflights passed **22,584,752 integer comparisons each**, including
224,296 ASan/UBSan comparisons. All baseline/candidate production benches matched
**1,714,434 nodes**; separate CPU-clock search profiles were captured. Frozen
search settings: Graviton 3 depth 11 × 10 passes, Graviton 4 depth 11 × 14 passes,
Intel depth 13 × 7 passes. All three began their A/A controls successfully.

A bounded, one-shot local collector checks AWS state and receipt availability, downloads
small evidence archives, writes `RESULTS.md`, and removes the owned key/security
group after termination. It launches no new jobs, runs no engines locally, does
not download production executables, and sends no notifications. Its six-hour
collector deadline is separate from the worker launch cutoffs. If local execution
is interrupted, remote evidence preservation and termination still run.

The slow ARM debug sanitizer check was profiled: 88.55% of a three-second CPU-clock
sample was in ASan's fake-stack allocator. An O1 rebuild was considered, consistent
with [sanitizer guidance](https://clang.llvm.org/docs/AddressSanitizer.html#usage),
but both checks completed and timing began before it could start. The pre-timing
guard blocked that restart; **no O1 rebuild or timing protocol change occurred**.
Use O1 for a separately declared future sanitizer protocol, retaining checks.

## Preparation and infrastructure history

The first PGN export reused a stateful exporter and repeated earlier games. It was
stopped before any build/test observation; logs and inputs were preserved. The
fix uses a new exporter per game and checks sample size and unique game count.
Intel's first Spot worker was reclaimed for capacity before candidate timing; its
partial remote artifacts could not be recovered. A replacement uses another zone.
The generic scalar GCC baseline target rejected x86-only `-m64` on ARM. Scalar
coverage moved to Intel; ARM retains ordinary NEON coverage. The first Graviton 4
attempt preserved 22,360,456 passing comparisons and that baseline failure before
terminating. Its replacement is a separate attempt. Pre-timing protocol revisions
added a bounded sanitizer sample and duration calibration; no scored observations
existed before these revisions. Original inputs and failures remain archived.

## Evidence and submission

Operator files and receipts are under `.work/aws-validation-20261003/`.
Encrypted private archives are in bucket `circle-packing-runs-406108502165-us-east-1`,
prefix `stockfish-nnue-validation/2026-10-03/`; every attempt has a distinct key.
`PLAN.md`, immutable input digests, compiler/build logs, exact-check receipts,
profile reports, timing plans, partial checkpoints and rejected results are retained.
Production executables are archived separately and need not be downloaded locally.

[Validation and submission procedure](VALIDATION.md) covers the standalone
Stockfish branch and evidence required for review. Fishtest is distributed across
[CPU contributors](https://raw.githubusercontent.com/official-stockfish/fishtest/master/README.md).
[Stockfish guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups)
generally recommends game tests for speedups but allows small, independently
verifiable optimizations to go directly to a PR. Exact-output checks and benchmarks
come first; maintainers can decide whether this combined ARM patch needs an
architecture-filtered game test. No public test, push or PR has been created.
