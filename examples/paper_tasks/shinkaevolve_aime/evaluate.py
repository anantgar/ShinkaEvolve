"""ShinkaEvolve paper protocol for evolving AIME agent scaffolds."""

from __future__ import annotations

import argparse
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from shinka.core.wrap_eval import load_program, save_json_results


PINNED_DATASET_URL = (
    "https://raw.githubusercontent.com/SakanaAI/ShinkaEvolve/"
    "5885bb7/examples/adas_aime/AIME_Dataset_1983_2025.csv"
)


class CallLimitedQuery:
    def __init__(self, query: Callable[..., tuple[str, float]], max_calls: int):
        self.query = query
        self.max_calls = max_calls
        self._thread_local = threading.local()

    @property
    def calls(self) -> int:
        return int(getattr(self._thread_local, "calls", 0))

    def reset(self) -> None:
        self._thread_local.calls = 0

    def __call__(self, **kwargs: Any) -> tuple[str, float]:
        if self.calls >= self.max_calls:
            raise RuntimeError(f"agent exceeded the {self.max_calls}-query budget")
        self._thread_local.calls = self.calls + 1
        return self.query(**kwargs)


def _openai_query(model_name: str) -> Callable[..., tuple[str, float]]:
    from openai import OpenAI

    client = OpenAI()

    def query(prompt: str, system: str, temperature: float = 0.0) -> tuple[str, float]:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content or "", 0.0

    return query


def _extract_answer(response: str) -> str | None:
    boxed = re.findall(r"\\boxed\s*\{\s*(\d{1,3})\s*\}", response)
    return str(int(boxed[-1])) if boxed else None


def _load_dataset(
    dataset_path: str | None, year: int, limit: int | None
) -> pd.DataFrame:
    source = dataset_path or PINNED_DATASET_URL
    data = pd.read_csv(source)
    required = {"Year", "problem", "answer"}
    if not required.issubset(data.columns):
        raise ValueError(
            f"dataset is missing columns: {sorted(required - set(data.columns))}"
        )
    selected = data[data["Year"] == year].copy()
    if limit is not None:
        selected = selected.head(limit)
    if selected.empty:
        raise ValueError(f"dataset has no AIME {year} problems")
    return selected


def evaluate_once(
    agent_class: type,
    problems: pd.DataFrame,
    base_query: Callable[..., tuple[str, float]],
    max_calls: int,
    problem_workers: int,
) -> dict[str, Any]:
    limited = CallLimitedQuery(base_query, max_calls)
    agent = agent_class(limited)
    rows: list[dict[str, Any] | None] = [None] * len(problems)

    def evaluate_example(index: int, example: pd.Series) -> tuple[int, dict[str, Any]]:
        limited.reset()
        response = ""
        error = None
        try:
            response, _ = agent.forward(str(example["problem"]).strip())
        except Exception as exc:
            error = str(exc)
        answer = _extract_answer(response)
        expected = str(int(example["answer"]))
        return index, {
            "problem": str(example["problem"]),
            "response": response,
            "answer": answer,
            "expected": expected,
            "correct": answer == expected,
            "num_calls": limited.calls,
            "error": error,
        }

    workers = min(problem_workers, len(problems))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(evaluate_example, index, example): index
            for index, (_, example) in enumerate(problems.iterrows())
        }
        for future in as_completed(futures):
            index, row = future.result()
            rows[index] = row
    completed_rows = [row for row in rows if row is not None]
    return {
        "accuracy": 100.0
        * sum(row["correct"] for row in completed_rows)
        / len(completed_rows),
        "average_calls": sum(row["num_calls"] for row in completed_rows)
        / len(completed_rows),
        "rows": completed_rows,
    }


def main(
    repo_path: str,
    results_dir: str,
    model_name: str,
    year: int,
    num_experiment_runs: int,
    max_calls: int,
    dataset_path: str | None,
    limit: int | None,
    problem_workers: int,
) -> None:
    try:
        module = load_program(str(Path(repo_path).resolve() / "agent.py"))
        if not hasattr(module, "Agent"):
            raise ValueError("candidate must define Agent(query_llm)")
        problems = _load_dataset(dataset_path, year, limit)
        query = _openai_query(model_name)
        if problem_workers == 0:
            problem_workers = min(30, os.cpu_count() or 1)
        if problem_workers < 1:
            raise ValueError("problem_workers must be positive, or zero for auto")
        runs = [
            evaluate_once(module.Agent, problems, query, max_calls, problem_workers)
            for _ in range(num_experiment_runs)
        ]
        accuracies = [run["accuracy"] for run in runs]
        always_wrong = [
            index
            for index in range(len(problems))
            if all(not run["rows"][index]["correct"] for run in runs)
        ]
        feedback = ""
        if always_wrong:
            row = runs[0]["rows"][always_wrong[0]]
            feedback = (
                f"Example missed in every run:\n{row['problem']}\n\n"
                f"Candidate response:\n{row['response']}\n\nExpected: {row['expected']}"
            )
        metrics = {
            "combined_score": float(np.mean(accuracies)),
            "public": {
                "year": year,
                "model": model_name,
                "num_problems": len(problems),
                "mean_accuracy_percent": float(np.mean(accuracies)),
                "mean_calls_per_problem": float(
                    np.mean([run["average_calls"] for run in runs])
                ),
            },
            "private": {"run_accuracies": accuracies},
            "text_feedback": feedback,
        }
        save_json_results(results_dir, metrics, True)
    except Exception as exc:
        save_json_results(
            results_dir,
            {"combined_score": 0.0, "public": {}, "private": {}},
            False,
            str(exc),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    parser.add_argument("--model_name", default="gpt-4.1-nano")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--num_experiment_runs", type=int, default=3)
    parser.add_argument("--max_calls", type=int, default=10)
    parser.add_argument("--dataset_path")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--problem_workers",
        type=int,
        default=0,
        help="Concurrent AIME problems; zero mirrors the paper's min(30, CPU) setting",
    )
    args = parser.parse_args()
    main(
        args.repo_path,
        args.results_dir,
        args.model_name,
        args.year,
        args.num_experiment_runs,
        args.max_calls,
        args.dataset_path,
        args.limit,
        args.problem_workers,
    )
