import asyncio
from pathlib import Path

import pandas as pd

from shinka.utils import load_df as load_df_module
from shinka.utils.async_io import write_text_async


def test_write_text_async_uses_utf8_with_path_methods(
    monkeypatch,
    tmp_path: Path,
) -> None:
    original_write_text = Path.write_text
    content = 'print("Han 漢")\n'
    file_path = tmp_path / "nested" / "individual.md"

    def write_text_with_cp1252_default(
        self,
        data,
        encoding=None,
        errors=None,
        newline=None,
    ):
        return original_write_text(
            self,
            data,
            encoding=encoding or "cp1252",
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", write_text_with_cp1252_default)

    written = asyncio.run(write_text_async(file_path, content))

    assert written is True
    assert file_path.read_text(encoding="utf-8") == content


def test_store_best_path_writes_utf8_repo_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    original_write_text = Path.write_text

    def write_text_with_cp1252_default(
        self,
        data,
        encoding=None,
        errors=None,
        newline=None,
    ):
        return original_write_text(
            self,
            data,
            encoding=encoding or "cp1252",
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", write_text_with_cp1252_default)

    df = pd.DataFrame(
        [
            {
                "id": "prog-1",
                "parent_id": None,
                "generation": 1,
                "correct": True,
                "combined_score": 1.0,
                "repo_diff": """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1 +1 @@
-print("plain")
+print("Han 漢")
""",
                "repo_summary": "# Individual Summary\n\nHan 漢\n",
                "patch_name": "unicode-mutation",
            }
        ]
    )

    load_df_module.store_best_path(df, str(tmp_path))

    assert (tmp_path / "best_path" / "diffs" / "repo_0.diff").read_text(
        encoding="utf-8"
    ).endswith('print("Han 漢")\n')
    assert (tmp_path / "best_path" / "summaries" / "individual_0.md").read_text(
        encoding="utf-8"
    ) == "# Individual Summary\n\nHan 漢\n"
