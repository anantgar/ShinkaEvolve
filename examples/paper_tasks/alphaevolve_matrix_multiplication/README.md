# AlphaEvolve matrix multiplication

AlphaEvolve ran separate searches on 54 matrix-multiplication tensors. This
index currently contains one independently runnable fixed instance:

- [`matmul_2_2_2`](matmul_2_2_2)

Its evaluator ports the released notebook's generic tensor construction and
strict equality check, minimizes rank, and uses a cheap three-seed proxy for the
paper's multi-seed success-fraction tie-breaker. It does not repair or quantize
candidate factors, expose dimensions as a task switch, or claim to reproduce
Google's accelerator search cascade.
