"""Original-style ShinkaEvolve seed for 26 variable-radius circles."""

import numpy as np


def _max_radii(centers: np.ndarray) -> np.ndarray:
    radii = np.ones(len(centers))
    for index, (x, y) in enumerate(centers):
        radii[index] = min(x, y, 1.0 - x, 1.0 - y)
    for left in range(len(centers)):
        for right in range(left + 1, len(centers)):
            distance = float(np.linalg.norm(centers[left] - centers[right]))
            if radii[left] + radii[right] > distance:
                scale = distance / (radii[left] + radii[right])
                radii[left] *= scale
                radii[right] *= scale
    return radii


def run_packing() -> tuple[np.ndarray, np.ndarray, float]:
    centers = np.zeros((26, 2))
    centers[0] = (0.5, 0.5)
    for index in range(8):
        angle = 2.0 * np.pi * index / 8
        centers[index + 1] = (0.5 + 0.3 * np.cos(angle), 0.5 + 0.3 * np.sin(angle))
    for index in range(16):
        angle = 2.0 * np.pi * index / 16
        centers[index + 9] = (0.5 + 0.7 * np.cos(angle), 0.5 + 0.7 * np.sin(angle))
    centers = np.clip(centers, 0.01, 0.99)
    radii = _max_radii(centers)
    return centers, radii, float(np.sum(radii))
