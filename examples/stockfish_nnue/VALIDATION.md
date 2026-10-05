# Validate and submit the NNUE performance patch

The submitted change must preserve weights, NNUE outputs and search rules.
The [current pointer-only protocol](AWS_PR_VALIDATION_2026-10-05.md) controls
build metadata and uses one immutable baseline PGO profile per pair.
Historical pilot/combined-patch timings do not establish performance of this
submission. October 3/4 speed claims are withdrawn because `GIT_DIFFINDEX`
changed version code and layout. Preserve frozen campaigns and raw receipts.

## What to verify

Before timing, verify actual compiler commands, common `GIT_SHA`, `GIT_DATE` and
empty `GIT_DIFFINDEX`; do not rely on requested make arguments. Profile-build
recursion can override `EXTRACXXFLAGS`. Record the actual network-embedding mode.
For edits excluded on x86, require matching disassembly before claiming an
unaffected regression check. Rejected build identity or A/A controls cannot
qualify a speed claim; retain their receipts and declare any corrected protocol.

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

[Draft PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208)
contains only GCC NEON pointer materialization in `nnue_accumulator.cpp` and
AUTHORS, commit `830c5c3`. Lane-dot and accumulator-bank changes were removed
after review. No README is added to Stockfish. Track current checks and remaining
work in [SUBMISSION.md](SUBMISSION.md).

Recheck master, preserve the network/compiler identities, and verify GCC with
and without dot-product support, Clang and x86. The pointer constraint affects
ordinary NEON too. Use independent CPU families and boots, matching A/A controls,
and excluded-path code identity. Common baseline PGO isolates this source change;
independently retrained release profiles require separate evidence.

Follow Stockfish's [contribution rules](https://github.com/official-stockfish/Stockfish/blob/master/CONTRIBUTING.md)
and [speedup guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups):
keep the PR concise, add AUTHORS for a first contribution, use clang-format 20,
and retain matching bench signatures and a `No functional change` declaration.
Inspect the actual formatting step; the workflow can mask its failure.
Performance changes generally need Fishtest unless maintainers accept direct
evidence under the documented exception. Fishtest is deferred by the user;
no game result or exemption is claimed. A future test needs affected ARM/GCC
coverage, not only x86 or Clang workers.

The operator driver restores caller CPU affinity, including on failure. For
sanitizer checks use `-O1` while retaining debug assertions and recovery-disabled
ASan/UBSan. Use an ARM target for ordinary NEON; GCC `general-64` adds unsupported
`-m64` on ARM. Historic native Apple/Clang and scalar checks are recorded in the
[October 3 audit](AWS_VALIDATION_2026-10-03.md), rather than treated as speed evidence.
