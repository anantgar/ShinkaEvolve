# Examples

The examples below are mostly legacy single-file benchmarks unless they
explicitly state repo mode. For a public repo-mode run, use
`examples/pipeline_tests/euclidean_tsp_repo`, where the evaluator accepts
`--repo_path`. The `examples/inference_pipeline_repo` task is a secure
persistent-service example and requires secure evaluation setup.

ShinkaEvolve ships with runnable tasks demonstrating different languages,
evaluation styles, and runtime profiles.

---

## Circle Packing

Recommended first example.

| | |
|-|-|
| **Path** | [`examples/circle_packing`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/circle_packing) |
| **Language** | Python |
| **Focus** | Async evolution, config profiles, result notebooks |

Key files: `initial.py`, `evaluate.py`, `run_evo.py`, `shinka_small.yaml`,
`shinka_medium.yaml`, `shinka_large.yaml`

```bash
cd examples/circle_packing
python run_evo.py --config_path shinka_small.yaml
```

Best reference for budgeted async runs and notebook inspection after evolution.

---

## Repo-Mode Tasks

### Euclidean TSP Pipeline

[`examples/pipeline_tests/euclidean_tsp_repo`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/pipeline_tests/euclidean_tsp_repo)
is the smallest public repo-mode example. Its evaluator loads
`seed_repo/src/solver.py`, while Shinka keeps the evaluator outside the
candidate repository and initializes the plain seed directory automatically.

```bash
shinka_run \
  --task-dir examples/pipeline_tests/euclidean_tsp_repo \
  --results_dir results/euclidean_tsp_repo \
  --num_generations 2
```

### Secure Inference Service

[`examples/inference_pipeline_repo`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/inference_pipeline_repo)
demonstrates a persistent candidate service evaluated through framed stdio.
It uses `SecureJobConfig`/secure CLI mode, pinned container images, private
inputs, and a Node candidate service. It is not a drop-in replacement for the
public local example above.

---

## Benchmark Catalogs

- [`examples/paper_tasks`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/paper_tasks)
  contains 31 independent paper-task artifacts. Each child has its own
  evaluator, configuration, and seed repository.
- [`examples/open_problem_tasks`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/open_problem_tasks)
  contains four independent open-problem task artifacts.

For both catalogs, `seed_repo/` is a normal tracked directory, not a nested
Git module. A task may put its implementation at the repository root or under
`src/`; the evaluator's path contract decides which layout is correct.

---

## Game 2048

| | |
|-|-|
| **Path** | [`examples/game_2048`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/game_2048) |
| **Language** | Python |
| **Focus** | Policy optimization in a game environment |

Use this for a nontrivial evaluator with task-specific environment logic and a
control-oriented problem shape.

---

## Julia Prime Counting

| | |
|-|-|
| **Path** | [`examples/julia_prime_counting`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/julia_prime_counting) |
| **Language** | Julia candidate + Python evaluator |
| **Focus** | Cross-language evolution, strict correctness scoring |

Legacy manual eval command: run `examples/julia_prime_counting/evaluate.py`
against `initial.jl` and write to `results/manual_eval`.

Cleanest example of evolving a non-Python candidate while keeping the evaluation
harness in Python.

---

## Go Collatz Steps

| | |
|-|-|
| **Path** | [`examples/go_collatz_steps`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/go_collatz_steps) |
| **Language** | Go candidate + Python evaluator |
| **Focus** | Cross-language evolution, strict correctness scoring |

```bash
cd examples/go_collatz_steps
python evaluate.py --program_path initial.go --results_dir results/manual_eval
```

Use this for a compact compiled-language task where Go candidates compute
Collatz stopping times and `evaluate.py` owns `go run` validation and scoring.

---

## Fortran Heat Diffusion

| | |
|-|-|
| **Path** | [`examples/fortran_heat_diffusion`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/fortran_heat_diffusion) |
| **Language** | Fortran candidate + Python evaluator |
| **Focus** | Compiled numerical stencil evolution |

Legacy manual eval command: run `examples/fortran_heat_diffusion/evaluate.py`
against `initial.f90` and write to `results/manual_eval`.

Use this when candidates should be compiled with `gfortran` before
floating-point correctness and runtime scoring.

---

## Wolfram GCD Sum

| | |
|-|-|
| **Path** | [`examples/wolfram_gcd_sum`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/wolfram_gcd_sum) |
| **Language** | Wolfram Language candidate + Python evaluator |
| **Focus** | Program optimization against an auto-calibrated baseline |

```bash
cd examples/wolfram_gcd_sum
python evaluate.py --program_path initial.wl --results_dir results/manual_eval
```

Use this when candidates run through `wolframscript` and are scored by
speedup over a deoptimized seed.

---

## Novelty Generator

| | |
|-|-|
| **Path** | [`examples/novelty_generator`](https://github.com/SakanaAI/ShinkaEvolve/tree/main/examples/novelty_generator) |
| **Language** | Python |
| **Focus** | Novelty-oriented generation, nontraditional evaluation |

Use this to inspect prompt design, novelty judgment, and nontraditional
evaluation metrics.

---

## Tutorial Notebook

[`examples/shinka_tutorial.ipynb`](https://github.com/SakanaAI/ShinkaEvolve/blob/main/examples/shinka_tutorial.ipynb) — exploratory, notebook-centered introduction
before moving into full CLI or API workflows.

---

## Choosing an Example

| Goal | Example |
|------|---------|
| Best default choice | Circle Packing |
| Public repo-mode evolution | Euclidean TSP Pipeline |
| Secure candidate service | Inference Pipeline Repo |
| Paper benchmarks | Paper Task Catalog |
| Open-problem benchmarks | Open-Problem Catalog |
| Cross-language evolution | Julia Prime Counting |
| Compiled numerical stencil | Fortran Heat Diffusion |
| Program optimization | Wolfram GCD Sum |
| Game / control optimization | Game 2048 |
| Creative / open-ended | Novelty Generator |
