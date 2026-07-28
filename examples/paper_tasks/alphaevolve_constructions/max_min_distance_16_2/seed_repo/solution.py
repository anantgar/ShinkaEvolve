"""Seed: 16 equally spaced points on a circle."""

import math

import numpy as np


def construct() -> np.ndarray:
    angles = 2.0 * math.pi * np.arange(16) / 16
    return np.column_stack((np.cos(angles), np.sin(angles)))
