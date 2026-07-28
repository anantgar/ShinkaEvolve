# AlphaEvolve matrix multiplication `(2, 2, 2)`

This repository is one fixed tensor instance, not a dispatcher over tensor
shapes. Its tensor construction and strict `np.array_equal` verification are
ported from AlphaEvolve's released notebook; candidate entries are not rounded
or otherwise repaired. It uses a three-seed, CPU-friendly proxy for the paper's
secondary success-rate objective. The paper used a larger evaluation cascade;
this example keeps the same ordering signal without claiming the same compute
budget.
