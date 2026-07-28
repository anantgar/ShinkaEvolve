"""Smooth asymmetric seed sequence."""

import numpy as np


def construct() -> np.ndarray:
    x = np.linspace(-1.0, 1.0, 400)
    return np.exp(-4.0 * x * x) * (1.0 - 0.25 * x)
