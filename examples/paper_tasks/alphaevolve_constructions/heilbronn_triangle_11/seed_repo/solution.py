"""Seed: deterministic interior points of the unit equilateral triangle."""

import math

import numpy as np


def construct() -> np.ndarray:
    rng = np.random.default_rng(1729)
    barycentric = rng.dirichlet(np.ones(3), size=11)
    vertices = np.asarray([(0.0, 0.0), (1.0, 0.0), (0.5, math.sqrt(3.0) / 2.0)])
    return barycentric @ vertices
