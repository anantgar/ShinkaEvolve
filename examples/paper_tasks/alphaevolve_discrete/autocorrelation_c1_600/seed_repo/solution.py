"""Smooth nonnegative seed sequence."""

import numpy as np


def construct() -> np.ndarray:
    x = np.linspace(-1.0, 1.0, 600)
    return np.exp(-4.0 * x * x)
