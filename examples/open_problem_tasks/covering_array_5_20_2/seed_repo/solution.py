"""Deterministic random seed for CA(N; 5, 20, 2)."""

import numpy as np


def construct_array() -> np.ndarray:
    rng = np.random.default_rng(20260716)
    return rng.integers(0, 2, size=(400, 20), dtype=np.int8)
