"""Seed: the signed coordinate vectors in dimension 11."""

import numpy as np


def construct() -> np.ndarray:
    basis = np.eye(11, dtype=np.int64)
    return np.vstack((basis, -basis))
