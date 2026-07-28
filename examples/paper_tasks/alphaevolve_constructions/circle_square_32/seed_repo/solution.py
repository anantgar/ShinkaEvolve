"""Seed: a 6-by-6 grid truncated to 32 circles."""

import numpy as np


def construct() -> np.ndarray:
    radius = 0.999999 / 12.0
    circles = []
    for row in range(6):
        for column in range(6):
            if len(circles) == 32:
                return np.asarray(circles)
            circles.append(((column + 0.5) / 6.0, (row + 0.5) / 6.0, radius))
    raise AssertionError("unreachable")
