"""Uniform feasible seed for the fixed n=95 relaxation."""

import numpy as np


def construct() -> np.ndarray:
    return np.full(95, 0.5)
