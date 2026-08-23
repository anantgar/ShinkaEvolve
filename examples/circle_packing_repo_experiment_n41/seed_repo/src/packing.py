"""Deterministic baseline for packing 41 circles in a unit square."""

from __future__ import annotations

import numpy as np


N_CIRCLES = 41


def run_packing():
    """Return a valid 6-by-7 grid baseline with one slot removed."""

    columns, rows = 6, 7
    centers = np.asarray(
        [
            ((column + 0.5) / columns, (row + 0.5) / rows)
            for row in range(rows)
            for column in range(columns)
        ][:N_CIRCLES],
        dtype=float,
    )
    radius = min(1.0 / (2.0 * columns), 1.0 / (2.0 * rows))
    radii = np.full(N_CIRCLES, radius, dtype=float)
    return centers, radii, float(np.sum(radii))
