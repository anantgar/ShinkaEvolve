# Validate and submit the NNUE performance patch

This is an exact implementation change: weights, NNUE outputs and search rules
must remain unchanged. The September 30 Graviton pilot and independent synthetic
holdout measured 2.33% and 2.26% faster fixed searches. They do not establish
representative workload performance, portability or Elo. Preserve those frozen
campaigns and the original patch; create new validation directories and manifests.

## What to verify

1. **Correctness:** compare raw NNUE values and final evaluations against the
   unchanged baseline for both perspectives, eager/lazy accumulators, refresh,
   make/undo, sibling branches, null moves, castling, promotion, en passant and
   Chess960. The existing private replay checker exercises these paths. Run its
   release and ASan/UBSan builds, with sanitizer recovery disabled. A bench
   signature alone cannot replace these checks.
2. **Search behavior:** run the same positions at fixed depth, one thread, same
   network, hash and reset policy. Require exact scores, PV, best/ponder moves,
   node totals and checksums. This exercises evaluations in real search branches.
   Replay positions from actual games; there is no need to play new games merely
   to obtain positions. No finite sample proves equivalence for every position.
3. **Speed:** time that identical work externally on quiet native machines, with
   fresh sequential processes, warmups and balanced ABBA/BAAB order. Compare
   whole-engine throughput across position groups and depths. Run compiler PGO/LTO
   builds representative of releases as well as the campaign build. Keep compiler,
   flags and network identical within each baseline/candidate comparison.
4. **Playing strength:** equal-time games test whether extra search translates
   into stronger play. They cannot prove unchanged predictions: faster search
   may reach different positions and choose different moves. Use paired openings
   and reversed colors with the accepted Fishtest statistical procedure; do not
   invent a magic game count or repeatedly inspect fixed-sample intervals.

Profiling is diagnostic. On native Linux, run separate `perf stat` and `perf record`
jobs against each binary on the same workload to explain changes in cycles,
instructions and hot functions. Do not collect profiles during scored timing.
A single played game's wall time mixes positions, clock management and search
depth and is insufficient evidence of a speedup.

## Less expensive future proposals

Build a task-specific agent image from the already qualified provider image:

```bash
docker build -f examples/stockfish_nnue/AgentDockerfile \
  --build-arg AGENT_BASE=registry/agent@sha256:REPLACE_WITH_DIGEST \
  -t registry/nnue-agent:validation examples/stockfish_nnue
```

Publish and configure its immutable digest, then rerun the real provider edit/resume
canary. Before authorizing a new batch, verify that the agent can run
`python3 agent_compile.py --target graviton` in its sanitized source view. New
campaigns include this immutable helper. It compiles NNUE objects in temporary
space without private benchmark/test sources. It catches compile failures; it
does not supply fitness, correctness tests, or full-engine linking to the agent.
The private builder continues restoring withheld sources to build the real engine.

Failed builds now preserve both bounded compiler streams in private, structured
content-addressed diagnostics. Provider failures retain redacted stderr/stdout
links and a private diagnostic. Generation failure artifacts reference these
records without copying private output into rewards or mutation prompts.
These changes help explain future failures; they cannot recover missing old logs.

## Real-game corpus and staged operator measurements

Supply a curated PGN collection covering the intended deployment: openings,
middlegames, endgames and multiple game sources. Keep source identity and selection
criteria. Human-game or opening-only data is not automatically representative of
engine search. The importer records provenance and samples early/middle/late
parts of trace segments without selecting positions by candidate results:

```bash
python -m examples.stockfish_nnue.validation_corpus \
  --pgn /private/representative-games.pgn --output /private/validation-corpus \
  --seed 20261003 --max-cases 96
```

Games are deduplicated before splitting into development, screening and finalist
sets. Sampled search positions cannot overlap across roles, including
transpositions with different move clocks. Concrete cases stay outside mutation
snapshots. Keep the finalist set untouched until selecting a fixed candidate.
The cap and source selection remain a declared coverage limit.

The operator driver uses the existing fixed-search checker and statistical model.
It accepts trusted, already built executables; it is not a sandbox for arbitrary
candidate code and does not replace private replay, resource or sanitizer checks:

```bash
python -m examples.stockfish_nnue.validate_candidate \
  --baseline /private/bin/baseline --candidate /private/bin/candidate \
  --network /private/network.nnue --corpus /private/validation-corpus/screening.json \
  --stage screening --cpu 2 --passes 1 --output /private/screen-candidate
```

Run compile and exact checks first, then the 24-block screening stage. Screening
can eliminate obvious regressions; its estimates are not final fitness or a
speedup claim. A noisy screen remains uncertain, not evidence of a bad candidate.
For the selected candidate, use `--stage finalist` and `finalist.json` to collect
all 128 blocks on each of at least two independent worker boots. Run matching
`--control aa` jobs on both machines, plus the existing private mild/gross
slowdown and wrong-output controls before qualification. Do not claim validation
from an uncontrolled standalone timing result.

Use native Linux and an explicit engine core; the driver excludes the controller
from it. Set passes from a baseline-only probe before candidate timing so every
sample exceeds the one-second floor. Reuse that declared plan for controls and
candidate. Do not adjust it midway, trim samples or rerun until favorable. Each
output directory is exclusive; plan, partial checkpoints and rejected results
are retained. This tool never admits Shinka fitness. Resource limits and the
complete controls remain the secure evaluator's responsibility. Existing frozen
evolution fitness and completed 128-block campaigns are unchanged.

## Upstream submission

The first portability check on October 3 rebased the saved patch without conflicts
onto Stockfish master `49ea5ded38315cff8e67f4a677a9e7811612fbf6` and built the native
Apple/Clang baseline and candidate. Both default benches searched **1,714,434
nodes**; fresh-process depth-7 smoke searches matched all recorded fingerprints.
The public compile helper also passed Apple SIMD and scalar object builds. The
local standalone branch is `codex/nnue-neon-inference`, commit `233068f`, in
`.work/submission-master`; it contains only the three NNUE files and is unpushed.
These are portability/correctness checks, not local speed evidence. The new
agent image recipe has not been built: the local Docker daemon was unavailable.
Master uses `nn-252f33942263.nnue`, different from the pilot's
network; earlier performance evidence does not transfer automatically. Preserve
the baseline SHA, network SHA and compiler for every new result.

Before publication, recheck current master and form a small standalone Stockfish
branch containing only the three NNUE changes. Verify ARM with and without
dot-product support, GCC and Clang, plus scalar/x86 builds. The GCC-only pointer
constraints also affect ordinary NEON. Test another ARM CPU family, not just
another VM of the same type. Consider separating lane-dot/bank changes from
pointer-scheduling changes if the latter are not consistently beneficial.

Stockfish requests current-master patches, portable code, readable evidence and
the appropriate bench/functional-change declaration. Its speedup guidance asks
for repeated benchmarks on different machines and generally Fishtest; small,
assembly-verifiable improvements may instead go directly to a PR. Our ARM-specific
gain needs ARM-capable testing: an x86-only match fleet will not measure its benefit.
Discuss the applicable worker coverage with maintainers before consuming Fishtest
capacity. More complex patches may require normal STC and LTC tests.
([Contribution rules](https://github.com/official-stockfish/Stockfish/blob/master/CONTRIBUTING.md),
[Speedup and submission guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups))

Prepare one clear commit ending with `No functional change`, verified matching
default bench signatures, hardware/compiler/build details, raw speed scripts and
results, correctness/sanitizer coverage, and any Fishtest links. Add the author
to AUTHORS if required for a first contribution. Publish the Stockfish branch
and open its PR when this evidence is ready; Shinka's infrastructure commits
do not belong in that PR. No upstream PR or public Fishtest test has been created
by this tooling update.
