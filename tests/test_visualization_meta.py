import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

from shinka.database import DatabaseConfig, ProgramDatabase

markdown_stub = ModuleType("markdown")
setattr(markdown_stub, "markdown", lambda text: text)
sys.modules.setdefault("markdown", markdown_stub)


def _handler_cls():
    from shinka.webui.visualization import DatabaseRequestHandler

    return DatabaseRequestHandler


def _make_handler(search_root: Path):
    handler_cls = _handler_cls()
    handler = handler_cls.__new__(handler_cls)
    handler.search_root = str(search_root)
    handler._get_actual_db_path = lambda db_path: db_path
    handler.send_response = lambda code: None
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda: None
    handler.wfile = None
    return handler


def _create_program_database(path: Path) -> None:
    database = ProgramDatabase(
        DatabaseConfig(db_path=str(path), num_islands=1),
        embedding_model=None,
    )
    database.close()


def _record_failed_proposal(
    db_path: Path,
    *,
    generation: int = 7,
    details: dict | None = None,
) -> None:
    payload = {
        "node_kind": "failed_proposal",
        "failure_stage": "proposal",
        "failure_class": "proposal_generation_failed",
        "failure_reason": "proposal failed",
        "parent_id": "parent-1",
        "failure_json_path": f"results/gen_{generation}/failure.json",
        "pipeline_started_at": 100.0,
        "sampling_started_at": 100.0,
        "sampling_finished_at": 105.0,
        "evaluation_started_at": 105.0,
        "evaluation_finished_at": 105.0,
        "postprocess_started_at": 105.0,
        "postprocess_finished_at": 105.0,
    }
    payload.update(details or {})
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO attempt_log
                (generation, stage, status, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (generation, "proposal", "failed", json.dumps(payload), 123.0),
        )


def test_handle_get_meta_files_returns_processed_counts(tmp_path):
    results_dir = tmp_path / "results"
    meta_dir = results_dir / "meta"
    meta_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    db_path.write_text("", encoding="utf-8")
    (meta_dir / "meta_5.txt").write_text("first", encoding="utf-8")
    (meta_dir / "meta_60.txt").write_text("latest", encoding="utf-8")

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_meta_files("results/programs.sqlite")

    assert "error" not in sent
    assert sent["data"] == [
        {
            "processed_count": 5,
            "generation": 5,
            "filename": "meta_5.txt",
            "path": str(meta_dir / "meta_5.txt"),
        },
        {
            "processed_count": 60,
            "generation": 60,
            "filename": "meta_60.txt",
            "path": str(meta_dir / "meta_60.txt"),
        },
    ]


def test_handle_get_meta_content_returns_processed_count(tmp_path):
    results_dir = tmp_path / "results"
    meta_dir = results_dir / "meta"
    meta_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    db_path.write_text("", encoding="utf-8")
    (meta_dir / "meta_60.txt").write_text(
        "# META RECOMMENDATIONS",
        encoding="utf-8",
    )

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_meta_content("results/programs.sqlite", "60")

    assert "error" not in sent
    assert sent["data"] == {
        "processed_count": 60,
        "generation": 60,
        "filename": "meta_60.txt",
        "content": "# META RECOMMENDATIONS",
    }


def test_handle_get_programs_summary_merges_failed_attempt_nodes(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    _create_program_database(db_path)

    failure_path = results_dir / "gen_7" / "failure.json"
    failure_path.parent.mkdir(parents=True)
    failure_path.write_text(
        json.dumps(
            {
                "failure_reason": "proposal failed",
                "failure_json_path": "results/gen_7/failure.json",
            }
        ),
        encoding="utf-8",
    )
    _record_failed_proposal(db_path)

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_programs_summary("results/programs.sqlite")

    assert "error" not in sent
    assert len(sent["data"]) == 1
    failed_node = sent["data"][0]
    assert failed_node["id"] == "failed:proposal:7"
    assert failed_node["parent_id"] == "parent-1"
    assert failed_node["repo_summary"] is None
    assert failed_node["metadata"]["node_kind"] == "failed_proposal"
    assert failed_node["text_feedback"] == "proposal failed"
    assert failed_node["metadata"]["sampling_started_at"] == 100.0
    assert failed_node["metadata"]["postprocess_finished_at"] == 105.0


def test_handle_get_program_details_returns_failed_repo_summary(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    _create_program_database(db_path)

    summary = "# Individual Summary\n\nFailed candidate summary.\n"
    failure_path = results_dir / "gen_7" / "failure.json"
    failure_path.parent.mkdir(parents=True)
    failure_path.write_text(
        json.dumps(
            {
                "repo_summary": summary,
                "failure_reason": "proposal failed",
                "failure_json_path": "results/gen_7/failure.json",
            }
        ),
        encoding="utf-8",
    )
    _record_failed_proposal(db_path)

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_program_details(
        "results/programs.sqlite",
        "failed:proposal:7",
    )

    assert "error" not in sent
    assert sent["data"]["id"] == "failed:proposal:7"
    assert sent["data"]["repo_summary"] == summary
    assert sent["data"]["metadata"]["failure_class"] == (
        "proposal_generation_failed"
    )
    assert sent["data"]["metadata"]["pipeline_started_at"] == 100.0
    assert sent["data"]["metadata"]["postprocess_finished_at"] == 105.0


def _create_stats_database(db_path: Path, rows: list[tuple]) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE programs (
                id TEXT PRIMARY KEY,
                repo_summary TEXT,
                generation INTEGER,
                correct INTEGER,
                combined_score REAL,
                timestamp REAL,
                metadata TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO programs VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_handle_get_database_stats_uses_best_correct_program(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    _create_stats_database(
        db_path,
        [
            (
                "incorrect-best",
                "# Incorrect\n",
                5,
                0,
                10.0,
                200.0,
                '{"pipeline_started_at": 100.0, "postprocess_finished_at": 200.0}',
            ),
            (
                "correct-best",
                "# Correct\n",
                2,
                1,
                3.5,
                150.0,
                '{"pipeline_started_at": 110.0, "postprocess_finished_at": 150.0}',
            ),
        ],
    )

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_database_stats("results/programs.sqlite")

    assert "error" not in sent
    assert sent["data"]["generation_count"] == 2
    assert sent["data"]["best_generation"] == 2
    assert sent["data"]["max_generation"] == 5
    assert sent["data"]["correct_count"] == 1
    assert sent["data"]["best_score"] == 3.5
    assert sent["data"]["gens_since_improvement"] == 3


def test_handle_get_database_stats_returns_no_best_when_no_correct_programs(
    tmp_path,
):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    db_path = results_dir / "programs.sqlite"
    _create_stats_database(
        db_path,
        [
            ("p1", "# One\n", 1, 0, 2.0, 100.0, "{}"),
            ("p2", "# Two\n", 4, 0, 9.0, 130.0, "{}"),
        ],
    )

    handler = _make_handler(tmp_path)
    sent = {}
    handler.send_json_response = lambda data: sent.setdefault("data", data)
    handler.send_error = lambda code, msg: sent.setdefault("error", (code, msg))

    handler.handle_get_database_stats("results/programs.sqlite")

    assert "error" not in sent
    assert sent["data"]["generation_count"] == 2
    assert sent["data"]["best_generation"] is None
    assert sent["data"]["max_generation"] == 4
    assert sent["data"]["correct_count"] == 0
    assert sent["data"]["best_score"] is None
    assert sent["data"]["gens_since_improvement"] == 4
