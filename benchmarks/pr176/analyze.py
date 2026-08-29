#!/usr/bin/env python3
"""Summarize paired PR #176 benchmark runs from their SQLite artifacts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any, Iterable, Optional


RUN_RE = re.compile(r"^(baseline|treatment)-r([1-9][0-9]*)$")


def _median(values: Iterable[float]) -> Optional[float]:
    materialized = list(values)
    return statistics.median(materialized) if materialized else None


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return numerator / denominator if denominator else None


def _metadata(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _selection_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "inspiration_selections.jsonl"
    if not path.exists():
        return []
    events = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def summarize_run(run_dir: Path) -> dict[str, Any]:
    database = run_dir / "programs.sqlite"
    if not database.exists():
        raise FileNotFoundError(f"Missing run database: {database}")
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, parent_id, generation, combined_score, correct, metadata
            FROM programs
            ORDER BY generation, timestamp, id
            """
        ).fetchall()

    programs = [
        {
            "id": str(row["id"]),
            "parent_id": row["parent_id"],
            "generation": int(row["generation"]),
            "score": float(row["combined_score"] or 0.0),
            "correct": bool(row["correct"]),
            "metadata": _metadata(row["metadata"]),
        }
        for row in rows
    ]
    if not programs:
        raise ValueError(f"Run database contains no programs: {database}")

    by_id = {program["id"]: program for program in programs}
    evolved = [program for program in programs if program["generation"] > 0]
    correct_programs = [program for program in programs if program["correct"]]
    max_generation = max(program["generation"] for program in programs)
    scores_by_generation: dict[int, list[float]] = {}
    for program in correct_programs:
        scores_by_generation.setdefault(program["generation"], []).append(
            program["score"]
        )

    running_best = 0.0
    best_curve: list[float] = []
    for generation in range(max_generation + 1):
        generation_scores = scores_by_generation.get(generation, [])
        if generation_scores:
            running_best = max(running_best, max(generation_scores))
        best_curve.append(running_best)

    crossover = [
        program
        for program in evolved
        if program["metadata"].get("patch_type") == "cross"
    ]
    all_cross_deltas: list[float] = []
    correct_cross_deltas: list[float] = []
    for program in crossover:
        parent = by_id.get(program["parent_id"])
        if parent is None:
            continue
        delta = program["score"] - parent["score"]
        all_cross_deltas.append(delta)
        if program["correct"]:
            correct_cross_deltas.append(delta)

    events = _selection_events(run_dir)
    clean_events = [
        event
        for event in events
        if int(event.get("usable_embedding_count") or 0) >= 2
        and event.get("fallback_reason") is None
    ]
    selected_distances = [
        float(event["selected_distance"])
        for event in clean_events
        if isinstance(event.get("selected_distance"), (int, float))
        and math.isfinite(float(event["selected_distance"]))
    ]
    distance_lifts = [
        float(event["selected_distance"]) - float(event["random_draw_distance"])
        for event in clean_events
        if isinstance(event.get("selected_distance"), (int, float))
        and isinstance(event.get("random_draw_distance"), (int, float))
        and math.isfinite(float(event["selected_distance"]))
        and math.isfinite(float(event["random_draw_distance"]))
    ]
    failed_proposals = sum(
        program["metadata"].get("node_kind") == "failed_proposal" for program in evolved
    )
    estimated_cost = sum(
        float(program["metadata"].get(key) or 0.0)
        for program in evolved
        for key in ("api_costs", "embed_cost", "novelty_cost")
    )

    return {
        "run_dir": str(run_dir),
        "max_generation": max_generation,
        "program_count": len(programs),
        "evolved_program_count": len(evolved),
        "verified_best_score": max(
            (program["score"] for program in correct_programs), default=0.0
        ),
        "best_so_far_auc": statistics.fmean(best_curve),
        "correct_rate": _rate(sum(p["correct"] for p in evolved), len(evolved)),
        "proposal_persist_success_rate": _rate(
            len(evolved) - failed_proposals,
            len(evolved),
        ),
        "crossover_program_count": len(crossover),
        "crossover_correct_rate": _rate(
            sum(program["correct"] for program in crossover),
            len(crossover),
        ),
        "crossover_child_parent_delta_median": _median(all_cross_deltas),
        "correct_crossover_child_parent_delta_median": _median(correct_cross_deltas),
        "selection_event_count": len(events),
        "clean_selection_event_count": len(clean_events),
        "selection_fallback_count": sum(
            event.get("fallback_reason") is not None for event in events
        ),
        "selected_distance_median": _median(selected_distances),
        "selected_distance_lift_over_random_median": _median(distance_lifts),
        "estimated_api_cost": estimated_cost,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _markdown(
    runs: dict[str, dict[int, dict[str, Any]]], comparison: dict[str, Any]
) -> str:
    lines = [
        "# PR #176 benchmark comparison",
        "",
        "| Arm | Repeat | Best | Best-so-far AUC | Correct | Cross decisions | Clean decisions | Selected distance | Distance lift | Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("baseline", "treatment"):
        for repeat, summary in sorted(runs.get(arm, {}).items()):
            lines.append(
                "| "
                + " | ".join(
                    [
                        arm,
                        str(repeat),
                        _fmt(summary["verified_best_score"]),
                        _fmt(summary["best_so_far_auc"]),
                        _fmt(summary["correct_rate"]),
                        str(summary["selection_event_count"]),
                        str(summary["clean_selection_event_count"]),
                        _fmt(summary["selected_distance_median"]),
                        _fmt(summary["selected_distance_lift_over_random_median"]),
                        _fmt(summary["estimated_api_cost"]),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            f"Matched repeats: {comparison['matched_repeat_count']}",
            f"Median treatment - baseline best: {_fmt(comparison['median_best_delta'])}",
            f"Median treatment - baseline AUC: {_fmt(comparison['median_auc_delta'])}",
            f"Direction agreement (best/AUC): {comparison['best_positive_pairs']}/{comparison['auc_positive_pairs']}",
            f"Claim ready: {comparison['claim_ready']}",
        ]
    )
    if comparison["claim_blockers"]:
        lines.append("Blockers: " + "; ".join(comparison["claim_blockers"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    args = parser.parse_args()
    results_root = args.results_root.resolve()

    runs: dict[str, dict[int, dict[str, Any]]] = {
        "baseline": {},
        "treatment": {},
    }
    for path in sorted(results_root.iterdir()):
        match = RUN_RE.fullmatch(path.name)
        if match and path.is_dir():
            arm, repeat_text = match.groups()
            runs[arm][int(repeat_text)] = summarize_run(path)

    matched = sorted(set(runs["baseline"]) & set(runs["treatment"]))
    pair_deltas = []
    for repeat in matched:
        baseline = runs["baseline"][repeat]
        treatment = runs["treatment"][repeat]
        pair_deltas.append(
            {
                "repeat": repeat,
                "best_delta": treatment["verified_best_score"]
                - baseline["verified_best_score"],
                "auc_delta": treatment["best_so_far_auc"] - baseline["best_so_far_auc"],
                "correct_rate_delta": (
                    treatment["correct_rate"] - baseline["correct_rate"]
                    if treatment["correct_rate"] is not None
                    and baseline["correct_rate"] is not None
                    else None
                ),
            }
        )

    median_best_delta = _median(pair["best_delta"] for pair in pair_deltas)
    median_auc_delta = _median(pair["auc_delta"] for pair in pair_deltas)
    median_correct_delta = _median(
        pair["correct_rate_delta"]
        for pair in pair_deltas
        if pair["correct_rate_delta"] is not None
    )
    best_positive = sum(pair["best_delta"] > 0 for pair in pair_deltas)
    auc_positive = sum(pair["auc_delta"] > 0 for pair in pair_deltas)
    clean_totals = {
        arm: sum(
            summary["clean_selection_event_count"] for summary in arm_runs.values()
        )
        for arm, arm_runs in runs.items()
    }

    blockers = []
    if len(matched) < 3:
        blockers.append("fewer than 3 matched repeats")
    if median_best_delta is None or median_best_delta <= 0:
        blockers.append("median final-best delta is not positive")
    if median_auc_delta is None or median_auc_delta <= 0:
        blockers.append("median best-so-far AUC delta is not positive")
    if best_positive < 2 or auc_positive < 2:
        blockers.append("fewer than 2 matched pairs improve both headline directions")
    if median_correct_delta is not None and median_correct_delta < -0.05:
        blockers.append("correctness rate falls by more than 5 percentage points")
    if clean_totals["baseline"] < 30 or clean_totals["treatment"] < 30:
        blockers.append("fewer than 30 clean crossover decisions in an arm")

    comparison = {
        "matched_repeat_count": len(matched),
        "pairs": pair_deltas,
        "median_best_delta": median_best_delta,
        "median_auc_delta": median_auc_delta,
        "median_correct_rate_delta": median_correct_delta,
        "best_positive_pairs": best_positive,
        "auc_positive_pairs": auc_positive,
        "clean_selection_events": clean_totals,
        "claim_ready": not blockers,
        "claim_blockers": blockers,
    }
    payload = {"runs": runs, "comparison": comparison}
    json_path = results_root / "comparison.json"
    markdown_path = results_root / "comparison.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown = _markdown(runs, comparison)
    markdown_path.write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    print(f"\nWrote {json_path} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
