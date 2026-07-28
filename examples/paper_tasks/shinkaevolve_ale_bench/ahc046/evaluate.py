"""External ALE-Bench LITE evaluator for the fixed ahc046 task."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from shinka.core.wrap_eval import save_json_results


PROBLEM_ID = "ahc046"


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _case_counts(result: Any) -> tuple[int, int]:
    passed = sum(
        _value(case.judge_result) == "ACCEPTED" for case in result.case_results
    )
    return passed, len(result.case_results) - passed


def _feedback_case(result: Any) -> Any:
    overall = _value(result.overall_judge_result)
    if overall == "ACCEPTED":
        return result.case_results[0]
    return next(
        (case for case in result.case_results if _value(case.judge_result) == overall),
        result.case_results[0],
    )


def main(
    repo_path: str,
    results_dir: str,
    num_public_cases: int,
    num_workers: int,
    private_eval: bool,
) -> None:
    output = Path(results_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        import ale_bench

        session_file = output / "session.json"
        if session_file.exists():
            session = ale_bench.restart(
                session_saved_file=session_file, num_workers=num_workers
            )
        else:
            session = ale_bench.start(
                problem_id=PROBLEM_ID,
                lite_version=True,
                num_workers=num_workers,
            )
        if session is None:
            raise RuntimeError("ALE-Bench did not create a session")

        code = (Path(repo_path).resolve() / "main.cpp").read_text(encoding="utf-8")
        cases = session.case_gen(list(range(num_public_cases)))
        result = session.case_eval(
            cases,
            code,
            code_language="cpp20",
            skip_local_visualization=True,
        )
        (output / "public_result.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        session.save(session_file)

        passed, failed = _case_counts(result)
        score_type = _value(session.problem.metadata.score_type)
        raw_mean = float(result.overall_absolute_score) / num_public_cases
        score = raw_mean if score_type == "maximize" else -raw_mean
        judge_result = _value(result.overall_judge_result)
        feedback = _feedback_case(result)
        metrics: dict[str, Any] = {
            "combined_score": score,
            "public": {
                "problem_id": PROBLEM_ID,
                "raw_mean_score": raw_mean,
                "score_type": score_type,
                "judge_result": judge_result,
                "passed_cases": passed,
                "failed_cases": failed,
                "max_execution_time_sec": max(
                    case.execution_time for case in result.case_results
                ),
                "max_memory_usage_mib": max(
                    case.memory_usage for case in result.case_results
                )
                / 1024**2,
                "standard_error": feedback.error_str,
                "message": feedback.message,
            },
        }
        if private_eval:
            private_result, rank, performance = session.private_eval(
                code, code_language="cpp20"
            )
            (output / "private_result.json").write_text(
                private_result.model_dump_json(indent=2), encoding="utf-8"
            )
            private_passed, private_failed = _case_counts(private_result)
            metrics["private"] = {
                "rank": rank,
                "performance": performance,
                "raw_score": float(private_result.overall_absolute_score),
                "passed_cases": private_passed,
                "failed_cases": private_failed,
            }
        save_json_results(results_dir, metrics, judge_result == "ACCEPTED")
    except Exception as exc:
        save_json_results(
            results_dir,
            {
                "combined_score": 0.0,
                "public": {
                    "problem_id": PROBLEM_ID,
                    "judge_result": "REJECTED",
                },
            },
            False,
            str(exc),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", required=True)
    parser.add_argument("--results_dir", required=True)
    parser.add_argument("--num_public_cases", type=int, default=50)
    parser.add_argument("--num_workers", type=int, default=13)
    parser.add_argument("--private_eval", action="store_true")
    args = parser.parse_args()
    main(
        args.repo_path,
        args.results_dir,
        args.num_public_cases,
        args.num_workers,
        args.private_eval,
    )
