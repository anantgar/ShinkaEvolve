from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from shinka.core import EvolutionConfig
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig

REPO_ROOT = Path(__file__).parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


@pytest.mark.parametrize("skill_name", ["shinka-setup", "shinka-convert"])
def test_repo_task_templates_match_current_config(skill_name: str) -> None:
    scripts_dir = SKILLS_ROOT / skill_name / "scripts"
    config = yaml.safe_load((scripts_dir / "shinka.yaml").read_text(encoding="utf-8"))

    assert set(config) == {
        "max_evaluation_jobs",
        "max_proposal_jobs",
        "max_db_workers",
        "verbose",
        "evo",
        "db",
        "job",
    }
    assert not (set(config["evo"]) - {field.name for field in fields(EvolutionConfig)})
    assert not (set(config["db"]) - {field.name for field in fields(DatabaseConfig)})
    assert not (set(config["job"]) - {field.name for field in fields(LocalJobConfig)})

    evo_config = EvolutionConfig(**config["evo"])
    db_config = DatabaseConfig(**config["db"])
    job_config = LocalJobConfig(**config["job"])

    assert evo_config.seed_repo_path == "seed_repo"
    assert evo_config.mutable_paths == []
    assert evo_config.generation_target_mode == "evaluated_candidates"
    assert evo_config.llm_models
    assert all(model.startswith("headless/") for model in evo_config.llm_models)
    assert db_config.num_islands == 1
    assert job_config.eval_program_path == "evaluate.py"

    compile(
        (scripts_dir / "run_evo.py").read_text(encoding="utf-8"),
        str(scripts_dir / "run_evo.py"),
        "exec",
    )


@pytest.mark.parametrize(
    "skill_name", ["shinka-setup", "shinka-convert", "shinka-run", "shinka-inspect"]
)
def test_skill_docs_use_repo_individual_contract(skill_name: str) -> None:
    content = (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
    assert "repo" in content.lower()
    assert "--program_path" not in content
    assert "init_program_path" not in content
    assert "EVOLVE-BLOCK-START" not in content


@pytest.mark.parametrize(
    "skill_name", ["shinka-setup", "shinka-convert", "shinka-run"]
)
def test_task_lifecycle_skills_describe_automatic_seed_git_initialization(
    skill_name: str,
) -> None:
    content = (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
    assert "Shinka initializes" in content
    assert "existing" in content.lower()
    assert "clean" in content.lower()
    assert "git -C seed_repo init" not in content


def _create_inspection_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE programs (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                generation INTEGER,
                combined_score REAL,
                correct INTEGER,
                repo_commit TEXT,
                repo_parent_commit TEXT,
                repo_diff TEXT,
                repo_summary TEXT,
                summary_version TEXT,
                changed_files TEXT,
                artifact_uri TEXT,
                public_metrics TEXT,
                private_metrics TEXT,
                text_feedback TEXT,
                archive_inspiration_ids TEXT,
                top_k_inspiration_ids TEXT,
                agent_provider TEXT,
                agent_model TEXT,
                agent_session_id TEXT,
                code TEXT
            )
            """
        )
        rows = [
            (
                "best-candidate",
                "seed",
                2,
                2.5,
                1,
                "b" * 40,
                "a" * 40,
                "diff --git a/src/core.py b/src/core.py\n+fast path\n-old path\n",
                """# Individual Summary

## Core Idea

Add a vectorized fast path.

## Changed Files

- src/core.py

## Validation Performed

Ran correctness and latency checks.

## Performance Hypothesis

Fewer interpreter transitions should reduce latency.

## Risks and Followups

- Check large input memory use.
""",
                "repo-individual-v1",
                '["src/core.py"]',
                "/tmp/best",
                '{"latency_ms": 3.2}',
                '{"secret_metric": 999}',
                "Public evaluator feedback.",
                "[]",
                "[]",
                "headless",
                "codex",
                "session-best",
                "compatibility summary",
            ),
            (
                "second-candidate",
                "seed",
                1,
                1.75,
                1,
                "c" * 40,
                "a" * 40,
                "",
                """# Individual Summary

## Core Idea

Cache normalized inputs.

## Performance Hypothesis

Repeated calls avoid duplicate parsing.

## Risks and Followups

- Bound cache growth.
""",
                "repo-individual-v1",
                '["src/core.py", "src/cache.py"]',
                "/tmp/second",
                '{"latency_ms": 4.1}',
                '{"secret_metric": 123}',
                "",
                "[]",
                "[]",
                "headless",
                "cursor",
                "session-second",
                "compatibility summary",
            ),
            (
                "incorrect-high-score",
                "seed",
                3,
                100.0,
                0,
                "d" * 40,
                "a" * 40,
                "",
                "# Individual Summary\n\n## Core Idea\n\nBreak correctness.",
                "repo-individual-v1",
                '["src/core.py"]',
                "/tmp/incorrect",
                '{"latency_ms": 0.1}',
                '{"secret_metric": 1000}',
                "Incorrect.",
                "[]",
                "[]",
                "headless",
                "codex",
                "session-bad",
                "compatibility summary",
            ),
        ]
        connection.executemany(
            """
            INSERT INTO programs VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )


def test_inspect_skill_builds_summary_based_context(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    database = results_dir / "programs.sqlite"
    _create_inspection_db(database)
    output = tmp_path / "context.md"
    script = SKILLS_ROOT / "shinka-inspect" / "scripts" / "inspect_best_programs.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--results-dir",
            str(results_dir),
            "--k",
            "2",
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    content = output.read_text(encoding="utf-8")
    assert "Selection mode: `top-correct`" in content
    assert "Add a vectorized fast path." in content
    assert "Cache normalized inputs." in content
    assert "Most common changed paths: `src/core.py` (2)" in content
    assert "Public evaluator feedback." in content
    assert "incorrect-high-score" not in content
    assert "secret_metric" not in content
    assert "compatibility summary" not in content


def test_inspect_skill_labels_no_correct_fallback(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    database = results_dir / "programs.sqlite"
    _create_inspection_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE programs SET correct = 0")
    output = tmp_path / "fallback.md"
    script = SKILLS_ROOT / "shinka-inspect" / "scripts" / "inspect_best_programs.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--results-dir",
            str(results_dir),
            "--k",
            "1",
            "--out",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    content = output.read_text(encoding="utf-8")
    assert "Selection mode: `top-all-fallback-no-correct`" in content
    assert "Warning: no correct individuals matched" in content
    assert "incorrect-high-score" in content
