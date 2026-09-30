# Private NNUE search pilot — September 30, 2026

Status at 11:34 UTC: the system is implemented; qualification is being restarted
after correcting checkpoint storage.
No evolutionary candidate or Stockfish speed improvement has been claimed.
Application code is on `codex/stockfish-inference-aws`. The initial private-search
implementation is commit `1aedbc8`; this report also covers its recording fix.
Fork main remains `340bc74`.

## Contract and verification

- Only the existing NNUE implementation allowlist can change. Weights, feature
  definitions, quantization, search, build flags and the evaluator remain fixed.
- The agent receives surrounding engine source and the read-only network. Test
  code, fixtures, benchmark sources, original Git history, evaluator snapshots,
  reference executable and operator state are absent from its container.
- The builder links the engine and exact checker from the same NNUE object files.
  The checker validates fresh, incremental, lazy, undo and sibling paths before
  timing. Private fixed-depth searches must also match scores, PV, moves, nodes
  and canonical checksums. Speed evaluation never plays matches.
- Timing belongs to the external controller. UCI time and NPS are ignored.
  Four fresh processes form each ABBA/BAAB block. Each process has a warmup and
  a measured request; only one engine is resident at a time. The statistical
  unit is the independent process block, not each repeated search.
- Fitness is the one-sided 95% lower confidence bound on geometric speedup.
  Wrong output gets zero fitness; excessive noise or infrastructure failure
  produces no fitness. All samples and failures are retained.

Local validation: **895 passed, 3 skipped**, plus clean Ruff and diff checks.
Native seed ASan/UBSan verification passed **81,124 exact values** and **12 fixed
depth-11 searches**. Release A/A controls each checked **162,048 exact values**.

The exact route `headless/codex@gpt-6-astra?effort=high` passed a real edit/resume
canary in the isolated AWS agent container. Its versions are Headless 0.5.0,
Codex CLI 0.146.0 and Node 22.23.2. Minimal auth lives outside the project and was
removed from the durable canary session. Runtime containers have no network or
provider credentials; agent network access is restricted to provider endpoints.

## Completed timing controls

A/A compares byte-identical baseline and candidate binaries. Qualification
requires the entire two-sided 95% interval to fit inside ±0.003 log speedup.

| Protocol | Host | Geometric speedup | 95% interval | Outcome |
| --- | --- | ---: | --- | --- |
| Paired, 24 blocks | Primary | 0.998783 | 0.997403–1.000164 | A/A passed |
| Paired, 24 blocks | Replication | 0.997467 | 0.993904–1.001043 | Failed equivalence |
| Sequential ABBA, 24 blocks | Primary | 1.003153 | 1.000147–1.006168 | Failed equivalence |
| Sequential ABBA, 24 blocks | Replication | 0.999698 | 0.996490–1.002917 | Failed equivalence |

Neither campaign qualified. The initial primary mild control was cancelled after
the replication failed; concurrent evidence archival also invalidated that
partial timing. It cannot be used as a performance observation.

A diagnostic with GC callbacks measured pauses no longer than **1.34 ms**.
The largest slow requests had no overlapping GC. A separate GC-disabled host
still showed timing excursions. This rules out GC as the explanation for the
large spikes; the two hosts are not a controlled estimate of GC's speed impact.

The completed sequential controls, work calibration, sanitizer result and GC
diagnostics are preserved in two locally downloaded archives with verified SHA-256
hashes. Exact fixtures and raw outputs remain in the ignored operator directory.

## Current fixed sampling plan

`campaign-precision128` was declared at 11:10 UTC and started at 11:13 UTC.
It was cancelled at 11:19 UTC after a source audit found quadratic raw-checkpoint
growth: every observation caused all earlier raw outputs to be serialized again.
That recording traffic could interfere with later samples. Its contribution to
the measured noise remains unproven. The partial 13- and 14-block records are
archived, SHA-256 verified and excluded from fitness.

The corrected recorder stores distinct compressed raw outputs with SHA-256
checks and compact references. It avoids bulk writes between warmup and timing.
Each checkpoint remains self-contained for AWS recovery; failed warmups are
retained by the exception handler. Full final diagnostics keep the raw format.
Regression tests cover recovery, corruption and timing boundaries. A synthetic
128-block stream writes **54,030,295 bytes across 640 snapshots**, with a largest
snapshot of **160,171 bytes**. An actual interrupted checkpoint shrank from
**3,726,884 to 40,273 bytes** and recovered every raw value exactly.

The new `campaign-compact128` uses **128 process blocks**, with all correctness, minimum
duration, standard-error and equivalence gates unchanged. The work is frozen at
eight passes through twelve private positions per request, depth 13, one search
thread and 16 MiB hash. It uses the same pinned Graviton compiler/runtime image.

The larger budget is intended to estimate across-process variation more precisely.
It is not a successful rerun selected from repeated attempts: the old failures
remain evidence, the sample count was fixed before collecting new data, and there
is no interim significance stopping. Whether it qualifies remains unknown.

Both preselected workers run A/A first. After both pass, the primary runs mild
and gross deliberate slowdowns and an incorrect-output control. The remaining
controls must pass before the campaign is frozen. Replication is transferred
only while the primary is between measurement jobs. Nothing builds, mutates,
archives large trees or copies large evidence files during scored timing.

The guarded mini-run requests a measured seed and **four proposal generations**,
using the exact requested model route, one island and one proposal/evaluation
slot. Failed proposals can leave fewer measured individuals. It has no auxiliary
model calls and a 20-minute proposal timeout. The larger timing budget means this
is now a multi-hour pilot. Do not change a frozen evaluator to make it finish sooner.

## AWS and monitoring

The two live workers are Spot `c8g.2xlarge` instances in `us-east-1f`:
`i-0fada3380b3a3bd93` and `i-0f99a358acd33e8db`. No GPU is needed for this exact
inference optimization. Controllers run as UID 65532 outside measurement core 2.
Each runtime container is restricted to core 2, with network disabled and a
4 GiB memory limit. Each instance terminates on its scheduled shutdown at
**22:10 UTC** unless active authorized work requires a deliberate extension.

Bare-metal Spot requests failed on capacity/quota. On-demand bare metal exceeded
the 32-vCPU quota. EC2 also rejected small dedicated-tenancy instances, requiring
a Standard On-Demand quota of at least 257 vCPUs. `servicequotas:RequestServiceQuotaIncrease`
is denied for the current IAM user. An optional user request to raise
`L-1216C47A` in `us-east-1` is pending. No metal or dedicated instance was created.
AWS documents that dedicated tenancy isolates hardware from other accounts;
Dedicated Spot requires the largest or metal size for the family.
([Dedicated instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/dedicated-instance.html),
[Dedicated Spot restrictions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-spot-instances-work.html))

The active heartbeat is `monitor-stockfish-four-generation-pilot`, every ten
minutes, quiet unless there is a meaningful change or required action. The
operator entry points are:

```text
examples/stockfish_nnue/.work/aws-search-mini-20260930/state.json
examples/stockfish_nnue/.work/aws-search-mini-20260930/OPERATOR.md
/home/ec2-user/shinka/workflow-compact128.json
/home/ec2-user/shinka/workflow-compact128.log
```

Before cleanup, preserve the full control and candidate evidence, verify archive
hashes, and exclude credentials and durable provider homes. Terminate only the
two owned workers, confirm termination, remove their temporary SSH key pair and
security group, record cleanup, then stop the heartbeat. A promising candidate
still needs source review, sanitizers and fresh independent holdout measurements.
