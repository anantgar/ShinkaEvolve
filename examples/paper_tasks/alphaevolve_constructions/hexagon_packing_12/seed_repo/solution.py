"""Seed: a loose grid of 12 unit hexagons."""

import numpy as np


def construct() -> dict[str, object]:
    points = []
    for index in range(12):
        column = index % 4
        row = index // 4
        points.append((3.0 * (column - 1.5), 3.0 * (row - 1.0), 0.0))
    return {
        "inner": np.asarray(points),
        "outer_center": np.asarray((0.0, 0.0)),
        "outer_side": 10.0,
        "outer_angle_degrees": 0.0,
    }
