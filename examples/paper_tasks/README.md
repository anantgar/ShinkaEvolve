# Paper task reproductions

Research snapshot: 2026-07-17. Primary sources are the
[AlphaEvolve paper](https://arxiv.org/abs/2506.13131), its
[official result artifacts](https://github.com/google-deepmind/alphaevolve_results),
[released mathematical verification notebook](https://github.com/google-deepmind/alphaevolve_results/blob/4226acbf237ff9ad10ba7673a2af127a2d8a5971/mathematical_results.ipynb),
the [ShinkaEvolve paper](https://arxiv.org/abs/2509.19349), and the
[original ShinkaEvolve repository](https://github.com/SakanaAI/ShinkaEvolve).

## Repository contract

Every runnable directory is one independent evolution task:

```text
task/
  evaluate.py       # external, fixed, and invisible to the coding agent
  shinka.yaml       # mutable/immutable/hidden path policy
  seed_repo/        # initial candidate repository template
```

There are no source markers. Empty `mutable_paths` makes the normal contents of
the seed repository mutable; `immutable_paths` narrows that scope, and external
evaluation remains outside the candidate repository. A seed may be a full
multi-file repository when the workload needs one. These mathematical seeds use
one file because additional scaffolding would add no value.

Family directories are indexes only. No candidate or evaluator dispatches
between different fixed tasks. Repeated trials of one stochastic constructor,
or a dataset of examples for one agent objective, remain one evaluation task.

## What is implemented

### AlphaEvolve

- [`alphaevolve_constructions`](alphaevolve_constructions): 11 independently
  runnable geometric/construction instances disclosed in the paper artifacts.
- [`alphaevolve_discrete`](alphaevolve_discrete): six independent analytic and
  combinatorial instances.
- [`alphaevolve_matrix_multiplication/matmul_2_2_2`](alphaevolve_matrix_multiplication/matmul_2_2_2):
  one fixed tensor using the notebook's generic exact tensor verifier. The paper
  ran 54 fixed tensor tasks; this repository does not pretend one generic
  dispatcher is all 54.

AlphaEvolve also reports private Borg scheduling, TPU/Pallas tiling, Verilog,
compiler-IR, and FlashAttention workloads. Their candidate code and evaluators
were not released, so faithful runnable reproductions are not possible here.

### ShinkaEvolve

- [`shinkaevolve_circle_packing`](shinkaevolve_circle_packing): fixed 26-circle
  unit-square task, including exact and relaxed validation modes.
- [`shinkaevolve_aime`](shinkaevolve_aime): one agent-scaffold objective over
  the 30 AIME 2024 training examples, with the paper's model/call/run budgets.
- [`shinkaevolve_ale_bench`](shinkaevolve_ale_bench): ten separate task repos,
  one for each AHC problem, using the original recovered C++ seeds.
- [`shinkaevolve_moe`](shinkaevolve_moe): fixed load-balancing-loss task and
  paper configuration; an external trainer is still required because the paper
  trainer was not released.

The AlphaEvolve evaluators port the notebook's disclosed objective and
verification code for tensor decompositions and sections B.1-B.13. Local
wrappers add fixed-instance shape checks, finite-value checks, JSON output, and
explicit resource guards where repeated evolution requires them. The notebook
verifies published results; it does not disclose the full private search-time
evaluation cascades or starting programs. Seeds here therefore remain simple
valid baselines rather than claimed originals.

Approved non-paper benchmarks live in
[`../open_problem_tasks`](../open_problem_tasks). Further proposals are in
[`../open_problem_tasks/PROPOSALS.md`](../open_problem_tasks/PROPOSALS.md).
