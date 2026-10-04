# Private NNUE search pilot — September 30, 2026

The authorized four-proposal pilot completed at **20:35 UTC September 30**.
Independent finalist validation completed at **00:57 UTC October 1** and confirmed
2.26% faster fixed searches on fresh synthetic positions. All controls, exact
checks and sanitizers passed. The workers and monitoring were cleaned up.
No representative production-corpus or Elo improvement has been established.
See [the validation workflow](../examples/stockfish_nnue/VALIDATION.md) for next steps.
Application code is on `codex/stockfish-inference-aws`. The initial private-search
implementation is commit `1aedbc8`; the deployed recording fix is **`2bd9b98`**.
The universal manifest recording fix is **`b3ff0d7`**. Fork main remains `340bc74`.

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

Local validation: **901 passed, 3 skipped**, including the fake Headless CLI
end-to-end test, plus clean Ruff and diff checks.
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
| Compact sequential ABBA, 128 blocks | Primary | 0.998752 | 0.997638–0.999867 | Passed equivalence |
| Compact sequential ABBA, 128 blocks | Replication | 1.000402 | 0.999187–1.001618 | Passed equivalence |

Neither 24-block campaign qualified. The initial primary mild control was cancelled after
the replication failed; concurrent evidence archival also invalidated that
partial timing. It cannot be used as a performance observation.

The complete compact 128-block campaign qualified. The mild and gross slowdown
controls measured 0.909237× and 0.229725× geometric speedup, respectively, and the
wrong-output control received zero fitness. Correct controls each matched
162,048 exact values. All fixed-budget samples were retained. The mild control
is roughly a 9% throughput reduction; it does not directly validate detection of
tiny gains. A/A equivalence permits small residual offsets, so independent
holdouts remain necessary before claiming a candidate speed improvement.

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

The new `campaign-compact128` started at **11:42 UTC**. Both workers passed the
162,048-value exact checker and entered timing with the compact format; their
first live checkpoints were about 13 KB. Both A/A qualifications and the remaining
controls subsequently passed.
The campaign uses **128 process blocks**, with all correctness, minimum
duration, standard-error and equivalence gates unchanged. The work is frozen at
eight passes through twelve private positions per request, depth 13, one search
thread and 16 MiB hash. It uses the same pinned Graviton compiler/runtime image.

The larger budget is intended to estimate across-process variation more precisely.
It is not a successful rerun selected from repeated attempts: the old failures
remain evidence, the sample count was fixed before collecting new data, and there
is no interim significance stopping. This campaign passed its declared gates;
the effect of the checkpoint fix on previous noise is not isolated by this result.

Both preselected workers run A/A first. After both pass, the primary runs mild
and gross deliberate slowdowns and an incorrect-output control. The remaining
controls passed before the campaign was frozen. Replication was transferred
only while the primary is between measurement jobs. Nothing builds, mutates,
archives large trees or copies large evidence files during scored timing.

The guarded mini-run requests a measured seed and **four proposal generations**,
using the exact requested model route, one island and one proposal/evaluation
slot. Failed proposals can leave fewer measured individuals. It has no auxiliary
model calls and a 20-minute proposal timeout. The larger timing budget means this
is now a multi-hour pilot. Do not change a frozen evaluator to make it finish sooner.

## Startup failure and bounded continuation

At 15:50 UTC, after qualification and a fresh successful secure provider canary,
the launcher failed while recording framework provenance. On the installed
worker, `git rev-parse` returned an error that the manifest writer treated as a
directory path. The failed invocation is diagnostic: no proposal, program or
evaluation job had been created. Its logs and databases are preserved alongside
the complete controls in two downloaded, SHA-256-verified archives.

Commit `b3ff0d7` fixes this universal metadata problem and adds regression tests
for non-Git installations, unavailable commands, timeouts, clean checkouts and
complete dirty-state hashing. The replacement worker wheel differs only in
`shinka/run_manifest.py` and its wheel RECORD. Its SHA-256 is
`b2fa889397d12c4b6f73c7d327babf1e9a6d702ef986dce727b47548ec61d84b`.
The installed-worker smoke test passed. The private evaluation identity was
verified unchanged before and after installation:
`52f6f05ef9694d5301e951335275dfdd13ac30e6ee1b6bfe25d348bfbca20a84`.

At 17:54 UTC the same results directory continued with the identical config and
five total proposal IDs. `workflow-mini-recovery.json` records its completion at
20:35 UTC. Both original controllers are retained as evidence and must not be
rerun. This continuation does not authorize another evolution batch.

## Pilot outcomes and independent validation

| Generation | Outcome | Geometric speedup | Lower-bound fitness |
| --- | --- | ---: | ---: |
| 0 | Correct seed | 1.000758 | 0.999564 |
| 1 | Build failed | — | None |
| 2 | Proposal timed out after 20 minutes | — | None |
| 3 | Correct candidate | 1.023302 | 1.022015 |
| 4 | Agent process failed | — | None |

The fixed four-proposal budget ended with two measured programs and three failed
attempts; no extra proposals replaced failures. Both measured programs passed
162,048 exact comparisons and all private fixed-search fingerprints over all 128
process blocks. Generation 3's 2.33% geometric speedup has a one-sided 95% lower
bound of 2.20%. These observations apply to the pilot corpus and pinned Graviton
environment. Recorded model cost is $16.755375; missing usage on failed routes
means this is not necessarily the complete provider bill.

The reviewed candidate changes exactly three NNUE files: ARM lane SDOT instead
of broadcasted inputs, one sparse-layer accumulator bank instead of three, and
empty GCC/NEON assembly constraints that materialize weight-column pointers.
All other source files are byte-identical. No weights, search, test, timing,
compiler flags or interfaces changed. The
[saved patch](../examples/stockfish_nnue/candidates/pilot_gen3_neon.patch) applies
to the frozen seed and reconstructs all 80 candidate files exactly. Candidate
digest: `sha256:6974a34e351f55d2d4bb9bc15abe34df9f5608a320a9c969be265c34aaa76fe6`.
The complete run archive, including failed attempts, was downloaded and verified
before finalist validation.

The separate `campaign-finalist-holdout128` is **validation only**. Its predeclared
corpus seed 2026100101 produces 24 traces and twelve private search positions,
verified disjoint from the pilot positions. It uses the same evaluator code,
128-block budget and gates, and the candidate artifact is fixed. Reference work
calibration chose eight passes with a 2.106-second probe. These synthetic positions
provide an independent pilot holdout, not a representative production benchmark.

Worker 1 runs candidate ASan/UBSan, then independent A/A. Worker 2 runs all four
controls. The collector transfers replication only after worker 2 finishes timing
and waits between jobs. Only if both workers qualify and sanitizers pass does
worker 2 freeze this validation campaign and evaluate the selected candidate
once. Failed controls or measurements are retained; none may be repeated until
favorable. No additional mutation batch runs. Validation identity:
`046976f9b1bd8277b73a1b5c1110a2e20e512d17e5fd1371868f15539273a7f8`.

At 21:02 UTC, the selected candidate passed **ASan/UBSan, 82,016 exact values,
and twelve depth-11 searches** on the fresh fixtures. Both holdout A/A jobs passed
163,824 release exact comparisons and entered their full timing budgets. Holdout
qualification subsequently passed. The fixed candidate was evaluated once,
matching outputs and measuring geometric speedup **1.0226464126**, lower-bound
score **1.0217343891**, over all 128 blocks. Independent A/A measured
**0.9999890172**. This completes the synthetic holdout validation.

The preserved generation-1 build diagnostic contains only a generic build error;
generation 4 likewise lacks the underlying agent-process cause. Better private
stderr retention was implemented in the October 3 tooling update; it preserves
private compiler streams and redacted provider artifacts for future failures.
It cannot recover the missing historical causes. Neither failure received
fitness or was replaced by an extra proposal.

## AWS and monitoring

The two workers were Spot `c8g.2xlarge` instances in `us-east-1f`:
`i-0fada3380b3a3bd93` and `i-0f99a358acd33e8db`. No GPU is needed for this exact
inference optimization. Controllers ran as UID 65532 outside measurement core 2.
Each runtime container was restricted to core 2, with network disabled and a
4 GiB memory limit. Each instance had a scheduled termination at
**05:47 UTC October 1**, extended from 22:10 UTC September 30 to finish the
authorized continuation. Both were explicitly terminated at **01:13:32 UTC
October 1**, after evidence collection. Temporary access resources were deleted.

Bare-metal Spot requests failed on capacity/quota. On-demand bare metal exceeded
the 32-vCPU quota. EC2 also rejected small dedicated-tenancy instances, requiring
a Standard On-Demand quota of at least 257 vCPUs. `servicequotas:RequestServiceQuotaIncrease`
is denied for the current IAM user. An optional user request to raise
`L-1216C47A` in `us-east-1` is pending. No metal or dedicated instance was created.
AWS documents that dedicated tenancy isolates hardware from other accounts;
Dedicated Spot requires the largest or metal size for the family.
([Dedicated instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/dedicated-instance.html),
[Dedicated Spot restrictions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-spot-instances-work.html))

Monitoring is finished: the original heartbeat was replaced for finalist work,
and its replacement was deleted after closeout. Historical operator entry points are:

```text
examples/stockfish_nnue/.work/aws-search-mini-20260930/state.json
examples/stockfish_nnue/.work/aws-search-mini-20260930/OPERATOR.md
/home/ec2-user/shinka/finalist-replication.json       # worker 1
/home/ec2-user/shinka/finalist-replication.py.log     # worker 1
/home/ec2-user/shinka/finalist-validation.json        # worker 2
/home/ec2-user/shinka/finalist-validation.py.log      # worker 2
```

Full evidence was checksum-verified and archived to S3 before cleanup. The
compact final report is retained under the ignored operator directory at
`finalist-holdout-final-evidence/REPORT.md`. Further claims require representative
workloads, portability/release-build checks and additional independent machines.
