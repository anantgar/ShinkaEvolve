# Stockfish × Shinka: inference first, other research tracks

Decision, 2026-09-30: implement exact NNUE inference optimization first. AWS can
parallelize candidates across many independent workers. Weight training, search
changes and joint evolution remain separate experiments. The implementation and
runbook are in [`examples/stockfish_nnue`](../examples/stockfish_nnue/README.md).

## Why this first experiment avoids a large game budget

Yes: distinguishing small strength differences between strong engines often
requires very many games. Draws, opening choice, colors, time control and search
variance make a short match unreliable. A paired game experiment is much more
expensive than a deterministic inference replay. AWS reduces elapsed time by
running independent games, but does not remove the statistical requirement or
total CPU cost. There is no universal required game count: it depends on the
effect size and empirical paired-game variance.

An exact implementation optimization can instead require identical raw NNUE
outputs, then measure runtime. It does not need a strength match for every
individual. Preserve the full evaluator interface, quantization and weights;
test incremental state as carefully as standalone forward passes. A final
full-engine verification still matters because cache behavior, search share,
undefined behavior and hardware effects can erase a kernel improvement.

If NNUE occupies fraction `f` of engine time and its implementation speeds up by
factor `s`, a first approximation to engine speedup is
`1 / ((1 - f) + f/s)`. For example, `f=0.30, s=1.10` gives about 2.8% overall.
This is an estimate, not an Elo conversion; confirm it with the unchanged search.

## Angles worth pursuing

| Track | Shinka changes | Per-individual fitness | Main difficulty / AWS use |
|---|---|---|---|
| **Exact accumulator and feature transform** | Incremental updates, refresh loops, cache locality, redundant loads, scheduling | Exact replay gate, then paired runtime | Representative make/undo paths; dedicated ARM/x86 CPU workers |
| **Exact sparse/dense NNUE kernels** | SIMD packing, activation fusion, nonzero traversal, tiling, instruction scheduling | Same gate and runtime; memory/code-size bounds | Existing code is highly optimized; hardware-specific gains need separate campaigns |
| **Compiler/build recipe** | Fixed alternatives for PGO data, compiler version, LTO, architecture flags | Exact outputs and whole-engine speed | A separate campaign with build logic explicitly mutable; current task freezes it |
| **Trainer throughput** | Data loading, batching, feature generation, training kernels | Fixed training work and loss/gradient agreement, then examples/sec and cost | GPU profiling and reproducibility; GPU workers plus CPU preprocessing |
| **Training recipe / fine-tuning** | Sampling mix, loss targets, schedules, regularization, quantization-aware training | Cheap held-out loss screens, then fixed-time playing strength | Proxy loss may not predict Elo; GPU training and large CPU game pools |
| **Network architecture** | Width, bottlenecks, feature sets, quantization, sparsity | Strength at fixed time and hardware, with training budget fixed | Changes output semantics and search interaction; much larger experiment |
| **Search constants or a focused rule** | A narrow pruning/reduction/extension or parameter family | Paired games against a fixed reference | No exact-output shortcut; numeric constants may be better suited to SPSA |
| **Evaluation/search co-design** | A trained evaluator paired with small search adaptations | Joint strength after isolating each component's effect | Expensive, noisy credit assignment; reserve for a later phase |

Exact inference is a valid standalone Shinka problem. Search co-evolution is
not required when every evaluation result is unchanged. Hardware-specific
improvements are demonstrably possible: Stockfish's accepted
[accumulator change](https://github.com/official-stockfish/Stockfish/commit/db98633b1f8bbcad850bee892ec143ff8723ba80)
reported a local 0.60% speed gain, and an
[AVX2 activation change](https://github.com/official-stockfish/Stockfish/commit/1c384d3a8774ec9906e2e85b5005b22e27489044)
reported 2.45% on its tested machine. These are examples of remaining opportunity,
not promised gains on our hardware. A
[later loop change](https://github.com/official-stockfish/Stockfish/commit/8bc5caa2e4b1d4c189b1428e93158b10d3edb0b6)
also illustrates why a change can help one ISA and regress another.

For the initial campaign, prioritize accumulator/refresh work and sparse kernel
changes over cosmetic edits. Use profiles from the frozen baseline to choose
the hot functions. Keep SIMD families in separate worker pools, and retain
portable fallbacks in any eventual upstream contribution.

## Can we train weights?

Yes, with the existing [official NNUE trainer](https://github.com/official-stockfish/nnue-pytorch).
Start from a compatible pretrained network and a controlled fine-tuning recipe.
Let gradient descent train the weights; let Shinka evolve the recipe or training
code. Directly mutating millions of weight values with an LLM would be a poor
use of the available search budget.

An individual should include the exact recipe/code revision, parent checkpoint,
data selection and hashes, seed, training budget, optimizer settings and exported
network. Fix the search engine, target hardware, inference precision and game
time control while comparing recipes. Repeat promising recipes with multiple
training seeds, so a lucky checkpoint does not become the scientific conclusion.

Verification has distinct stages:

1. **Training correctness:** smoke-run the recipe; check finite losses,
   deterministic data splits, checkpoint resume, tensor shapes, and numerical
   agreement where an implementation optimization is meant to be exact.
2. **Export/integration correctness:** verify the quantized exported network
   against its own quantized reference and the engine loader; test all relevant
   features, saturation/rounding and accumulator paths. A newly trained network
   is expected to differ from the old network's evaluations.
3. **Cheap quality screens:** held-out prediction loss, calibration and tactical
   suites can eliminate obvious failures. They are proxies, not strength proof.
4. **Playing strength:** paired openings with colors reversed, fixed opponent,
   engine settings, CPU class, time control and adjudication. Run adequate games
   for the desired effect size; preserve raw results and opening-pair blocks.
   Repeat finalists at longer controls and with new seeds/openings.

For a fixed game budget, expected score is `(wins + draws/2) / games`. Rank
training candidates with a conservative lower bound on that score against the
same reference, using opening pairs as the resampling/statistical units. Use
the paired/pentanomial result structure when estimating uncertainty; the two
games from an opening are not independent Bernoulli trials. Invalid/crashing
engines fail the correctness gate; a correct weaker engine is a valid low scorer.

Use predetermined budget rungs for successive halving: cheap screening for all,
larger matches for survivors, independent confirmation for finalists. Comparing
raw scores from incompatible opponents or budgets is misleading. SPRT is useful
for accepting/rejecting a specified strength hypothesis, but its stopping time
or likelihood ratio is not a general fitness ranking for an evolutionary pool.
Follow the official [Fishtest statistical documentation](https://github.com/official-stockfish/fishtest/wiki/Mathematics)
when choosing game tests and stopping rules.

When games already use equal wall-clock time on identical CPUs, inference speed
already affects their outcomes. Do not add a second speed bonus to that Elo/score
without intentionally defining a new multi-objective problem. For different
training costs or model sizes, use explicit budget constraints or a Pareto
frontier rather than an arbitrary, changeable weighted score.

## When co-evolution becomes useful

Search consumes both evaluation values and their distribution. A newly trained
or smaller network can change score scale, uncertainty or tactical weaknesses,
which can interact with pruning and reductions. Co-adaptation may help then.
Establish evaluator gains with fixed search first, so the evidence is interpretable.

For finalists, measure a 2×2 experiment: old/new evaluator crossed with old/new
search. This reveals the isolated effects and whether the combination adds an
interaction benefit. Only then invest in joint evolution; otherwise an apparent
network improvement can merely be a search-specific compensation.

## AWS allocation

- **Inference campaign:** homogeneous CPU workers; one candidate at a time per
  instance; serial baseline/candidate pairs on one pinned CPU. Parallelize
  candidates and independent finalist repeats, not simultaneous timings on a host.
- **Training campaign:** GPU pools for fixed-budget training, independent seeds,
  and export validation; separate CPU pools for games. Keep generation/model
  artifacts in content-addressed S3 and record both GPU-hours and CPU-hours.
- **Data generation:** separate immutable reference engine workers. Freeze the
  resulting datasets before recipe comparisons; never regenerate them silently
  for different individuals.

Current code implements the inference row of this allocation. It provides the
queue/worker plumbing, not a deployed fleet, a trained network, a measured Elo
gain, or an already optimized Stockfish candidate. Production corpus selection,
worker AMI/account parameters, hardware calibration and the first evolutionary
campaign are the next operational steps.
