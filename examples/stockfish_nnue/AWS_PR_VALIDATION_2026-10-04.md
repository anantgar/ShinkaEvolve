# Stockfish PR validation — October 4, 2026

Status: three native AWS Spot workers launched; builds and preflight checks precede
timing. No completed result is claimed by this launch record.

PR [#7208](https://github.com/official-stockfish/Stockfish/pull/7208) now has a single
commit, `97d81e88a303c7fa4fce945026022d2a5585218d`, including AUTHORS. clang-format
20.1.8 fixed one continuation indent. The new Actions formatting step passed and
removed its earlier comment. The old job's green overall status concealed a failed
step because the workflow uses `continue-on-error`; inspect step logs, not only
job conclusions. Ignoring whitespace, the engine source matches the original
validated combined patch `233068f32740aa08433bad2c4af89416e9b65bb4`.

Base: `49ea5ded38315cff8e67f4a677a9e7811612fbf6`, current upstream master on launch.
Network: `nn-252f33942263.nnue`, SHA-256
`252f33942263bc8b8f740ba8aec3fed5a159ff148113c47a55c18c33d6627ab3`.
Frozen corpus SHA-256:
`8cf513a99de0497ea1c64742091da3ce18f966db47488bc0d8c176b44fff7965`.

## Declared protocol

- Fresh c7g.xlarge, c8g.xlarge and c7i.xlarge boots. Initial ten-hour shutdown backstop,
  one-time Spot capped at $0.15/hour per worker. After observing A/A wall-clock
  throughput (before any candidate timing), the infrastructure cutoff was extended
  to fourteen hours to avoid truncating the final groups: maximum $6.30 compute
  plus storage. Sample budgets, inputs and acceptance rules are unchanged; the
  deadline-extension receipt is archived on every worker.
  Encrypted auto-deleted volumes, IMDSv2, no IAM role, operator-IP SSH, automatic
  private archive upload and termination. Local work is orchestration, formatting
  and lightweight inspection; engines and compilation stay on AWS.
- Forty-eight additional nonterminal positions from the same frozen 1,000-game
  Lichess broadcast sample: sixteen each from early/middle/late trace fractions.
  Traces shuffled with seed 20261004; original twelve timed positions excluded.
  Selection is fixed before measurements. These are new timing positions from
  an already checked game corpus, not an untouched independent game source.
- Build GCC PGO/LTO dot-product baseline and five fixed variants on ARM: lane-only,
  bank-only, pointer-only, lane+bank, combined. Clang dot-product and GCC ordinary
  NEON each have separate PGO/LTO baseline/combined pairs. Intel checks GCC AVX2
  baseline/combined. Every production bench must remain 1,714,434 nodes.
- Exact replay includes all public special traces plus the forty-eight expanded
  traces. GCC variants run three rounds; Clang/plain NEON run one. Combined
  ASan/UBSan uses O1, debug assertions and disabled recovery. Preserve the earlier
  full-corpus correctness results and narrower scope of these new combinations.
- Store generated assembly, compiler/build logs and executable hashes before
  timing. Assembly review will distinguish broadcasts, dot-product lanes,
  accumulator chains and pointer/load scheduling; static instruction counts alone
  do not establish speed.
- Calibrate all depths/passes using only baselines before any candidate timing.
  Main depth starts at 11; a deeper group starts two plies higher. Increase by two
  only if needed for sample duration. Target 1.5 seconds, maximum sixteen passes,
  frozen before observations. One thread, 16 MiB hash, engine CPU 2, fresh sequential
  ABBA/BAAB processes, warmups, fixed 128-block comparisons, no trimming or favorable
  retries. Matching A/A controls for each compiler/ISA/depth. Intel excludes both
  engine-core siblings from the controller affinity.
- On ARM: full GCC comparison at both depths, each component and lane+bank at the
  main depth, and full Clang/plain-NEON comparisons. On Intel: both depth groups.
  Retain rejected controls and partial attempts without reporting qualified gains.

Private operator input manifests, scripts and receipts are under
`.work/aws-pr-validation-20261004/`; encrypted evidence archives use the S3 prefix
`stockfish-nnue-validation/2026-10-04-pr/`. Upload authorization stays outside
archived evidence. The completed October 3 evidence remains unchanged.

The final patch scope and PR body will follow the completed evidence and Stockfish's
request for small, distinct ideas. No Fishtest submission or Elo result is claimed.
