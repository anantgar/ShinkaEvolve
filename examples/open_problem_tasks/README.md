# Open-problem evolution tasks

Each child directory is a completely independent task artifact:

```text
task/
  evaluate.py       # external/invisible evaluation
  shinka.yaml       # mutable/immutable policy and task prompt
  seed_repo/        # initial candidate repository template
```

The current seeds need only one mutable source file. Before launching, copy a
task if desired, initialize its `seed_repo/` as a git repository, and commit the
seed. Empty `mutable_paths` means the whole seed repository is mutable except
for protected, explicitly immutable, or hidden paths. No marker strings inside
source files define mutability.

Implemented after approval:

- `maximal_determinant_29`
- `covering_array_5_20_2`
- `degree_diameter_4_5`
- `golomb_ruler_29`

