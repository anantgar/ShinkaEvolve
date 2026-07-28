"""Simple nonsingular order-29 {+1,-1} seed matrix."""

import numpy as np


def construct_matrix() -> np.ndarray:
    matrix = -np.ones((29, 29), dtype=np.int64)
    np.fill_diagonal(matrix, 1)
    return matrix
