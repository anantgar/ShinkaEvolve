# Stockfish NNUE Forward-Path Experiment Plan

## Goal

Evolve only Stockfish's NNUE evaluation path for lower Apple Silicon latency
while preserving every NNUE output exactly. The complete Stockfish source tree
may be visible to the agent, but search, move generation, threading, UCI, build
logic, network weights, and the evaluator remain immutable.

This experiment does not optimize tree search or claim an Elo improvement.

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
independent positions. Split them into a small public development corpus and a
much larger sealed holdout. Pin the source, generation code, seed, and digest of
each corpus.

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

Exclude process startup, network loading, FEN parsing, move parsing, and move
application from fitness timing. Warm the executable and network pages equally
before paired measurements.

1. **Warm incremental:** retain the production accumulator stack and caches
   while replaying consecutive and branch-shaped traces. This is the primary
   workload.
2. **Cold accumulator:** invalidate/reset accumulator state before each selected
   position and measure a production refresh. The process and network weights
   remain warm, so this measures NNUE work rather than executable startup.
3. **Hot reuse:** evaluate an already-computed accumulator repeatedly. This
   isolates the layer-propagation-heavy path and reduces timer overhead through
   batching.

Use provisional fitness weights of 60% warm incremental, 25% cold accumulator,
and 15% hot reuse. Before freezing the benchmark, instrument an immutable
baseline search over representative positions and adjust these weights once to
match observed production frequencies. Do not change them during a campaign.

## Correctness gate

A candidate receives no timing fitness unless it passes all correctness checks:

- exact raw NNUE outputs at every public and private checkpoint;
- incremental output equal to a fresh accumulator/cache refresh;
- exact restoration after make/undo/redo and sibling traversal;
- identical results after corpus reordering and repeated execution;
- deterministic behavior with clean and retained cache state;
- thread-safety checks if the mutable code introduces shared state; and
- no build, sanitizer, resource, or mutation-policy failure.

Also compare Stockfish's final static evaluation as an integration guard, but
do not time or evolve tree search.

## Timing and fitness

For each workload shard, baseline and candidate execute the identical ordered
trace with the same number of NNUE calls. Record the **total time spent inside
the NNUE evaluation boundary** and the call count.

Use total time for comparison, not the arithmetic mean of per-position ratios:

```text
workload_speedup = baseline_total_nnue_ns / candidate_total_nnue_ns
reported_ns_per_call = total_nnue_ns / call_count
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
traces, reordered runs, memory limits, and multiple workload types must prevent
corpus-specific output memoization from becoming a valid optimization.

## Evaluator structure

Keep four separately reviewable components:

1. candidate seed repository containing the pinned Stockfish source;
2. trusted preparation tooling for the source, NNUE network, toolchain, and
   immutable baseline;
3. sealed replay/evaluator code and private trace corpus; and
4. public task documentation, policy, and focused evaluator tests.

Run generated code without provider credentials, general network access, a
Docker socket, or writable host mounts. Record the Stockfish commit, network
digest, compiler and OS versions, build flags, evaluator commit, machine model,
resource limits, and corpus digests with every result.

## Completion plan

- [ ] Transplant only the parked Stockfish task and focused tests onto current
      Shinka contracts; keep unrelated branch history out.
- [ ] Select and pin a current Stockfish development commit and matching NNUE
      network before adapting the harness.
- [ ] Finalize the mutable file allowlist and prove all other paths are rejected.
- [ ] Build the real-game linear, branch, cold, hot, public, and sealed corpora.
- [ ] Implement exact-output replay and total-time workload measurement.
- [ ] Add unit tests for parsing/statistics and integration tests for replay,
      illegal changes, malformed output, failures, timeouts, and memory limits.
- [ ] Verify the unchanged seed and separately rebuilt baseline are identical.
- [ ] Run repeated baseline-versus-baseline and deliberately slower-candidate
      calibration; freeze weights, shard duration, repetitions, and promotion
      threshold from those results.
- [ ] Merge the benchmark definition before preparing campaign artifacts.
- [ ] Run a 5–10 evaluated-candidate canary without framework edits.
- [ ] Run the frozen full campaign with one timing evaluator at a time.
- [ ] Recheck finalists on the sealed corpus in at least three fresh runs, then
      validate sanitizers, production-equivalent PGO/LTO, and non-target ISA
      compilation before claiming an improvement.

Use the local M2 for development and calibration. Choose a quiet local campaign
or a clean AWS M2 Mac only after calibration reveals the runtime and noise; the
fitness machine and toolchain must remain unchanged within a campaign.
