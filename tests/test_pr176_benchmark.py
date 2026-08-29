from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from benchmarks.pr176.analyze import summarize_run
from shinka.core import EvolutionConfig
from shinka.database import DatabaseConfig
from shinka.launch import SecureDockerJobConfig


ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "benchmarks" / "pr176" / "circle_packing"


def test_pr176_config_is_a_controlled_cheap_model_comparison():
    config = yaml.safe_load((TASK / "benchmark.yaml").read_text(encoding="utf-8"))

    assert config["evo"]["llm_models"] == ["gemini-2.5-flash-lite"]
    assert config["evo"]["embedding_model"] == "gemini-embedding-001"
    assert config["evo"]["novelty_llm_models"] is None
    assert config["evo"]["crossover_inspiration_selection"] == "random"
    assert config["evo"]["patch_type_probs"] == [0.45, 0.45, 0.10]
    assert config["db"]["num_archive_inspirations"] == 4
    assert config["db"]["num_top_k_inspirations"] == 2
    assert config["max_proposal_jobs"] == 1
    assert EvolutionConfig(**config["evo"]).random_seed is None
    assert DatabaseConfig(**config["db"]).archive_size == 40
    assert SecureDockerJobConfig(**config["job"]).cpus == 1.0


def test_frozen_circle_packing_seed_passes_evaluator(tmp_path: Path):
    subprocess.run(
        [
            sys.executable,
            str(TASK / "evaluate.py"),
            "--program_path",
            str(TASK / "initial.py"),
            "--results_dir",
            str(tmp_path),
        ],
        check=True,
    )

    correct = json.loads((tmp_path / "correct.json").read_text(encoding="utf-8"))
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert correct == {"correct": True, "error": None}
    assert metrics["combined_score"] > 0


def test_pr176_analyzer_reads_scores_and_selection_audit(tmp_path: Path):
    database = tmp_path / "programs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE programs (
                id TEXT,
                parent_id TEXT,
                generation INTEGER,
                timestamp REAL,
                combined_score REAL,
                correct INTEGER,
                metadata TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO programs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("seed", None, 0, 0.0, 1.0, 1, "{}"),
                (
                    "child",
                    "seed",
                    1,
                    1.0,
                    1.2,
                    1,
                    json.dumps({"patch_type": "cross", "api_costs": 0.01}),
                ),
            ],
        )
    (tmp_path / "inspiration_selections.jsonl").write_text(
        json.dumps(
            {
                "usable_embedding_count": 2,
                "fallback_reason": None,
                "selected_distance": 0.75,
                "random_draw_distance": 0.25,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_run(tmp_path)

    assert summary["verified_best_score"] == 1.2
    assert summary["best_so_far_auc"] == 1.1
    assert summary["crossover_child_parent_delta_median"] == pytest.approx(0.2)
    assert summary["clean_selection_event_count"] == 1
    assert summary["selected_distance_median"] == 0.75
    assert summary["selected_distance_lift_over_random_median"] == 0.5
