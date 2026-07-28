"""Seed: a regular 14-gon."""

import math

import numpy as np


def construct() -> np.ndarray:
    angles = 2.0 * math.pi * np.arange(14) / 14
    return np.column_stack((np.cos(angles), np.sin(angles)))
