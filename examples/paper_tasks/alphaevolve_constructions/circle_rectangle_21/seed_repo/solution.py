"""Seed: 21 equal circles on a 7-by-3 grid."""

import numpy as np


def construct() -> np.ndarray:
    radius = 0.0999999
    return np.asarray(
        [
            ((column + 0.5) * 0.2, (row + 0.5) * 0.2, radius)
            for row in range(3)
            for column in range(7)
        ]
    )
