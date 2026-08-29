# PR #176 inspiration-selection benchmark

This harness compares the upstream random crossover selector with the proposed
maximum-cosine-distance selector. Both arms run the same instrumented code,
frozen task, evaluator image, proposal model, embedding model, search parameters,
and matched seed. The only arm-level override is
`evo.crossover_inspiration_selection`.

Both policies consume the same random control draw at every crossover. The
distance arm records that counterfactual draw, then substitutes the farthest
usable candidate, preventing the selector itself from shifting later RNG state.

The upstream baseline is frozen at
`9912af12d423504b8d580f4179fd15f5f88b8c50`. The secure Docker evaluator is
common benchmark infrastructure and should not be included in the selector PR.

## Model policy

The primary proposal model is `gemini-3.1-flash-lite`; embeddings use
`gemini-embedding-2`. Proposal reasoning is fixed at `medium`; Gemini's default
temperature of `1.0` is retained. The novelty LLM, meta LLM, prompt evolution,
dynamic model selection, and proposal oversubscription are disabled. This
isolates the inspiration-selection effect and avoids paying a second LLM to
reject proposals.

As of 2026-08-29, Google lists Gemini 3.1 Flash-Lite at $0.25/M text input
tokens and $1.50/M output tokens, and Gemini Embedding 2 at $0.20/M text input
tokens. Embedding 2 inputs receive Google's symmetric semantic-similarity task
instruction before embedding. If the proposal canary shows a quality floor,
rerun the entire campaign—not just one arm—with an Azure deployment of
`gpt-5.4-nano`. Do not mix proposal models within one paired campaign.

## Run

Prerequisites: Docker Desktop (or rootless Docker on Linux), this checkout's
Python environment with the `wandb` extra installed, and `GEMINI_API_KEY` plus
`WANDB_API_KEY` in the checkout's ignored `.env`. Online W&B logging uses the
`shinka-pr176-benchmark` project and a separate group for each campaign root.

Inspect the exact commands without building an image or making API calls:

```bash
python benchmarks/pr176/run_campaign.py --dry-run
```

`--dry-run` is implicit; the flag is shown only for readability and is not
required. To perform a minimal end-to-end canary (three total persisted
programs, including generation zero):

```bash
python benchmarks/pr176/run_campaign.py \
  --execute \
  --generations 3 \
  --repeats 1 \
  --policies random \
  --results-root results/pr176-canary-gemini-3-1-flash-lite
```

Start the full campaign only if both proposals parse/apply, evaluation and
embeddings complete, and the W&B run initializes and finishes cleanly. The
canary is an infrastructure check, not evidence about selector performance.

For the Azure fallback, deploy the model under the exact deployment name
`gpt-5.4-nano`, set `AZURE_OPENAI_API_KEY` and `AZURE_API_ENDPOINT` in addition
to `GEMINI_API_KEY`, and pass
`--proposal-model azure-gpt-5.4-nano`. Gemini Embedding 2 remains fixed.

Run the full alternating paired campaign:

```bash
python benchmarks/pr176/run_campaign.py --execute
```

The order is baseline/treatment, treatment/baseline, baseline/treatment for
repeats 1-3. Each arm has 150 persisted programs (149 post-seed proposals), two
islands, archive size 40, four archive inspirations, two top-k inspirations, and
a 10% crossover probability.

Analyze completed runs:

```bash
python benchmarks/pr176/analyze.py \
  --results-root results/pr176-9912af1
```

The analyzer reports verified best score, best-so-far AUC, correctness,
crossover child-parent delta, selected distance, fallback count, and estimated
API cost. Its conservative `claim_ready` check requires three matched repeats,
positive median final-best and AUC deltas, improvement in at least two pairs,
no material correctness regression, and at least 30 clean crossover decisions
per arm.

## Artifacts to attach to the PR

- `campaign_manifest.json`, including exact code, config, model, image, and seeds;
- `comparison.md` and `comparison.json`;
- each run's `programs.sqlite`, `inspiration_selections.jsonl`, and best program;
- total cost and wall-clock time;
- an honest note if the selector frequently fell back because embeddings were
  unavailable.
