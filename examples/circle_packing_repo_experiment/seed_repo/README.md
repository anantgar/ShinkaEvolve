# Circle Packing Seed Repo

The evolutionary run starts from the simple implementation in
`src/packing.py`. The agent may edit the repository, add helpers or tests, and
remove obsolete files. The external evaluator depends only on the public
contract below.

The required public function is:

```python
def run_packing():
    return centers, radii, sum_radii
```

`centers` must be shape `(26, 2)`, `radii` must be shape `(26,)`, all circles
must stay inside the unit square, and no two circles may overlap.
