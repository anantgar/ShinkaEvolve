from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "examples" / "paper_tasks"

CONSTRUCTION_TASKS = (
    "circle_rectangle_21",
    "circle_square_26",
    "circle_square_32",
    "heilbronn_convex_13",
    "heilbronn_convex_14",
    "heilbronn_triangle_11",
    "hexagon_packing_11",
    "hexagon_packing_12",
    "kissing_number_11",
    "max_min_distance_14_3",
    "max_min_distance_16_2",
)

DISCRETE_TASKS = (
    "autocorrelation_c1_600",
    "autocorrelation_c2_50",
    "autocorrelation_c3_400",
    "erdos_minimum_overlap_95",
    "sum_difference_set",
    "uncertainty_hermite",
)

ALE_TASKS = (
    "ahc008",
    "ahc011",
    "ahc015",
    "ahc016",
    "ahc024",
    "ahc025",
    "ahc026",
    "ahc027",
    "ahc039",
    "ahc046",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task_modules(task: Path):
    evaluator = _load(f"evaluator_{task.parent.name}_{task.name}", task / "evaluate.py")
    solution = _load(
        f"solution_{task.parent.name}_{task.name}",
        task / "seed_repo" / "solution.py",
    )
    return evaluator, solution


def test_every_runnable_task_is_an_independent_repo_artifact() -> None:
    configs = sorted(PAPER.rglob("shinka.yaml"))
    assert len(configs) == 31
    for config_path in configs:
        task = config_path.parent
        assert (task / "evaluate.py").is_file(), task
        assert (task / "seed_repo").is_dir(), task
        assert any(path.is_file() for path in (task / "seed_repo").rglob("*")), task
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config["evo"]["seed_repo_path"] == "seed_repo"
        assert config["evo"]["mutable_paths"] == []
        assert config["evo"]["immutable_paths"] == []
        assert config["evo"]["agent_hidden_paths"] == []
        assert config["job"]["eval_program_path"] == "evaluate.py"

    assert not list(PAPER.rglob("initial.py"))
    for path in PAPER.rglob("*"):
        if path.is_file():
            assert "EVOLVE-BLOCK" not in path.read_text(
                encoding="utf-8", errors="ignore"
            )


def test_alphaevolve_construction_seeds_are_valid() -> None:
    family = PAPER / "alphaevolve_constructions"
    assert not (family / "evaluate.py").exists()
    for task_name in CONSTRUCTION_TASKS:
        evaluator, solution = _task_modules(family / task_name)
        score, _ = evaluator.assess(solution.construct())
        assert np.isfinite(score), task_name


def test_alphaevolve_discrete_seeds_are_valid() -> None:
    family = PAPER / "alphaevolve_discrete"
    assert not (family / "evaluate.py").exists()
    for task_name in DISCRETE_TASKS:
        evaluator, solution = _task_modules(family / task_name)
        score, details = evaluator.assess(solution.construct())
        assert np.isfinite(score), task_name
        if task_name == "uncertainty_hermite":
            assert details["upper_bound_c4"] == pytest.approx(0.3523, abs=1e-4)


def test_fixed_matrix_multiplication_seed_is_exact() -> None:
    task = PAPER / "alphaevolve_matrix_multiplication" / "matmul_2_2_2"
    evaluator, solution = _task_modules(task)
    factors = solution.construct()
    score, details = evaluator.assess(factors)
    assert score == -8.0
    assert details["rank"] == 8
    metrics, correct, error = evaluator.evaluate(task / "seed_repo")
    assert correct, error
    assert metrics["combined_score"] == pytest.approx(-7.999)
    assert metrics["public"]["target"] == [2, 2, 2]


def test_matrix_verifier_does_not_repair_candidate_factors() -> None:
    task = PAPER / "alphaevolve_matrix_multiplication" / "matmul_2_2_2"
    evaluator, solution = _task_modules(task)
    factors = [factor.copy() for factor in solution.construct()]
    factors[2][1, 0] = 0.1
    with pytest.raises(ValueError, match="decomposition is not exact"):
        evaluator.assess(factors)


def test_kissing_verifier_uses_notebook_integer_rounding() -> None:
    task = PAPER / "alphaevolve_constructions" / "kissing_number_11"
    evaluator, solution = _task_modules(task)
    points = solution.construct().astype(float) + 0.49
    score, details = evaluator.assess(points)
    assert score == 22.0
    assert details["min_squared_distance"] >= details["max_squared_norm"]


def test_matrix_failed_trials_reduce_success_fraction(monkeypatch) -> None:
    task = PAPER / "alphaevolve_matrix_multiplication" / "matmul_2_2_2"
    evaluator, solution = _task_modules(task)
    valid = solution.construct(0)

    def construct(seed: int):
        return valid if seed == 0 else (np.zeros((4, 1)),) * 3

    monkeypatch.setattr(
        evaluator, "_load", lambda _: SimpleNamespace(construct=construct)
    )
    metrics, correct, error = evaluator.evaluate(task / "seed_repo")
    assert correct, error
    assert metrics["public"]["successful_trials"] == 1
    assert metrics["public"]["best_rank_success_fraction"] == pytest.approx(1 / 3)


def test_shinkaevolve_circle_seed_and_objective_are_verified() -> None:
    task = PAPER / "shinkaevolve_circle_packing"
    evaluator, solution = _task_modules(task)
    score, details = evaluator.assess(solution.run_packing())
    assert score == pytest.approx(details["sum_radii"])

    centers = np.full((26, 2), 0.5)
    radii = np.zeros(26)
    with pytest.raises(ValueError, match="reported radius sum"):
        evaluator.assess((centers, radii, 1e30))

    centers[0, 0] = -5e-7
    score, _ = evaluator.assess((centers, radii, 0.0), tolerance=1e-6)
    assert score == 0.0


def test_shinkaevolve_external_scaffolds_keep_paper_contracts() -> None:
    aime_task = PAPER / "shinkaevolve_aime"
    aime = _load("paper_aime_evaluator", aime_task / "evaluate.py")
    aime_seed = _load("paper_aime_seed", aime_task / "seed_repo" / "agent.py")
    assert aime._extract_answer(r"Therefore, \\boxed{042}.") == "42"
    problems = aime.pd.DataFrame(
        [
            {"problem": "Return 1.", "answer": 1},
            {"problem": "Return 2.", "answer": 2},
        ]
    )

    def fake_query(**kwargs):
        answer = "1" if "Return 1" in kwargs["prompt"] else "2"
        return rf"\\boxed{{{answer}}}", 0.0

    result = aime.evaluate_once(aime_seed.Agent, problems, fake_query, 10, 2)
    assert result["accuracy"] == 100.0
    assert result["average_calls"] == 1.0

    moe_task = PAPER / "shinkaevolve_moe"
    moe = _load("paper_moe_evaluator", moe_task / "evaluate.py")
    moe._validate_source(str(moe_task / "seed_repo" / "loss.py"))


def test_ale_bench_tasks_are_split_and_fixed() -> None:
    family = PAPER / "shinkaevolve_ale_bench"
    assert not (family / "evaluate.py").exists()
    for problem_id in ALE_TASKS:
        task = family / problem_id
        evaluator = _load(f"ale_{problem_id}", task / "evaluate.py")
        assert evaluator.PROBLEM_ID == problem_id
        source = (task / "seed_repo" / "main.cpp").read_text(encoding="utf-8")
        assert len(source) > 1_000


def test_invalid_minimization_candidates_cannot_outscore_valid_seeds() -> None:
    tasks = (
        PAPER / "alphaevolve_constructions" / "max_min_distance_16_2",
        PAPER / "alphaevolve_constructions" / "max_min_distance_14_3",
        PAPER / "alphaevolve_constructions" / "hexagon_packing_11",
        PAPER / "alphaevolve_constructions" / "hexagon_packing_12",
        PAPER / "alphaevolve_discrete" / "autocorrelation_c1_600",
        PAPER / "alphaevolve_discrete" / "autocorrelation_c3_400",
        PAPER / "alphaevolve_discrete" / "erdos_minimum_overlap_95",
        PAPER / "alphaevolve_discrete" / "uncertainty_hermite",
    )
    for task in tasks:
        evaluator = _load(
            f"penalty_{task.parent.name}_{task.name}", task / "evaluate.py"
        )
        metrics, correct, _ = evaluator.evaluate(task / "missing_repo")
        assert not correct
        assert metrics["combined_score"] == -1.0e30
