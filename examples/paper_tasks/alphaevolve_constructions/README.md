# AlphaEvolve public construction tasks

This directory is an index, not a runnable multi-task candidate. Each child is
one independent repo-mode task with its own external evaluator and seed:

- `max_min_distance_16_2`
- `max_min_distance_14_3`
- `heilbronn_triangle_11`
- `heilbronn_convex_13`
- `heilbronn_convex_14`
- `kissing_number_11`
- `circle_square_26`
- `circle_square_32`
- `circle_rectangle_21`
- `hexagon_packing_11`
- `hexagon_packing_12`

Scores are maximized, so minimization tasks negate their objectives. The
feasibility checks and objectives are ported from sections B.7-B.13 of the
[released notebook](https://github.com/google-deepmind/alphaevolve_results/blob/4226acbf237ff9ad10ba7673a2af127a2d8a5971/mathematical_results.ipynb).
The hexagon evaluators preserve its strict convention that tangent polygons
intersect, and the kissing evaluator preserves its round-to-integer step. These
are the publicly disclosed improved construction instances, not AlphaEvolve's
undisclosed full task list.

To run a child directly (Shinka will initialize the seed repository when an
evolution run starts):

```bash
python circle_square_26/evaluate.py \
  --repo_path circle_square_26/seed_repo \
  --results_dir /tmp/ae-circle-26
```
