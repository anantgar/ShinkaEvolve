# Stockfish NNUE Forward-Path Experiment Plan

## Goal

Evolve only Stockfish's NNUE evaluation path for lower latency on a fixed CPU
while preserving every NNUE output exactly. Relevant Stockfish engine source is
visible to the agent, with tests and benchmark implementations withheld. Search,
move generation, threading, UCI, build logic, weights and interfaces are immutable.

This experiment does not optimize tree search or claim an Elo improvement.

Implementation, 2026-09-30: [`examples/stockfish_nnue`](../examples/stockfish_nnue/README.md)
now provides preparation, exact replay, secure evaluation, calibration and AWS
submission. The first AWS target is Graviton/Linux ARM; x86 AVX2 is a separate
campaign, and native Apple builds remain useful for harness development. See
[`stockfish_shinka_research_options.md`](stockfish_shinka_research_options.md)
for training and search alternatives. The operational runbook supersedes earlier
prototype assumptions below where explicitly noted.
The [evaluation audit](stockfish_nnue_evaluation_audit.md) records the v2 changes
and production qualification requirements.

## Mutation boundary

Use an explicit allowlist rather than all of `src/nnue`.

Mutable code may include:

- accumulator and cache update implementation;
- feature transformation;
- sparse and dense affine layers;
- activation, NNZ, and SIMD helpers; and
- the internal NNUE propagation implementation.

Keep network architecture, dimensions, feature definitions, quantization,
scaling, weights, serialization, public interfaces, compiler flags, and every
non-NNUE path immutable. The trusted evaluator must reject any change outside
the reviewed allowlist before compiling a candidate.

## Evaluation corpus

Use deterministic traces derived from real games rather than a bag of random
independent positions. Keep development, fitness and final-confirmation datasets
separate and outside mutation containers. Pin the source, generation code, seed
and digest of each corpus. The importer's legacy `public.json` name does not
make that split visible to the mutation agent.

The corpus should cover openings, middlegames, endgames, captures, quiet moves,
castling, en passant, promotions, checks, king moves, and materially different
piece densities. Preserve consecutive positions within a trace.

Add branch-shaped traces consisting of make, evaluate, undo, and sibling-move
operations. These can be recorded from an immutable Stockfish search or
generated from real-game positions, but search itself is neither timed nor
mutable. Linear game replay alone does not reproduce accumulator-stack and
cache reuse during depth-first search.

Independent shuffled positions remain useful as a correctness and
anti-memoization control, but they are not the primary timing workload.

## Benchmark workloads

Exclude process startup, network loading, FEN parsing and move parsing from
fitness timing. Warm the executable and network pages equally before paired
measurements. The implemented authoritative clock is outside the candidate
process, so scored replay requests include immutable make/undo, accumulator
orchestration, checksums and protocol overhead. This deliberately replaces the
prototype's candidate-reported kernel time as the fitness authority. Per-call
timers have been removed. Replay gains require full-engine confirmation.

1. **Warm incremental:** retain the production accumulator stack and caches
   while replaying consecutive and branch-shaped traces. This is the primary
   workload.
2. **Cold accumulator:** invalidate/reset accumulator state before each selected
   position and measure a production refresh. The process and network weights
   remain warm, so this measures NNUE work rather than executable startup.
3. **Hot reuse:** evaluate an already-computed accumulator repeatedly. This
   emphasizes the layer propagation path while keeping protocol overhead small.

Use provisional fitness weights of 60% warm incremental, 25% cold accumulator,
and 15% hot reuse. Before freezing the benchmark, instrument an immutable
baseline search over representative positions and adjust these weights once to
match observed production frequencies. Do not change them during a campaign.

## Correctness gate

A candidate receives no timing fitness unless it passes all correctness checks:

- exact raw NNUE outputs at every private checkpoint;
- eager and lazy incremental output equal to a fresh accumulator/cache refresh;
- exact restoration after make/undo/redo and sibling traversal;
- identical results after corpus reordering and repeated execution;
- deterministic behavior with clean and retained cache state;
- no build, resource, or mutation-policy failure.

Also compare Stockfish's final static evaluation as an integration guard, but
do not time or evolve tree search.
Finalist qualification adds sanitizers, independent holdouts and full-engine
verification. New shared mutable state also requires explicit concurrency testing.

## Timing and fitness

For each workload shard, baseline and candidate execute the identical ordered
trace with the same number of NNUE calls. Record the **externally measured total
replay duration** and the call count.

Use total time for comparison, not the arithmetic mean of per-position ratios:

```text
workload_speedup = baseline_replay_seconds / candidate_replay_seconds
replay_ns_per_call = replay_seconds * 1e9 / call_count
```

Total time correctly preserves the cost of expensive and inexpensive positions
without giving every per-position ratio equal influence. `ns_per_call`, p50,
and p95 are useful diagnostics, but are not the fitness statistic.

Run paired baseline/candidate measurements in alternating AB/BA order on a
quiet, serialized machine. For pair `i`, combine workload ratios in log space:

```text
pair_log_speedup_i = sum(workload_weight * log(workload_speedup_i))
```

Fitness is the exponentiated one-sided 95% lower confidence bound of the mean
paired log speedup. Report the geometric mean speedup, lower bound, standard
error, per-workload totals, call counts, and all raw paired samples.

Split the private suite into enough deterministic shards to expose variance.
Set minimum shard duration and repetition count only after baseline-versus-
baseline calibration. Do not subtract estimated clock overhead; use identical
instrumentation and sufficiently long samples so it cancels in paired ratios.

Reject material network, accumulator, cache, binary-size, or RSS growth. Hidden
traces, reordered runs, memory limits and multiple workload types reduce the
opportunity for corpus-specific output memoization. They do not prove its absence;
review finalist diffs and validate on untouched positions and full-engine work.

## Evaluator structure

Keep four separately reviewable components:

1. candidate seed repository containing the pinned Stockfish source;
2. trusted preparation tooling for the source, NNUE network, toolchain, and
   immutable baseline;
3. sealed replay/evaluator code and private trace corpus; and
4. public task documentation and policy, with operator-only evaluator tests.

Run generated code without provider credentials, general network access, a
Docker socket, or writable host mounts. Record the Stockfish commit, network
digest, compiler and OS versions, build flags, evaluator commit, machine model,
resource limits, and corpus digests with every result.

## Completion plan

- [x] Implement the task on current secure Shinka contracts, preserving unrelated
      branch history.
- [x] Select and pin a current Stockfish development commit and matching NNUE
      network before adapting the harness.
- [x] Finalize the mutable file allowlist and reject all other paths.
- [x] Implement PGN splitting and linear/branch/cold/hot replay plus public smoke cases.
- [ ] Select production PGNs and a separate untouched finalist corpus.
- [x] Implement exact-output replay and externally timed workload measurement.
- [x] Add focused policy/statistics/transport tests and real container integration.
- [x] Verify the unchanged seed and separately rebuilt baseline are identical.
- [x] Run local A/A, deliberately slower and incorrect-output controls.
- [ ] Repeat calibration on production AWS hardware; freeze workload frequencies,
      duration, repetitions and promotion threshold before evolution.
- [ ] Review and integrate the implementation before the evolutionary campaign.
- [ ] Run a 5–10 evaluated-candidate canary without framework edits.
- [ ] Run the frozen full campaign with one timing evaluator at a time.
- [ ] Recheck finalists on the sealed corpus in at least three fresh runs, then
      validate sanitizers, production-equivalent PGO/LTO, and non-target ISA
      compilation before claiming an improvement.

Use the local M2 for development smoke tests. The selected initial production
path is a homogeneous Graviton/Linux pool. Other instance families and a native
M2 campaign require separate calibration and result populations; do not mix their
scores. The fitness machine class and toolchain remain fixed within a campaign.
