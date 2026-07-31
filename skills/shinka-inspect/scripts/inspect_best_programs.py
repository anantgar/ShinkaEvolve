#!/usr/bin/env python3
"""Build an agent context bundle from top repo-backed Shinka individuals."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_K = 5
DEFAULT_MAX_SUMMARY_CHARS = 6000

LIGHT_COLUMNS = (
    "id",
    "parent_id",
    "generation",
    "combined_score",
    "correct",
    "repo_commit",
    "repo_parent_commit",
    "repo_summary",
    "summary_version",
    "changed_files",
    "artifact_uri",
    "public_metrics",
    "text_feedback",
    "archive_inspiration_ids",
    "top_k_inspiration_ids",
    "agent_provider",
    "agent_model",
    "agent_session_id",
)


@dataclass(frozen=True)
class InspectConfig:
    results_dir: str
    k: int
    out: str | None
    max_summary_chars: int
    min_generation: int | None
    include_feedback: bool


def _parse_args(argv: list[str] | None = None) -> InspectConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Rank repo-backed Shinka individuals and export their summaries, "
            "lineage, public metrics, and cross-candidate insights as Markdown."
        )
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Path to a Shinka results directory or directly to its SQLite DB.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_K,
        help=f"Number of individuals to include (default: {DEFAULT_K}).",
    )
    parser.add_argument("--out", default=None, help="Output Markdown path.")
    parser.add_argument(
        "--max-summary-chars",
        dest="max_summary_chars",
        type=int,
        default=DEFAULT_MAX_SUMMARY_CHARS,
        help=(
            "Per-individual repository-summary cap "
            f"(default: {DEFAULT_MAX_SUMMARY_CHARS})."
        ),
    )
    parser.add_argument(
        "--min-generation",
        type=int,
        default=None,
        help="Optional minimum generation filter.",
    )
    parser.add_argument(
        "--include-feedback",
        dest="include_feedback",
        action="store_true",
        default=True,
        help="Include persisted evaluator feedback (default: enabled).",
    )
    parser.add_argument(
        "--no-include-feedback",
        dest="include_feedback",
        action="store_false",
        help="Omit persisted evaluator feedback.",
    )
    args = parser.parse_args(argv)
    if args.k <= 0:
        parser.error("--k must be positive")
    if args.max_summary_chars <= 0:
        parser.error("--max-summary-chars must be positive")
    return InspectConfig(
        results_dir=args.results_dir,
        k=args.k,
        out=args.out,
        max_summary_chars=args.max_summary_chars,
        min_generation=args.min_generation,
        include_feedback=args.include_feedback,
    )


def _resolve_db_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.suffix in {".sqlite", ".db"}:
        if not path.is_file():
            raise FileNotFoundError(f"Database does not exist: {path}")
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"Results directory does not exist: {path}")
    for name in ("programs.sqlite", "evolution_db.sqlite"):
        candidate = path / name
        if candidate.is_file():
            return candidate
    sqlite_files = sorted(path.glob("*.sqlite"))
    if sqlite_files:
        return sqlite_files[0]
    raise FileNotFoundError(f"No SQLite database found under: {path}")


def _output_path(config: InspectConfig, db_path: Path) -> Path:
    if config.out:
        return Path(config.out).expanduser().resolve()
    return db_path.parent / "shinka_inspect_context.md"


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _available_columns(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(programs)").fetchall()
    if not rows:
        raise ValueError("Database has no programs table.")
    return {str(row["name"]) for row in rows}


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _as_list(value: Any) -> list[str]:
    parsed = _json_value(value, [])
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _as_feedback(value: Any) -> str:
    parsed = _json_value(value, value)
    if isinstance(parsed, list):
        return "\n".join(str(item) for item in parsed)
    if isinstance(parsed, dict):
        return json.dumps(parsed, indent=2, sort_keys=True)
    return str(parsed or "")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _as_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return score


def _load_candidates(
    connection: sqlite3.Connection,
    *,
    min_generation: int | None,
) -> list[dict[str, Any]]:
    available = _available_columns(connection)
    required = {"id", "generation", "combined_score", "correct"}
    missing = sorted(required - available)
    if missing:
        raise ValueError(f"Programs table is missing columns: {', '.join(missing)}")
    columns = [column for column in LIGHT_COLUMNS if column in available]
    query = (
        "SELECT " + ", ".join(f'"{column}"' for column in columns) + " FROM programs"
    )
    params: tuple[Any, ...] = ()
    if min_generation is not None:
        query += ' WHERE "generation" >= ?'
        params = (min_generation,)
    records = [dict(row) for row in connection.execute(query, params).fetchall()]
    for record in records:
        record["score"] = _as_score(record.get("combined_score"))
        record["is_correct"] = _as_bool(record.get("correct"))
        record["changed_files_list"] = _as_list(record.get("changed_files"))
        record["public_metrics_dict"] = _as_dict(record.get("public_metrics"))
        record["archive_inspiration_ids_list"] = _as_list(
            record.get("archive_inspiration_ids")
        )
        record["top_k_inspiration_ids_list"] = _as_list(
            record.get("top_k_inspiration_ids")
        )
        record["feedback_text"] = _as_feedback(record.get("text_feedback"))
        record["summary_text"] = str(record.get("repo_summary") or "")
    return [record for record in records if record["score"] is not None]


def _select_top(
    candidates: list[dict[str, Any]], k: int
) -> tuple[list[dict[str, Any]], str]:
    if not candidates:
        raise ValueError("No scored individuals matched the requested filters.")
    correct = [candidate for candidate in candidates if candidate["is_correct"]]
    pool = correct or candidates
    mode = "top-correct" if correct else "top-all-fallback-no-correct"
    selected = sorted(
        pool,
        key=lambda item: (
            float(item["score"]),
            int(item.get("generation") or 0),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )[:k]
    return selected, mode


def _attach_repo_diffs(
    connection: sqlite3.Connection,
    selected: list[dict[str, Any]],
) -> None:
    if "repo_diff" not in _available_columns(connection):
        return
    by_id = {str(item["id"]): item for item in selected}
    placeholders = ", ".join("?" for _ in by_id)
    rows = connection.execute(
        f'SELECT "id", "repo_diff" FROM programs WHERE "id" IN ({placeholders})',
        tuple(by_id),
    ).fetchall()
    for row in rows:
        candidate = by_id.get(str(row["id"]))
        if candidate is not None:
            candidate["repo_diff_text"] = str(row["repo_diff"] or "")


def _diff_stats(diff: str) -> tuple[int, int]:
    additions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return additions, deletions


def _extract_section(summary: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        summary,
    )
    return match.group(1).strip() if match else ""


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n… truncated …", True


def _compact(text: str, max_chars: int = 700) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars].rstrip() + "…"


def _table_text(value: Any) -> str:
    return str(value if value not in {None, ""} else "—").replace("|", "\\|")


def _short(value: Any, length: int = 12) -> str:
    text = str(value or "—")
    return text if text == "—" else text[:length]


def _quote_markdown(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in text.splitlines()]


def _render_insights(selected: list[dict[str, Any]]) -> list[str]:
    scores = [float(item["score"]) for item in selected]
    changed_counter = Counter(
        path for item in selected for path in item["changed_files_list"]
    )
    lines = [
        "## Cross-candidate insights",
        "",
        (f"- Selected score range: `{min(scores):.6f}` to `{max(scores):.6f}`."),
        f"- Distinct direct parents represented: `{len({item.get('parent_id') for item in selected})}`.",
    ]
    if changed_counter:
        common = ", ".join(
            f"`{path}` ({count})" for path, count in changed_counter.most_common(8)
        )
        lines.append(f"- Most common changed paths: {common}.")
    lines.extend(["", "### Ideas and hypotheses", ""])
    for rank, item in enumerate(selected, start=1):
        summary = item["summary_text"]
        core_idea = _compact(_extract_section(summary, "Core Idea"))
        hypothesis = _compact(_extract_section(summary, "Performance Hypothesis"))
        risks = _compact(_extract_section(summary, "Risks and Followups"))
        lines.append(
            f"{rank}. `{_short(item.get('id'))}`"
            + (f" — {core_idea}" if core_idea else " — no core idea recorded")
        )
        if hypothesis:
            lines.append(f"   - Hypothesis: {hypothesis}")
        if risks:
            lines.append(f"   - Risks/follow-ups: {risks}")
    lines.append("")
    return lines


def _render_markdown(
    *,
    selected: list[dict[str, Any]],
    source_db: Path,
    output_path: Path,
    config: InspectConfig,
    selection_mode: str,
    total_candidates: int,
    total_correct: int,
) -> str:
    lines = [
        "# Shinka repository inspection context",
        "",
        "## Run metadata",
        "",
        f"- Generated UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Source database: `{source_db}`",
        f"- Selection mode: `{selection_mode}`",
        f"- Scored individuals loaded: `{total_candidates}`",
        f"- Correct individuals loaded: `{total_correct}`",
        f"- Individuals included: `{len(selected)}`",
        f"- Output: `{output_path}`",
    ]
    if config.min_generation is not None:
        lines.append(f"- Minimum generation: `{config.min_generation}`")
    lines.extend(
        [
            "",
            (
                "> Repository summaries are agent-authored context. Treat commits, "
                "diffs, and evaluator metrics as authoritative."
            ),
            "",
        ]
    )
    if selection_mode == "top-all-fallback-no-correct":
        lines.extend(
            [
                "> Warning: no correct individuals matched; ranking includes incorrect results.",
                "",
            ]
        )

    lines.extend(_render_insights(selected))
    lines.extend(
        [
            "## Ranking",
            "",
            "| Rank | ID | Gen | Score | Correct | Commit | Parent | Changed |",
            "|---:|---|---:|---:|:---:|---|---|---:|",
        ]
    )
    for rank, item in enumerate(selected, start=1):
        lines.append(
            "| "
            f"{rank} | {_short(item.get('id'))} | {_table_text(item.get('generation'))} | "
            f"{float(item['score']):.6f} | {'Y' if item['is_correct'] else 'N'} | "
            f"{_short(item.get('repo_commit'))} | {_short(item.get('parent_id'))} | "
            f"{len(item['changed_files_list'])} |"
        )
    lines.extend(["", "## Individual details", ""])

    for rank, item in enumerate(selected, start=1):
        diff_additions, diff_deletions = _diff_stats(item.get("repo_diff_text", ""))
        changed_files = item["changed_files_list"]
        summary, was_truncated = _truncate(
            item["summary_text"], config.max_summary_chars
        )
        lines.extend(
            [
                f"### {rank}. Individual `{item.get('id')}`",
                "",
                f"- Generation: `{item.get('generation')}`",
                f"- Combined score: `{float(item['score']):.6f}`",
                f"- Correct: `{'true' if item['is_correct'] else 'false'}`",
                f"- Repository commit: `{item.get('repo_commit') or 'not recorded'}`",
                f"- Repository parent commit: `{item.get('repo_parent_commit') or 'not recorded'}`",
                f"- Parent individual: `{item.get('parent_id') or 'root'}`",
                f"- Changed files: `{len(changed_files)}`; diff `+{diff_additions}/-{diff_deletions}`",
            ]
        )
        route = "/".join(
            part
            for part in (
                str(item.get("agent_provider") or ""),
                str(item.get("agent_model") or ""),
            )
            if part
        )
        if route:
            lines.append(f"- Agent route: `{route}`")
        if item.get("agent_session_id"):
            lines.append(f"- Agent session: `{item['agent_session_id']}`")
        if item.get("artifact_uri"):
            lines.append(f"- Artifact URI: `{item['artifact_uri']}`")
        if changed_files:
            lines.extend(["", "Changed paths:", ""])
            lines.extend(f"- `{path}`" for path in changed_files)
        public_metrics = item["public_metrics_dict"]
        if public_metrics:
            lines.extend(
                [
                    "",
                    "Public metrics:",
                    "",
                    "```json",
                    json.dumps(public_metrics, indent=2, sort_keys=True),
                    "```",
                ]
            )
        if config.include_feedback and item["feedback_text"].strip():
            feedback, _ = _truncate(item["feedback_text"].strip(), 2500)
            lines.extend(["", "Evaluator feedback:", "", "```text", feedback, "```"])
        lines.extend(["", "Repository summary:", ""])
        if was_truncated:
            lines.append(
                f"_Summary truncated to {config.max_summary_chars} characters._"
            )
            lines.append("")
        lines.extend(_quote_markdown(summary or "No repository summary recorded."))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_context(config: InspectConfig) -> Path:
    db_path = _resolve_db_path(config.results_dir)
    output_path = _output_path(config, db_path)
    with _connect_read_only(db_path) as connection:
        candidates = _load_candidates(connection, min_generation=config.min_generation)
        selected, selection_mode = _select_top(candidates, config.k)
        _attach_repo_diffs(connection, selected)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = _render_markdown(
        selected=selected,
        source_db=db_path,
        output_path=output_path,
        config=config,
        selection_mode=selection_mode,
        total_candidates=len(candidates),
        total_correct=sum(1 for item in candidates if item["is_correct"]),
    )
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    config = _parse_args(argv)
    output_path = build_context(config)
    print(f"Shinka inspection context written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
