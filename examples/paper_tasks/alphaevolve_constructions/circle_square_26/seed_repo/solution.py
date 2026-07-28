"""Seed: a separated 6-by-5 grid truncated to 26 circles."""

import numpy as np


def construct() -> np.ndarray:
    radius = 0.999999 / 12.0
    circles = []
    for row in range(5):
        for column in range(6):
            if len(circles) == 26:
                return np.asarray(circles)
            circles.append(((column + 0.5) / 6.0, (row + 0.5) / 5.0, radius))
    raise AssertionError("unreachable")
