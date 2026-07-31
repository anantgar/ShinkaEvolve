"""Repository-level source complexity analysis.

Repository individuals are executable trees, not single source strings. This
module analyzes each eligible source file separately and combines the results
without treating Markdown summaries, vendored code, or raw diffs as code.
"""

from __future__ import annotations

import fnmatch
import math
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from shinka.database.complexity import analyze_code_metrics


REPO_COMPLEXITY_SCHEMA_VERSION = "repo-complexity-v1"

# These are the languages the existing analyzer can interpret structurally.
SUPPORTED_LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".pyw": "python",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cp": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cu": "cuda",
    ".cuh": "cuda",
    ".go": "go",
    ".rs": "rust",
}

# Known source languages which are deliberately reported as unsupported rather
# than sent through the generic analyzer, where they would get a fake CC of 1.
UNSUPPORTED_LANGUAGE_EXTENSIONS = {
    ".cs": "csharp",
    ".fs": "fsharp",
    ".f": "fortran",
    ".f90": "fortran",
    ".f95": "fortran",
    ".hs": "haskell",
    ".java": "java",
    ".jl": "julia",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".m": "matlab",
    ".php": "php",
    ".pl": "perl",
    ".rb": "ruby",
    ".r": "r",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
}

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".shinka",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "out",
        "site-packages",
        "target",
        "third-party",
        "third_party",
        "vendor",
        "venv",
    }
)
GENERATED_FILE_PATTERNS = ("*.generated.*", "*.gen.*", "*_generated.*")


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize_path(path)
    for pattern in patterns:
        normalized_pattern = _normalize_path(pattern)
        if not normalized_pattern:
            continue
        if (
            normalized == normalized_pattern
            or normalized.startswith(f"{normalized_pattern}/")
            or fnmatch.fnmatchcase(normalized, normalized_pattern)
        ):
            return True
    return False


def _is_generated_or_vendor_path(path: str) -> bool:
    pure_path = PurePosixPath(path)
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in pure_path.parts[:-1]):
        return True
    return any(
        fnmatch.fnmatchcase(pure_path.name, pattern)
        for pattern in GENERATED_FILE_PATTERNS
    )


def _candidate_paths(repo_path: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Could not list candidate files for complexity analysis: {detail}"
        )
    return sorted(
        _normalize_path(path.decode("utf-8"))
        for path in completed.stdout.split(b"\0")
        if path
    )


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def _empty_aggregate() -> dict[str, Any]:
    return {
        "lines_of_code": 0,
        "logical_lines_of_code": 0,
        "comments": 0,
        "cyclomatic_complexity": 0,
        "complexity_blocks": 0,
        "average_cyclomatic_complexity": 0.0,
        "halstead_volume": 0.0,
        "halstead_difficulty": 0.0,
        "halstead_effort": 0.0,
        "maintainability_index": 0.0,
        "max_nesting_depth": 0,
        "complexity_score": 0.0,
        "p95_file_complexity_score": 0.0,
        "max_file_complexity_score": 0.0,
    }


def analyze_repository_complexity(
    repo_path: str | Path,
    *,
    mutable_paths: Optional[Iterable[str]] = None,
    immutable_paths: Optional[Iterable[str]] = None,
    ignore_paths: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Analyze eligible candidate source files and aggregate their metrics.

    Empty ``mutable_paths`` means the whole candidate is mutable, subject to
    immutable, ignored, generated, and vendor exclusions. Unsupported source
    languages remain visible in coverage metadata but never receive generic
    cyclomatic-complexity values. Untracked entries are included because secure
    candidate artifacts use a synthetic Git repository for diagnostics.
    """

    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Repository path does not exist: {root}")

    mutable = list(mutable_paths or [])
    immutable = list(immutable_paths or [])
    ignored = list(dict.fromkeys([".git", ".shinka", *(ignore_paths or [])]))
    candidate_paths = _candidate_paths(root)
    analyzed_files: list[dict[str, Any]] = []
    unsupported_files: list[dict[str, str]] = []
    skipped_files: list[dict[str, str]] = []
    exclusion_counts: Counter[str] = Counter()

    for relative_path in candidate_paths:
        if _matches_any(relative_path, ignored):
            exclusion_counts["ignored"] += 1
            continue
        if immutable and _matches_any(relative_path, immutable):
            exclusion_counts["immutable"] += 1
            continue
        if mutable and not _matches_any(relative_path, mutable):
            exclusion_counts["outside_mutable_paths"] += 1
            continue
        if _is_generated_or_vendor_path(relative_path):
            exclusion_counts["generated_or_vendor"] += 1
            continue

        extension = Path(relative_path).suffix.lower()
        language = SUPPORTED_LANGUAGE_EXTENSIONS.get(extension)
        if language is None:
            unsupported_language = UNSUPPORTED_LANGUAGE_EXTENSIONS.get(extension)
            if unsupported_language is not None:
                unsupported_files.append(
                    {"path": relative_path, "language": unsupported_language}
                )
            else:
                exclusion_counts["not_supported_source"] += 1
            continue

        path = root / relative_path
        if path.is_symlink() or not path.is_file():
            skipped_files.append({"path": relative_path, "reason": "not_regular_file"})
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            skipped_files.append(
                {"path": relative_path, "reason": f"read_error: {exc}"}
            )
            continue
        if b"\0" in content:
            skipped_files.append({"path": relative_path, "reason": "binary"})
            continue
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            skipped_files.append({"path": relative_path, "reason": "non_utf8"})
            continue

        try:
            metrics = analyze_code_metrics(source, language)
        except Exception as exc:
            skipped_files.append(
                {"path": relative_path, "reason": f"analysis_error: {exc}"}
            )
            continue
        analyzed_files.append(
            {
                "path": relative_path,
                "language": language,
                "bytes": len(content),
                "metrics": metrics,
            }
        )

    aggregate = _empty_aggregate()
    if analyzed_files:
        aggregate["lines_of_code"] = sum(
            int(item["metrics"].get("lines_of_code", 0)) for item in analyzed_files
        )
        aggregate["logical_lines_of_code"] = sum(
            int(item["metrics"].get("logical_lines_of_code", 0))
            for item in analyzed_files
        )
        aggregate["comments"] = sum(
            int(item["metrics"].get("comments", 0)) for item in analyzed_files
        )
        aggregate["cyclomatic_complexity"] = sum(
            int(item["metrics"].get("cyclomatic_complexity", 0))
            for item in analyzed_files
        )
        aggregate["complexity_blocks"] = sum(
            int(item["metrics"].get("complexity_blocks", 0)) for item in analyzed_files
        )
        aggregate["halstead_volume"] = sum(
            float(item["metrics"].get("halstead_volume", 0.0))
            for item in analyzed_files
        )
        aggregate["halstead_effort"] = sum(
            float(item["metrics"].get("halstead_effort", 0.0))
            for item in analyzed_files
        )

        weights = [
            max(
                1,
                int(item["metrics"].get("logical_lines_of_code", 0)),
                int(item["metrics"].get("lines_of_code", 0)),
            )
            for item in analyzed_files
        ]
        total_weight = sum(weights)
        aggregate["halstead_difficulty"] = (
            sum(
                weight * float(item["metrics"].get("halstead_difficulty", 0.0))
                for item, weight in zip(analyzed_files, weights)
            )
            / total_weight
        )
        aggregate["maintainability_index"] = (
            sum(
                weight * float(item["metrics"].get("maintainability_index", 0.0))
                for item, weight in zip(analyzed_files, weights)
            )
            / total_weight
        )
        aggregate["complexity_score"] = round(
            sum(
                weight * float(item["metrics"].get("complexity_score", 0.0))
                for item, weight in zip(analyzed_files, weights)
            )
            / total_weight,
            3,
        )
        aggregate["max_nesting_depth"] = max(
            int(item["metrics"].get("max_nesting_depth", 0)) for item in analyzed_files
        )
        scores = [
            float(item["metrics"].get("complexity_score", 0.0))
            for item in analyzed_files
        ]
        aggregate["p95_file_complexity_score"] = round(_percentile_95(scores), 3)
        aggregate["max_file_complexity_score"] = round(max(scores), 3)
        block_count = aggregate["complexity_blocks"]
        aggregate["average_cyclomatic_complexity"] = round(
            aggregate["cyclomatic_complexity"] / block_count if block_count else 0.0,
            3,
        )

    language_counts = Counter(item["language"] for item in analyzed_files)
    return {
        "schema_version": REPO_COMPLEXITY_SCHEMA_VERSION,
        "status": "ok",
        "scope": {
            "mutable_paths": mutable,
            "immutable_paths": immutable,
            "ignore_paths": ignored,
            "generated_or_vendor_directories": sorted(EXCLUDED_DIRECTORY_NAMES),
        },
        "coverage": {
            "candidate_file_count": len(candidate_paths),
            "analyzed_file_count": len(analyzed_files),
            "unsupported_file_count": len(unsupported_files),
            "skipped_file_count": len(skipped_files),
            "excluded_file_counts": dict(sorted(exclusion_counts.items())),
            "language_file_counts": dict(sorted(language_counts.items())),
        },
        "aggregate": aggregate,
        "files": analyzed_files,
        "unsupported_files": unsupported_files,
        "skipped_files": skipped_files,
    }
