import asyncio
import subprocess
from pathlib import Path

from shinka.database import DatabaseConfig, Program, ProgramDatabase
from shinka.database.async_dbase import AsyncProgramDatabase
from shinka.repo.complexity import analyze_repository_complexity


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_fixture_repo(repo_path: Path) -> None:
    _git(repo_path, "init")
    _git(repo_path, "add", "-A")
    _git(
        repo_path,
        "-c",
        "user.name=Shinka Test",
        "-c",
        "user.email=shinka@example.invalid",
        "commit",
        "-m",
        "fixture",
    )


def test_repository_complexity_analyzes_only_mutable_supported_source_files(
    tmp_path: Path,
):
    (tmp_path / "src" / "vendor").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / ".shinka").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "logic.py").write_text(
        """def choose(value):
    if value > 0:
        return value
    return -value
""",
        encoding="utf-8",
    )
    (tmp_path / "src" / "worker.go").write_text(
        """package worker

func score(value int) int {
    if value > 0 {
        return value
    }
    return -value
}
""",
        encoding="utf-8",
    )
    (tmp_path / "src" / "immutable.py").write_text(
        "def retained():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "client.ts").write_text(
        "export const value = 1;\n", encoding="utf-8"
    )
    (tmp_path / "src" / "parser_generated.py").write_text(
        "def generated():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "vendor" / "copied.py").write_text(
        "def copied():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "binary.py").write_bytes(b"\0not python")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / ".shinka" / "individual.md").write_text("# Summary\n", encoding="utf-8")
    (tmp_path / "tests" / "test_logic.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )
    _commit_fixture_repo(tmp_path)

    analysis = analyze_repository_complexity(
        tmp_path,
        mutable_paths=["src"],
        immutable_paths=["src/immutable.py"],
    )

    assert analysis["status"] == "ok"
    assert [item["path"] for item in analysis["files"]] == [
        "src/logic.py",
        "src/worker.go",
    ]
    assert analysis["unsupported_files"] == [
        {"path": "src/client.ts", "language": "typescript"}
    ]
    assert analysis["skipped_files"] == [{"path": "src/binary.py", "reason": "binary"}]

    coverage = analysis["coverage"]
    assert coverage["analyzed_file_count"] == 2
    assert coverage["unsupported_file_count"] == 1
    assert coverage["skipped_file_count"] == 1
    assert coverage["excluded_file_counts"]["immutable"] == 1
    assert coverage["excluded_file_counts"]["generated_or_vendor"] == 2
    assert coverage["excluded_file_counts"]["outside_mutable_paths"] == 2

    metrics = [item["metrics"] for item in analysis["files"]]
    aggregate = analysis["aggregate"]
    assert aggregate["lines_of_code"] == sum(
        metric["lines_of_code"] for metric in metrics
    )
    assert aggregate["cyclomatic_complexity"] == sum(
        metric["cyclomatic_complexity"] for metric in metrics
    )
    assert aggregate["complexity_blocks"] == sum(
        metric["complexity_blocks"] for metric in metrics
    )
    assert aggregate["complexity_score"] < 1.0
    assert (
        aggregate["max_file_complexity_score"] >= aggregate["p95_file_complexity_score"]
    )


def test_repository_complexity_empty_mutable_scope_includes_test_sources(
    tmp_path: Path,
):
    (tmp_path / "tests").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "vendor" / "copied.py").write_text(
        "def copied():\n    return 1\n", encoding="utf-8"
    )
    _commit_fixture_repo(tmp_path)
    (tmp_path / "tests" / "candidate_added.py").write_text(
        "def candidate_added():\n    return 1\n", encoding="utf-8"
    )

    analysis = analyze_repository_complexity(tmp_path)

    assert [item["path"] for item in analysis["files"]] == [
        "tests/candidate_added.py",
        "tests/test_example.py",
    ]
    assert analysis["coverage"]["excluded_file_counts"]["generated_or_vendor"] == 1


def test_sync_database_never_analyzes_repo_summary(monkeypatch, tmp_path: Path):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("repo summaries must not be analyzed as source")

    monkeypatch.setattr("shinka.database.dbase.analyze_code_metrics", fail_if_called)
    db = ProgramDatabase(
        DatabaseConfig(db_path=str(tmp_path / "programs.sqlite"), num_islands=1),
        embedding_model=None,
    )
    try:
        repo = Program(
            id="repo-sync",
            code="# Individual Summary\n",
            language="repo",
            repo_commit="abc123",
            repo_summary="# Individual Summary\n",
            metadata={"repo_complexity": {"status": "ok", "aggregate": {}}},
        )
        db.add(repo)

        loaded = db.get(repo.id)
        assert loaded is not None
        assert loaded.complexity == 0.0
        assert "code_analysis_metrics" not in loaded.metadata
    finally:
        db.close()


def test_async_database_never_analyzes_repo_summary(monkeypatch, tmp_path: Path):
    async def run() -> None:
        db = ProgramDatabase(
            DatabaseConfig(db_path=str(tmp_path / "programs.sqlite"), num_islands=1),
            embedding_model=None,
        )
        async_db = AsyncProgramDatabase(db)
        try:
            repo = Program(
                id="repo-async",
                code="# Individual Summary\n",
                language="repo",
                repo_commit="abc123",
                repo_summary="# Individual Summary\n",
                metadata={"repo_complexity": {"status": "ok", "aggregate": {}}},
            )
            await async_db.add_program_async(repo)

            loaded = db.get(repo.id)
            assert loaded is not None
            assert loaded.complexity == 0.0
            assert "code_analysis_metrics" not in loaded.metadata
        finally:
            await async_db.close_async()
            db.close()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("repo summaries must not be analyzed as source")

    monkeypatch.setattr(
        "shinka.database.async_dbase.analyze_code_metrics", fail_if_called
    )
    asyncio.run(run())


def test_repository_complexity_is_used_by_archive_selection(tmp_path: Path):
    db = ProgramDatabase(
        DatabaseConfig(db_path=str(tmp_path / "programs.sqlite"), num_islands=1),
        embedding_model=None,
    )
    try:
        more_complex = Program(
            id="more-complex",
            language="repo",
            repo_commit="more-complex-commit",
            repo_summary="# Individual Summary\n",
            correct=True,
            combined_score=1.0,
            metadata={
                "repo_complexity": {
                    "status": "ok",
                    "aggregate": {"cyclomatic_complexity": 100},
                }
            },
        )
        simpler = Program(
            id="simpler",
            language="repo",
            repo_commit="simpler-commit",
            repo_summary="# Individual Summary\n",
            correct=True,
            combined_score=1.0,
            metadata={
                "repo_complexity": {
                    "status": "ok",
                    "aggregate": {"cyclomatic_complexity": 10},
                }
            },
        )

        assert db._get_criterion_value(simpler, "complexity") == 10
        assert db._is_better(simpler, more_complex, [more_complex])
    finally:
        db.close()
