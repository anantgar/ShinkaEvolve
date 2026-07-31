"""Async database smoke tests for repository individuals."""

import asyncio
from pathlib import Path

from shinka.database import DatabaseConfig, Program, ProgramDatabase
from shinka.database.async_dbase import AsyncProgramDatabase


def _program(prefix: str, index: int) -> Program:
    return Program(
        id=f"{prefix}-{index:04d}",
        repo_summary=f"# Repository {index}\n",
        generation=index,
        combined_score=float(index),
        complexity=float(index + 1),
        correct=True,
    )


def test_concurrent_repository_additions_preserve_supplied_complexity(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        sync_db = ProgramDatabase(
            DatabaseConfig(
                db_path=str(tmp_path / "async_concurrent.db"),
                num_islands=1,
            ),
            embedding_model="",
        )
        async_db = AsyncProgramDatabase(sync_db=sync_db)
        try:
            await asyncio.gather(
                *(
                    async_db.add_program_async(program=_program("repo", index))
                    for index in range(40)
                )
            )

            stored = sync_db.get("repo-0020")
            assert stored is not None
            assert stored.complexity == 21.0
            assert "code_analysis_metrics" not in (stored.metadata or {})
        finally:
            await async_db.close_async()
            sync_db.close()

    asyncio.run(run())
