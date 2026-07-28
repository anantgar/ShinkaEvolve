"""Exact external evaluator for the undirected (Delta,D)=(4,5) problem."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np


MAXIMUM_DEGREE = 4
MAXIMUM_DIAMETER = 5
MOORE_BOUND = 485
PUBLISHED_2024_RECORD = 364


def assess(value: Any) -> tuple[float, dict[str, Any], bool]:
    if not isinstance(value, dict):
        raise ValueError("construct_graph() must return a dictionary")
    n = value.get("num_vertices")
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)):
        raise ValueError("num_vertices must be an integer")
    n = int(n)
    if not 2 <= n <= MOORE_BOUND:
        raise ValueError(f"num_vertices must lie between 2 and {MOORE_BOUND}")
    raw_edges = np.asarray(value.get("edges"))
    if raw_edges.ndim != 2 or raw_edges.shape[1] != 2:
        raise ValueError("edges must be a sequence of vertex pairs")
    try:
        numeric = raw_edges.astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("edge endpoints must be numeric integers") from exc
    if not np.all(np.isfinite(numeric)) or not np.array_equal(
        numeric, np.rint(numeric)
    ):
        raise ValueError("edge endpoints must be finite integers")

    adjacency = [set() for _ in range(n)]
    seen: set[tuple[int, int]] = set()
    for raw_left, raw_right in numeric.astype(np.int64):
        left, right = int(raw_left), int(raw_right)
        if not 0 <= left < n or not 0 <= right < n:
            raise ValueError("an edge endpoint is outside the vertex range")
        if left == right:
            raise ValueError("self-loops are not allowed")
        edge = (min(left, right), max(left, right))
        if edge in seen:
            raise ValueError("duplicate undirected edges are not allowed")
        seen.add(edge)
        adjacency[left].add(right)
        adjacency[right].add(left)

    degree_excess = sum(
        max(0, len(neighbors) - MAXIMUM_DEGREE) for neighbors in adjacency
    )
    unreachable_pairs = 0
    distance_excess = 0
    observed_diameter = 0
    for source in range(n):
        distances = [-1] * n
        distances[source] = 0
        queue = deque([source])
        while queue:
            vertex = queue.popleft()
            for neighbor in adjacency[vertex]:
                if distances[neighbor] == -1:
                    distances[neighbor] = distances[vertex] + 1
                    queue.append(neighbor)
        for target in range(source + 1, n):
            distance = distances[target]
            if distance == -1:
                unreachable_pairs += 1
            else:
                observed_diameter = max(observed_diameter, distance)
                distance_excess += max(0, distance - MAXIMUM_DIAMETER)

    feasible = degree_excess == 0 and unreachable_pairs == 0 and distance_excess == 0
    score = (
        float(n)
        if feasible
        else -1_000_000.0
        - 10_000.0 * degree_excess
        - 100.0 * unreachable_pairs
        - distance_excess
    )
    return (
        score,
        {
            "num_vertices": n,
            "num_edges": len(seen),
            "maximum_degree": max(map(len, adjacency), default=0),
            "observed_finite_diameter": observed_diameter,
            "degree_excess": degree_excess,
            "unreachable_pairs": unreachable_pairs,
            "distance_excess": distance_excess,
            "is_feasible": feasible,
            "published_2024_record": PUBLISHED_2024_RECORD,
            "moore_bound": MOORE_BOUND,
        },
        feasible,
    )


def _load(repo_path: Path):
    source = repo_path / "solution.py"
    spec = importlib.util.spec_from_file_location("candidate_solution", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(repo_path: Path) -> tuple[dict[str, Any], bool, str]:
    try:
        score, public, feasible = assess(_load(repo_path).construct_graph())
        feedback = (
            "" if feasible else "graph violates the degree or diameter constraints"
        )
        return (
            {
                "combined_score": score,
                "public": public,
                "text_feedback": feedback,
            },
            feasible,
            feedback,
        )
    except Exception as exc:
        return (
            {"combined_score": -2_000_000.0, "public": {}, "text_feedback": str(exc)},
            False,
            str(exc),
        )


def main(repo_path: str, results_dir: str) -> None:
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    metrics, correct, error = evaluate(Path(repo_path).resolve())
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (output / "correct.json").write_text(
        json.dumps({"correct": correct, "error": error}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    main(args.repo_path, args.results_dir)
