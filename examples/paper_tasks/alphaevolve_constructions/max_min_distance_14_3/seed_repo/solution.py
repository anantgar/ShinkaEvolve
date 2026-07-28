"""Seed: a deterministic Fibonacci-sphere construction."""

import math

import numpy as np


def construct() -> np.ndarray:
    points = []
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for index in range(14):
        z = 1.0 - 2.0 * (index + 0.5) / 14
        radial = math.sqrt(max(0.0, 1.0 - z * z))
        angle = index * golden_angle
        points.append((radial * math.cos(angle), radial * math.sin(angle), z))
    return np.asarray(points)
