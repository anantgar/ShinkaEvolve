# Stockfish timing investigation — September 30, 2026

Application branch: `codex/stockfish-inference-aws`. This follows the
[failed initial qualification](stockfish_aws_canary_2026-09-30.md). All new runs
are operator diagnostics and produce **no evolutionary fitness**. The original
campaign, reference artifacts and rejection thresholds remain unchanged.

**Conclusion:** the clock is useful, but the replay's statistical model and
memory context are inadequate for small-gain selection. Large pages correct a
real mismatch without eliminating process-specific offsets. Externally timed,
fixed-work searches are a promising replacement objective: their three-host A/A
controls were much tighter. This is not yet a qualified production evaluator.

## Stockfish's documented practice

- `bench` runs search over fixed positions. Its node total is a useful functional
  fingerprint; repeated NPS measurements are also used for speed comparisons.
  Stockfish's documentation recommends at least 20 repetitions and warns that
  0.3% can be noise. Its linked `pyshbench` tool starts fresh processes for each
  run and swaps their CPU sets. Our serial, same-core comparisons avoid its
  concurrent-run contention assumptions. [Measurement guidance](https://official-stockfish.github.io/docs/stockfish-wiki/Advanced-topics.html#measure-the-speed-of-stockfish),
  [inspected pyshbench source](https://github.com/hazzl/pyshbench/blob/955f20b8ca40a4b4df984316e93bd54efbee32a1/pyshbench).
- `speedtest [threads] [hash MiB] [seconds]` is the recommended realistic hardware
  benchmark. At our pinned Stockfish commit it warms three positions, clears
  search state before measurement, suppresses normal search output and sums
  search time over game-like position sequences. It reports whole-engine NPS;
  it is not an isolated NNUE microbenchmark. [Command documentation](https://official-stockfish.github.io/docs/stockfish-wiki/UCI-Protocol-and-Stockfish-Commands.html#speedtest),
  [pinned implementation](https://github.com/official-stockfish/Stockfish/blob/0a215d6c9e48856ef630013b8ab8312941a59057/src/uci.cpp#L317).
- For speed patches, their contributor guide recommends repeated Linux `perf`
  cycle/instruction measurements, confirmation on different machines and usually
  Fishtest validation. Hardware counters and sampling help explain costs; games
  establish playing strength. [Speedup testing guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups).

## Diagnostic design

Four new Spot `c8g.2xlarge` instances in `us-east-1f` used the same AMI, compiler
image, network and prepared baseline as the first qualification. Each host ran
one experiment at a time on core 2; the trusted controller excluded that core.
The workers had three-hour automatic termination deadlines. All experiments
finished; evidence was collected before explicit termination.

The new operator CLI is `python -m examples.stockfish_nnue.diagnose`. It can run
one persistent pair or eight fresh process pairs, with six balanced measurement
rounds within each fresh pair. Both roles execute the same artifact. Startup
order is independently balanced across blocks. The diagnostic fixes incremental,
refresh and hot pass counts at 4096/2048/1024, half the first campaign's counts,
while retaining its one-second reference probe requirement and exact checks.
The lower counts are a separately recorded diagnostic design, not a change to
the failed campaign.

Raw timings, checksums, kernel cgroup CPU counters and memory maps stay in the
operator output. Kernel counters are read outside each timed request. Summary
intervals use fresh process pairs as the statistical units. They remain
approximate diagnostic intervals; they do not certify a worker pool or replace
the evaluator's fitness policy.

The first two workers ran persistent and fresh-process protocols in opposite
sequence. The third first ran 20 pairs of the upstream whole-engine `bench`, two
30-second `speedtest` runs and hardware counters, then fresh replay processes.
The fourth interleaves eight heap and eight large-page A/A blocks using one
diagnostic executable, with balanced mode order and startup order. Both allocation
modes passed 36,312 exact comparisons against the original baseline before this
comparison. Additional large-page repetitions on workers 2 and 3 and isolated
PMU measurements on worker 1 are recorded separately.

Finally, workers 2–4 each ran 20 fresh full-engine process pairs, timed by the
host controller. Every role received a separate copy/inode of the identical
executable. Startup and measurement orders were independently balanced and
randomized. Each process performed one excluded warmup before the measured
`bench 16 1 13 default depth`. Startup was excluded; command handling, search,
output transfer and completion acknowledgement were included. Both processes
were confined to core 2, while the controller used cores 0 and 1.

## What the replay controls established

1. The upstream whole-engine A/A experiment preserved node signature **1,495,562**
   in all 41 invocations (one warmup plus 20 pairs). Its geometric speed ratio
   was **0.998878**, log-SE **0.000987**; the approximate two-sided 95% interval
   includes equality. This is a control on one host, not proof of universal
   sub-percent precision.
2. Fresh replay processes reproduce and change the stable offsets. On worker 1,
   refresh ratios ranged from **0.98061 to 1.01919** across eight pairs of
   processes. Its between-block refresh log-SE was **0.003915**. Within a single
   persistent pair the aggregate log-SE was only **0.000103**. These describe
   different uncertainties; repeated requests cannot substitute for fresh
   process replication.
3. In worker 2's first fresh pair, refresh wall-time ratio was **0.986735** and
   kernel CPU-time ratio **0.986742**. The wall-minus-CPU gap was about 0.15–0.17 ms
   per request. The offset is present in actual CPU execution, not explained
   by request transport latency in that sample.
4. The harness differs from the engine's memory setup. The engine moves network
   weights into page-aligned shared storage and requests large pages; our replay
   kept an ordinary `make_unique` allocation. The controlled diagnostic changes
   only the startup allocation selected inside one executable. Linux reports
   **0 KiB** anonymous huge pages in heap mode and **112,640 KiB** in large-page
   mode. Both have identical outputs. This is a concrete representativeness
   issue. [Pinned shared allocation](https://github.com/official-stockfish/Stockfish/blob/0a215d6c9e48856ef630013b8ab8312941a59057/src/shm_unix.h#L525),
   [private large-page allocator](https://github.com/official-stockfish/Stockfish/blob/0a215d6c9e48856ef630013b8ab8312941a59057/src/memory.cpp#L153).

The allocation experiment attached `perf` to the C++ process and enabled counters
only around each replay request. Its 96 samples used two-event groups; every
reported counter ran for 100% of its enabled interval, avoiding multiplexing.
Each mode had four fresh process pairs. The following are descriptive means,
not independent candidate speedup claims:

| Workload | Heap cycles/call | Large-page cycles/call | Cycle change | Reported dTLB load-miss change |
| --- | ---: | ---: | ---: | ---: |
| Incremental | 3,516.48 | 3,405.69 | −3.15% | −93.81% |
| Refresh | 7,225.85 | 6,546.75 | −9.40% | −96.68% |
| Hot | 1,397.92 | 1,385.08 | −0.92% | −93.46% |

Instructions per call stayed essentially unchanged. These are effects of
correcting the replay's memory regime; Stockfish already requests large pages.
They are **not newly discovered Stockfish optimizations**.

Large pages did not solve A/A variation. In worker 4's interleaved comparison,
refresh's between-process log standard deviation fell from 1.659% to 0.389%,
but incremental variation increased from 0.408% to 0.710%. The additional
large-page runs included a refresh ratio of **0.968104** on worker 2 despite
both processes having 112,640 KiB of anonymous huge pages. Its eight-block mean
was 0.995890, versus 0.999582 on worker 3. None of these samples were discarded.

A further single-host probe put replay scratch objects into large-page
allocations too. Both modes passed 36,312 exact values against the original
baseline, but four fresh pairs per mode still showed incremental offsets of
roughly −0.9% to +1.3%. Its mode name `engine` denotes this allocation probe;
it does not reproduce the full search worker's layout or object lifetimes.

The evidence does not establish every remaining cause, or establish Spot as
the cause. More repetitions within one process pair cannot measure these
between-process effects. No allocation change or scoring policy has been
adopted into the production evaluator.

## Where actual Stockfish spends time

The unmodified pinned engine was sampled with `perf record` at 499 Hz during a
30-second `speedtest`. Approximately 15,000 user-cycle samples were recorded,
with zero lost samples. Flat symbol samples included:

| Function / activity | Sample share |
| --- | ---: |
| NNUE `apply_combined` accumulator updates | 33.20% |
| NNUE `NetworkArchitecture::propagate` | 13.58% |
| `MovePicker::next_move` | 9.21% |
| NNUE `update_accumulator_refresh_cache` | 7.84% |
| Non-PV search | 6.65% |
| NNUE `Network::evaluate` | 4.17% |
| NNUE `update_accumulator_incremental_both` | 2.38% |
| NNUE `update_accumulator_hybrid` | 2.09% |

The listed NNUE functions account for about 63% of sampled cycles on this
hardware/workload. This supports accumulator scheduling/locality, refresh-cache
handling and forward kernels as useful Shinka targets. It does not promise a
gain or establish the production replay weights. Sampling includes setup and
warmup; percentages are descriptive, not a universal NNUE runtime fraction.

The real engine used large pages for both anonymous allocations and shared
storage: its snapshot showed 165,888 KiB `AnonHugePages` and 110,592 KiB
`ShmemPmdMapped`. Five separate grouped cycle/instruction runs also completed
without multiplexing. Their whole-command elapsed times include Docker startup
and must not be confused with the later external search timings.

## Externally timed whole-engine controls

All 60 measured full-engine A/A pairs, plus their warmups, preserved the
**1,495,562-node** fingerprint. Each row below uses its 20 independent process
pairs as the statistical units, with a two-sided Student-t 95% interval:

| Worker | Apparent speed change | Log-SE | 95% interval for speed change |
| --- | ---: | ---: | ---: |
| 2 | +0.0595% | 0.000404 | −0.0251% to +0.1443% |
| 3 | +0.1156% | 0.000880 | −0.0685% to +0.3002% |
| 4 | +0.0923% | 0.000589 | −0.0310% to +0.2157% |

Every interval includes equality. This is encouraging for full-engine timing,
but three hosts and one short public benchmark do not prove equivalence or
sub-0.1% resolution. In particular, equality falling inside an interval is not
an equivalence test. No pooled interval treats all hosts as interchangeable.
Candidate-reported NPS was retained only for diagnosis; the numbers above use
the external controller's clock.

This suggests a useful objective: **allow changes only inside NNUE, require
exact outputs, and score time for fixed-work searches with search and weights
immutable**. It measures performance in the engine's actual cache/memory
context without needing game matches for every individual. Search coevolution
is unnecessary. Final playing-strength claims remain a separate question.

Before adopting it:

1. Build a private trusted search driver and representative hidden position
   sets. Keep readable engine context immutable and exclude the driver, fixtures
   and reference artifacts from mutation snapshots. Do not use the public
   default `bench` suite as a supposedly secret production corpus.
2. Keep externally measured completion time, exact NNUE checks, and private
   search-output/node checks. A node total alone is not proof of correctness.
   Validate the interface against malformed output and deliberate shortcuts.
3. Predeclare fresh-process blocks, host assignment, measurement order, budgets,
   minimum detectable effect and precision/acceptance gates. Estimate uncertainty
   across those blocks, not just repeated requests inside one allocation.
4. Qualify A/A, controlled slowdowns near the intended detection threshold,
   gross slowdowns, wrong outputs, timeouts and interrupted jobs. Keep every
   attempt; introduce a new campaign version instead of changing this one.
5. Run a small Shinka canary, then independent finalist checks on unused
   positions and fresh workers. Use replay and hardware counters to explain
   improvements, and separate ARM/x86 campaigns.

## Operator evidence and validation

Evidence is under `examples/stockfish_nnue/.work/aws-timing-investigation-20260930/`
and excluded from Git. `state.json` records the four owned instances and temporary
access resources; `worker-*-progress.json` contains retrieved completed blocks.
Four `worker-N-evidence.tar.gz` archives contain raw timings, counters, memory
snapshots, profiles, commands, diagnostic sources and correctness results.
`evidence-index.json` records their SHA-256 digests. Every included file was
verified against the remote archive manifest after download. Duplicate networks,
container images and per-pair executable copies were omitted with explicit
reasons; the original pinned inputs remain in the earlier operator directory.

The diagnostic allocation artifact is
`sha256:ea9dbf7569751ff7710ea174ec97147db767a5f89de56c816228d093c3869162`;
its heap/large modes share the exact executable and frozen weights. The scratch
allocation artifact is
`sha256:502a37fe9c1c3c5718647358f51d21b3e7eacd2b6791b0f52004df93a0e25360`.
The unmodified full-engine binary SHA-256 is
`953f370700ffd1b0875d06f92d48cd4f8b0abf4ff35cf5682345e5efcc29db66`.
The pinned source remains Stockfish
`0a215d6c9e48856ef630013b8ab8312941a59057`, `ARCH=armv8-dotprod COMP=gcc`.

An archive-packing call initially omitted its required `excludes` argument;
this was repaired before allocation measurements began. The first PMU-control
attempt stopped before measuring because this Linux `perf` includes a trailing
NUL in its acknowledgement. The first external-engine launch also failed before
measurement because its read-only directory lacked a placeholder for the network
mount. The corrected `external-engine-v2` creates that mountpoint first. All
failed setup attempts remain in the evidence; no timing samples were removed or
selectively retried. Early original-replay memory snapshots missed the C++
grandchild; later allocation/profile runs captured the complete process tree.

The new `diagnose.py` is operator-only. It reuses the production exact checks
and external request timer, records kernel CPU counters outside the timer and
computes diagnostic intervals across fresh process blocks. Unit tests demonstrate
that noiseless requests inside a process cannot erase between-process
uncertainty, and that one process pair cannot estimate it. The repository suite
passed **857 tests**, with 3 skipped and 2 deselected; the focused inference and
diagnostic suite passed 29 tests. These validate code paths, not timing precision.

No evolutionary fitness was admitted, no campaign was frozen, and
`pool_qualified` remains **false**. Only the application branch changed; fork
`main` retains its generic framework changes.

All four instances reached `terminated` and their temporary security group
`sg-025007cc2994e54b6` and key pair `shinka-stockfish-timing-20260930` were deleted
by **09:00:46 UTC**. `cleanup.json` records the verified states and successful
deletions. Each worker had only the loopback registry container left when its
evidence was archived.
