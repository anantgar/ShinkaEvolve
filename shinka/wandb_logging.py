"""Optional Weights & Biases logging for evolution runs."""

from __future__ import annotations

import importlib
import logging
import math
import re
import uuid
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from shinka.database import Program, ProgramDatabase

logger = logging.getLogger(__name__)

GENERATION_METRIC = "generation"
INDIVIDUAL_SCORE_METRIC = "score/individual"
EVALUATED_COUNT_METRIC = "population/evaluated_count"
PROGRAM_TABLE_KEY = "population/final_table"
WANDB_RUN_ID_FILENAME = ".wandb_run_id"
PROGRAM_TABLE_COLUMNS = [
    "id",
    "generation",
    "score",
    "correct",
    "parent_id",
    "island_idx",
    "is_copy",
    "in_archive",
    "patch_type",
    "model_name",
    "cost",
]

_COST_KEYS = {
    "api": "api_costs",
    "embed": "embed_cost",
    "novelty": "novelty_cost",
    "meta": "meta_cost",
}
_TIMING_KEYS = (
    "sampling_seconds",
    "evaluation_seconds",
    "postprocess_seconds",
    "pipeline_seconds",
)


def ensure_wandb_run_id(
    results_dir: Path,
    configured_id: Optional[str] = None,
) -> str:
    """Persist and return the W&B run ID associated with a results directory."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id_path = results_dir / WANDB_RUN_ID_FILENAME
    run_id = str(configured_id).strip() if configured_id else ""

    if not run_id and run_id_path.is_file():
        run_id = run_id_path.read_text(encoding="utf-8").strip()
    if not run_id:
        run_id = uuid.uuid4().hex

    run_id_path.write_text(f"{run_id}\n", encoding="utf-8")
    return run_id


def build_program_log_payload(program: Program) -> Dict[str, Any]:
    """Build one raw W&B history event for an evaluated individual.

    These events intentionally use W&B's normal internal history step. They are
    useful as candidate scatter data, but are not the run's progress axis.
    """
    metadata = program.metadata or {}
    costs = program_costs(program)
    logged_costs = {
        name: value
        for name, value in costs.items()
        if not (name == "api" and metadata.get("headless_pricing_unknown") is True)
    }
    individual_score = _finite_float(program.combined_score)
    payload: Dict[str, Any] = {
        GENERATION_METRIC: program.generation,
        INDIVIDUAL_SCORE_METRIC: individual_score,
        "individual/id": program.id,
        "individual/parent_id": program.parent_id,
        "individual/island_idx": program.island_idx,
        "individual/is_copy": is_island_copy(program),
        "individual/correct": bool(program.correct),
        "individual/in_archive": bool(program.in_archive),
        "individual/patch_type": metadata.get("patch_type"),
        "individual/model_name": _program_model_name(program),
        "headless/usage_status": metadata.get("headless_usage_status"),
        "headless/usage_unknown": metadata.get("headless_usage_unknown"),
        "headless/pricing_status": metadata.get("headless_pricing_status"),
        "headless/pricing_unknown": metadata.get("headless_pricing_unknown"),
        "headless/cost_basis": metadata.get("headless_cost_basis"),
        "headless/pricing_source": metadata.get("headless_pricing_source"),
        **{
            f"individual/cost/{name}": value
            for name, value in logged_costs.items()
        },
    }

    for key in _TIMING_KEYS:
        value = _finite_float(metadata.get(key))
        if value is not None:
            payload[f"individual/timing/{key}"] = value

    for prefix, metrics in (
        ("public_metrics", program.public_metrics),
        ("private_metrics", program.private_metrics),
    ):
        for key, value in flatten_numeric_metrics(metrics, prefix).items():
            leaf_name = key.rsplit("/", 1)[-1]
            if leaf_name in {"score", "combined_score"} and value == individual_score:
                continue
            payload[key] = value
    return {key: value for key, value in payload.items() if value is not None}


def is_island_copy(program: Program) -> bool:
    """Return whether a DB row is an administrative copy, not a new evaluation."""
    metadata = program.metadata or {}
    return bool(metadata.get("_is_island_copy") or metadata.get("_spawned_island"))


def flatten_numeric_metrics(
    data: Any,
    prefix: str,
    *,
    max_depth: int = 3,
) -> Dict[str, float]:
    """Flatten only numeric evaluation metrics, excluding bulky text and arrays."""
    flattened: Dict[str, float] = {}

    def visit(value: Any, parts: List[str], depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, [*parts, _metric_segment(key)], depth + 1)
            return
        numeric_value = _finite_float(value)
        if numeric_value is not None:
            flattened["/".join([prefix, *parts])] = numeric_value

    if isinstance(data, dict):
        for key, value in data.items():
            visit(value, [_metric_segment(key)], 1)
    return flattened


def program_costs(program: Program) -> Dict[str, float]:
    """Return the non-duplicated cost breakdown for one individual."""
    metadata = program.metadata or {}
    return {
        name: _finite_float(metadata.get(metadata_key)) or 0.0
        for name, metadata_key in _COST_KEYS.items()
    }


def program_table_row(program: Program) -> List[Any]:
    """Return a compact row; detailed program data remains in the WebUI database."""
    metadata = program.metadata or {}
    return [
        program.id,
        program.generation,
        _finite_float(program.combined_score),
        bool(program.correct),
        program.parent_id,
        program.island_idx,
        is_island_copy(program),
        bool(program.in_archive),
        metadata.get("patch_type"),
        _program_model_name(program),
        (
            None
            if metadata.get("headless_pricing_unknown") is True
            else sum(program_costs(program).values())
        ),
    ]


def build_population_progress_payload(
    programs: Sequence[Program],
    *,
    evaluated_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Build cumulative population and island metrics for one W&B snapshot.

    ``population/evaluated_count`` counts unique evaluation rows and excludes
    administrative island copies. Population and island ``count`` values retain
    those copies because they describe the actual DB population shown by the
    WebUI.
    """
    all_programs = list(programs)
    evaluated_programs = [program for program in all_programs if not is_island_copy(program)]
    scores = [
        score
        for program in all_programs
        if (score := _finite_float(program.combined_score)) is not None
    ]
    correct_programs = [program for program in all_programs if program.correct]
    correct_scores = [
        score
        for program in correct_programs
        if (score := _finite_float(program.combined_score)) is not None
    ]
    costs = {
        name: sum(
            program_costs(program)[name]
            for program in evaluated_programs
            if not (
                name == "api"
                and (program.metadata or {}).get("headless_pricing_unknown") is True
            )
        )
        for name in _COST_KEYS
    }
    unknown_pricing_count = sum(
        1
        for program in evaluated_programs
        if (program.metadata or {}).get("headless_pricing_unknown") is True
    )
    payload: Dict[str, Any] = {
        EVALUATED_COUNT_METRIC: (
            len(evaluated_programs)
            if evaluated_count is None
            else max(0, int(evaluated_count))
        ),
        "population/count": len(all_programs),
        "population/correct_count": len(correct_programs),
        "population/correct_rate": (
            len(correct_programs) / len(all_programs) if all_programs else 0.0
        ),
        "population/best_score": max(scores) if scores else None,
        "population/best_correct_score": max(correct_scores)
        if correct_scores
        else None,
        "population/mean_score": sum(scores) / len(scores) if scores else None,
        "cost/api": costs["api"],
        "cost/embed": costs["embed"],
        "cost/novelty": costs["novelty"],
        "cost/meta": costs["meta"],
        "cost/total": sum(costs.values()),
        "cost/pricing_unknown_count": unknown_pricing_count,
    }

    for key in _TIMING_KEYS:
        payload[f"timing/{key}_total"] = sum(
            _finite_float((program.metadata or {}).get(key)) or 0.0
            for program in evaluated_programs
        )

    islands: Dict[str, List[Program]] = {}
    for program in all_programs:
        island_key = (
            str(program.island_idx)
            if program.island_idx is not None
            else "unknown"
        )
        islands.setdefault(island_key, []).append(program)

    for island_key, island_programs in islands.items():
        island_scores = [
            score
            for program in island_programs
            if (score := _finite_float(program.combined_score)) is not None
        ]
        island_correct = sum(1 for program in island_programs if program.correct)
        metric_prefix = f"island/{_metric_segment(island_key)}"
        payload.update(
            {
                f"{metric_prefix}/count": len(island_programs),
                f"{metric_prefix}/evaluated_count": sum(
                    1 for program in island_programs if not is_island_copy(program)
                ),
                f"{metric_prefix}/correct_count": island_correct,
                f"{metric_prefix}/correct_rate": (
                    island_correct / len(island_programs)
                    if island_programs
                    else 0.0
                ),
                f"{metric_prefix}/best_score": max(island_scores)
                if island_scores
                else None,
                f"{metric_prefix}/mean_score": (
                    sum(island_scores) / len(island_scores)
                    if island_scores
                    else None
                ),
            }
        )

    return {key: value for key, value in payload.items() if value is not None}


def build_run_summary(
    programs: Sequence[Program],
    *,
    total_proposals_generated: Optional[int] = None,
    total_cost: Optional[float] = None,
) -> Dict[str, Any]:
    """Build concise final values for the W&B run summary."""
    population = build_population_progress_payload(programs)
    correct_programs = [program for program in programs if program.correct]
    correct_scores = [
        score
        for program in correct_programs
        if (score := _finite_float(program.combined_score)) is not None
    ]
    summary = {
        "run/program_count": len(programs),
        "run/evaluated_count": population[EVALUATED_COUNT_METRIC],
        "run/correct_rate": population["population/correct_rate"],
        "run/max_generation": max(
            (program.generation for program in programs), default=0
        ),
        "run/best_score": max(correct_scores) if correct_scores else None,
        "run/total_proposals_generated": total_proposals_generated,
        "run/total_cost": _finite_float(total_cost),
    }
    return {key: value for key, value in summary.items() if value is not None}


class ShinkaWandbLogger:
    """Best-effort W&B sink that leaves database/WebUI logging unchanged."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._wandb: Optional[Any] = None
        self._run: Optional[Any] = None
        self._logged_program_ids: set[str] = set()
        self._last_population_evaluated_count: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.enabled and self._run is not None

    def start(
        self,
        *,
        evo_config: Any,
        db_config: Any,
        job_config: Any,
        results_dir: Path,
    ) -> None:
        if not self.enabled:
            return

        try:
            self._wandb = importlib.import_module("wandb")
        except ImportError:
            logger.warning(
                "W&B logging is enabled, but wandb is not installed. "
                "Install shinka-evolve[wandb] to use it."
            )
            self.enabled = False
            return
        except Exception as exc:
            logger.warning("Failed to import W&B: %s", exc)
            self.enabled = False
            return

        try:
            extra_config = _json_safe(getattr(evo_config, "wandb_config", {}) or {})
            if not isinstance(extra_config, dict):
                raise ValueError("wandb_config must be a dictionary")

            run_id = ensure_wandb_run_id(
                results_dir,
                getattr(evo_config, "wandb_run_id", None),
            )
            evo_config.wandb_run_id = run_id
            config = {
                "evolution": _json_safe(evo_config),
                "database": _json_safe(db_config),
                "job": _json_safe(job_config),
            }
            config.update(extra_config)
            init_kwargs = {
                "project": getattr(evo_config, "wandb_project", None)
                or "shinka-evolve",
                "entity": getattr(evo_config, "wandb_entity", None),
                "group": getattr(evo_config, "wandb_group", None),
                "name": getattr(evo_config, "wandb_name", None)
                or Path(results_dir).name,
                "mode": getattr(evo_config, "wandb_mode", None),
                "tags": getattr(evo_config, "wandb_tags", None) or None,
                "notes": getattr(evo_config, "wandb_notes", None),
                "dir": getattr(evo_config, "wandb_dir", None) or str(results_dir),
                "id": run_id,
                "resume": getattr(evo_config, "wandb_resume", "allow"),
                "config": config,
            }
            self._run = self._wandb.init(
                **{
                    key: value
                    for key, value in init_kwargs.items()
                    if value is not None
                }
            )
            self._define_metrics()
            logger.info("W&B logging initialized for '%s'", init_kwargs["project"])
        except Exception as exc:
            logger.warning("Failed to initialize W&B logging: %s", exc)
            self.finish()
            self.enabled = False

    def log_program(self, program: Program) -> None:
        if (
            not self.active
            or is_island_copy(program)
            or program.id in self._logged_program_ids
        ):
            return
        try:
            self._run.log(build_program_log_payload(program))
            self._logged_program_ids.add(program.id)
        except Exception as exc:
            logger.warning("Failed to log individual %s to W&B: %s", program.id, exc)

    def log_population_progress(
        self,
        *,
        db: Optional[ProgramDatabase],
        force: bool = False,
    ) -> None:
        """Log the current population/island snapshot on the canonical axis."""
        if not self.active or db is None:
            return
        try:
            programs = db.get_all_programs()
            evaluated_count = sum(
                1 for program in programs if not is_island_copy(program)
            )
            if (
                not force
                and self._last_population_evaluated_count is not None
                and evaluated_count <= self._last_population_evaluated_count
            ):
                return
            self._run.log(build_population_progress_payload(programs))
            self._last_population_evaluated_count = evaluated_count
        except Exception as exc:
            logger.warning("Failed to log W&B population progress: %s", exc)

    def log_final(
        self,
        *,
        db: Optional[ProgramDatabase],
        total_proposals_generated: Optional[int] = None,
        total_cost: Optional[float] = None,
    ) -> None:
        if not self.active or db is None:
            return
        try:
            programs = db.get_all_programs()
            final_summary = build_run_summary(
                programs,
                total_proposals_generated=total_proposals_generated,
                total_cost=total_cost,
            )
            final_payload = build_population_progress_payload(programs)
            final_payload.update(final_summary)
            self._run.log(final_payload)
            self._last_population_evaluated_count = sum(
                1 for program in programs if not is_island_copy(program)
            )
            self._run.summary.update(final_summary)
            table = self._wandb.Table(
                columns=PROGRAM_TABLE_COLUMNS,
                data=[program_table_row(program) for program in programs],
            )
            self._run.log({PROGRAM_TABLE_KEY: table})
        except Exception as exc:
            logger.warning("Failed to log final W&B summary: %s", exc)

    def finish(self) -> None:
        if self._run is None:
            return
        try:
            self._run.finish()
        except Exception as exc:
            logger.warning("Failed to finish W&B run cleanly: %s", exc)
        finally:
            self._run = None

    def _define_metrics(self) -> None:
        if not hasattr(self._run, "define_metric"):
            return
        self._run.define_metric(GENERATION_METRIC)
        self._run.define_metric(EVALUATED_COUNT_METRIC)
        self._run.define_metric(
            INDIVIDUAL_SCORE_METRIC,
        )
        self._run.define_metric("run/*")
        for metric_glob in (
            "population/count",
            "population/correct_count",
            "population/correct_rate",
            "population/best_score",
            "population/best_correct_score",
            "population/mean_score",
            "island/*",
            "cost/*",
            "timing/*",
        ):
            self._run.define_metric(
                metric_glob,
                step_metric=EVALUATED_COUNT_METRIC,
            )


def _program_model_name(program: Program) -> Optional[str]:
    metadata = program.metadata or {}
    llm_result = metadata.get("llm_result")
    nested_model = llm_result.get("model") if isinstance(llm_result, dict) else None
    value = metadata.get("model_name") or metadata.get("model") or nested_model
    return str(value) if value is not None else None


def _finite_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, (bool, str, bytes)):
        return None
    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except Exception:
            return None
    if not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _metric_segment(value: Any) -> str:
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return segment.strip("_") or "value"


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)
