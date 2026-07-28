"""Schoolbook rank-8 decomposition for the fixed 2x2x2 tensor."""

import numpy as np


def construct(seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    del seed
    left = np.zeros((4, 8), dtype=float)
    right = np.zeros((4, 8), dtype=float)
    output = np.zeros((4, 8), dtype=float)
    term = 0
    for i in range(2):
        for j in range(2):
            for k in range(2):
                left[2 * i + j, term] = 1.0
                right[2 * j + k, term] = 1.0
                output[2 * k + i, term] = 1.0
                term += 1
    return left, right, output
