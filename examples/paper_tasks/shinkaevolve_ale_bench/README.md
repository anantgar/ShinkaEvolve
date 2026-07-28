# ShinkaEvolve ALE-Bench LITE tasks

Each child is a separate evolution repository for exactly one paper problem:

- `ahc008`
- `ahc011`
- `ahc015`
- `ahc016`
- `ahc024`
- `ahc025`
- `ahc026`
- `ahc027`
- `ahc039`
- `ahc046`

Every `seed_repo/main.cpp` is the corresponding best ALE-Agent solution shipped
with the original ShinkaEvolve release. Its own external evaluator scores 50
generated public cases. Private evaluation is opt-in because the paper submits
only selected best candidates.

```bash
python ahc039/evaluate.py \
  --repo_path ahc039/seed_repo \
  --results_dir /tmp/shinka-ahc039
```

Running requires the official `SakanaAI/ALE-Bench` package, Docker images, and
contest resources described by that project.
