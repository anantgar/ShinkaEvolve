"""Seed: a regular 13-gon."""

import math

import numpy as np


def construct() -> np.ndarray:
    angles = 2.0 * math.pi * np.arange(13) / 13
    return np.column_stack((np.cos(angles), np.sin(angles)))
