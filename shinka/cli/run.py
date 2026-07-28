#!/usr/bin/env python3
"""Agent-friendly repo-only async CLI launcher for Shinka tasks."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Dict, Optional, Union, get_args, get_origin, get_type_hints

from shinka.core import ShinkaEvolveRunner, EvolutionConfig
from shinka.database import DatabaseConfig
from shinka.launch import (
    JobConfig,
    LocalJobConfig,
    SecureJobConfig,
    validate_secure_job_config,
)
from shinka.cli.run_config import load_optional_yaml_config

SUPPORTED_INITIAL_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".jl": "julia",
    ".go": "go",
    ".sv": "verilog",
    ".rs": "rust",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".cu": "cuda",
    ".json": "json",
    ".f90": "fortran",
    ".f95": "fortran",
    ".f03": "fortran",
    ".f08": "fortran",
}

INITIAL_EXTENSION_PRIORITY: list[str] = [
    ".py",
    ".go",
    ".sv",
    ".jl",
    ".rs",
    ".cpp",
    ".cc",
    ".cxx",
    ".cu",
    ".swift",
    ".json",
    ".f90",
    ".f95",
    ".f03",
    ".f08",
]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def _resolve_runner_bool(
    cli_value: Optional[bool], runner_config: Dict[str, Any], key: str, default: bool
) -> bool:
    if cli_value is not None:
        return cli_value
    if key in runner_config:
        return bool(runner_config[key])
    return default


def _build_parser() -> argparse.ArgumentParser:
    description = (
        "Run async Shinka evolution from a task directory.\n\n"
        "Task directory contract:\n"
        "  - evaluator code (evaluate.py for trusted-local compatibility)\n"
        "  - seed_repo/ candidate directory, unless evo.seed_repo_path or "
        "--seed-repo-path is provided; Shinka initializes Git when needed"
    )
    epilog = (
        "Override grammar:\n"
        "  --set <namespace>.<field>=<value>\n"
        "  namespaces: evo, db, job\n"
        "  list/dict values must be valid JSON\n"
        "  bool values: true,false,1,0,yes,no (case-insensitive)\n\n"
        "Common evo settings via --set:\n"
        "  budget: --set evo.max_api_costs=0.5\n"
        "  models: --set "
        'evo.llm_models=\'["gpt-5-mini","gemini-3-flash-preview"]\'\n'
        '  patching: --set evo.patch_types=\'["diff","full"]\' '
        "--set evo.patch_type_probs='[0.7,0.3]'\n"
        '  llm kwargs: --set evo.llm_kwargs=\'{"temperatures":[0.0,0.5,1.0],'
        '"max_tokens":16384}\'\n'
        "  quality controls: --set evo.max_patch_resamples=3 "
        "--set evo.max_patch_attempts=1 --set evo.max_novelty_attempts=3\n"
        "  embeddings: --set evo.embedding_model=text-embedding-3-small "
        "--set evo.code_embed_sim_threshold=0.99\n"
        "              --set "
        "evo.embedding_model=local/text-embeddings-inference@http://localhost:8080/v1\n\n"
        "Common db settings via --set:\n"
        "  islands: --set db.num_islands=2\n"
        "  parent selection: --set db.parent_selection_strategy=weighted\n"
        "  archive: --set db.archive_size=40 --set db.num_archive_inspirations=1\n"
        "  migration: --set db.migration_interval=10 --set db.migration_rate=0.0\n\n"
        "Examples:\n"
        "  Minimal:\n"
        "    shinka_run --task-dir examples/circle_packing "
        "--results_dir results/circle_small --num_generations 20\n\n"
        "  With overrides:\n"
        "    shinka_run --task-dir examples/circle_packing "
        "--results_dir results/circle_custom --num_generations 50 "
        "--set db.num_islands=2 --set db.parent_selection_strategy=weighted "
        "--set job.time=00:10:00 "
        "--set job.activate_script=.venv/bin/activate "
        "--set "
        'evo.llm_models=\'["gpt-5-mini","gemini-3-flash-preview"]\'\n\n'
        "Failure behavior:\n"
        "  - unknown namespace/field: non-zero exit\n"
        "  - invalid value type: non-zero exit\n"
        "  - missing evaluate.py, missing seed repo, or invalid --config-fname YAML: "
        "non-zero exit\n\n"
        "Precedence:\n"
        "  - --config-fname YAML loads first; --set overrides config YAML\n"
        "  - --results_dir always sets evo.results_dir\n"
        "  - --num_generations always sets evo.num_generations"
    )
    parser = argparse.ArgumentParser(
        prog="shinka_run",
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    required_group = parser.add_argument_group("required arguments")
    required_group.add_argument(
        "--task-dir",
        type=Path,
        required=True,
        help="Directory containing evaluate.py and seed_repo/ by default.",
    )
    required_group.add_argument(
        "--seed-repo-path",
        type=Path,
        default=None,
        help=(
            "Seed candidate directory. Shinka initializes a Git baseline when "
            "needed. Defaults to TASK_DIR/seed_repo."
        ),
    )
    required_group.add_argument(
        "--results_dir",
        type=Path,
        required=True,
        help=(
            "Output directory for run artifacts/logs/databases. "
            "Authoritative: always sets evo.results_dir."
        ),
    )
    required_group.add_argument(
        "--num_generations",
        type=_positive_int,
        required=True,
        help=(
            "Number of generations to run. "
            "Authoritative: always sets evo.num_generations."
        ),
    )

    override_group = parser.add_argument_group("overrides")
    override_group.add_argument(
        "--evaluation-mode",
        choices=("trusted_local", "secure"),
        default=None,
        help=(
            "Select the evaluation boundary explicitly. secure is required for "
            "sealed/private/adversarial tasks; trusted_local is public/cooperative only."
        ),
    )
    override_group.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="NS.FIELD=VALUE",
        help=(
            "Repeatable namespaced override.\n"
            "Examples: --set evo.max_patch_attempts=4 "
            "--set db.num_islands=2 "
            "--set job.extra_cmd_args='{\"seed\":42}'"
        ),
    )
    override_group.add_argument(
        "--config-fname",
        type=str,
        default=None,
        help=(
            "Optional YAML config loaded before --set. Relative paths resolve from "
            "--task-dir. Supports evo/db/job or evo_config/db_config/job_config."
        ),
    )

    concurrency_group = parser.add_argument_group("concurrency")
    concurrency_group.add_argument(
        "--max-evaluation-jobs",
        type=_positive_int,
        default=None,
        help="Override ShinkaEvolveRunner max_evaluation_jobs.",
    )
    concurrency_group.add_argument(
        "--max-proposal-jobs",
        type=_positive_int,
        default=None,
        help="Override ShinkaEvolveRunner max_proposal_jobs.",
    )
    concurrency_group.add_argument(
        "--max-db-workers",
        type=_positive_int,
        default=None,
        help="Override ShinkaEvolveRunner max_db_workers.",
    )

    output_group = parser.add_argument_group("output/verbosity")
    output_group.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable verbose runner logging (default: enabled; use --no-verbose to disable).",
    )
    output_group.add_argument(
        "--debug",
        action="store_true",
        help="Enable extra async runner diagnostics.",
    )
    return parser


def _field_types() -> Dict[str, Dict[str, Any]]:
    evo_hints = get_type_hints(EvolutionConfig)
    db_hints = get_type_hints(DatabaseConfig)
    local_job_hints = get_type_hints(LocalJobConfig)
    secure_job_hints = get_type_hints(SecureJobConfig)
    return {
        "evo": {field.name: evo_hints[field.name] for field in fields(EvolutionConfig)},
        "db": {field.name: db_hints[field.name] for field in fields(DatabaseConfig)},
        "job": {
            **{
                field.name: local_job_hints[field.name]
                for field in fields(LocalJobConfig)
            },
            **{
                field.name: secure_job_hints[field.name]
                for field in fields(SecureJobConfig)
            },
        },
    }


def _coerce_bool(raw_value: str, key: str) -> bool:
    lowered = raw_value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid bool for {key}: {raw_value}. Use true/false/1/0/yes/no.")


def _coerce_scalar(raw_value: str, target_type: type, key: str) -> Any:
    if target_type is str:
        return raw_value
    if target_type is bool:
        return _coerce_bool(raw_value, key)
    if target_type is int:
        try:
            return int(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid int for {key}: {raw_value}") from exc
    if target_type is float:
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid float for {key}: {raw_value}") from exc
    return raw_value


def _coerce_json_container(raw_value: str, expected_container: type, key: str) -> Any:
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON for {key}: {raw_value}. "
            f"Expected {expected_container.__name__} JSON."
        ) from exc

    if not isinstance(parsed, expected_container):
        raise ValueError(
            f"Invalid type for {key}: expected {expected_container.__name__}, "
            f"got {type(parsed).__name__}."
        )
    return parsed


def _coerce_override_value(raw_value: str, target_type: Any, key: str) -> Any:
    origin = get_origin(target_type)
    args = get_args(target_type)

    if target_type is Any:
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value

    if target_type in {dict, list, str, bool, int, float}:
        if target_type is dict:
            return _coerce_json_container(raw_value, dict, key)
        if target_type is list:
            return _coerce_json_container(raw_value, list, key)
        return _coerce_scalar(raw_value, target_type, key)

    if origin is Union:
        if type(None) in args and raw_value.strip().lower() in {"none", "null"}:
            return None
        candidates = [candidate for candidate in args if candidate is not type(None)]
        last_error: Optional[Exception] = None
        for candidate in candidates:
            try:
                return _coerce_override_value(raw_value, candidate, key)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error is not None:
            raise ValueError(str(last_error))
        return raw_value

    if origin in {dict, Dict}:
        return _coerce_json_container(raw_value, dict, key)

    if origin in {list, tuple}:
        return _coerce_json_container(raw_value, list, key)

    return _coerce_scalar(raw_value, str, key)


def _parse_override_token(token: str) -> tuple[str, str, str]:
    if "=" not in token:
        raise ValueError(f"Invalid override '{token}'. Expected NS.FIELD=VALUE.")
    key, raw_value = token.split("=", 1)
    if "." not in key:
        raise ValueError(
            f"Invalid override key '{key}'. Expected namespaced key NS.FIELD."
        )
    namespace, field_name = key.split(".", 1)
    if not field_name:
        raise ValueError(f"Missing field name in override '{token}'.")
    return namespace, field_name, raw_value


def _parse_overrides(
    tokens: list[str], allowed_field_types: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    parsed: Dict[str, Dict[str, Any]] = {"evo": {}, "db": {}, "job": {}}
    for token in tokens:
        namespace, field_name, raw_value = _parse_override_token(token)
        if namespace not in allowed_field_types:
            valid_namespaces = ", ".join(sorted(allowed_field_types.keys()))
            raise ValueError(
                f"Invalid namespace '{namespace}' in '{token}'. "
                f"Use one of: {valid_namespaces}."
            )
        field_types_for_ns = allowed_field_types[namespace]
        if field_name not in field_types_for_ns:
            valid_fields = ", ".join(sorted(field_types_for_ns.keys()))
            raise ValueError(
                f"Unknown field '{namespace}.{field_name}'. "
                f"Valid {namespace} fields: {valid_fields}"
            )
        target_type = field_types_for_ns[field_name]
        parsed[namespace][field_name] = _coerce_override_value(
            raw_value, target_type, f"{namespace}.{field_name}"
        )
    return parsed


def _build_default_evo_values(
    *,
    language: str,
    seed_repo_path: Path,
    results_dir: Path,
    num_generations: int,
) -> Dict[str, Any]:
    return asdict(
        EvolutionConfig(
            seed_repo_path=str(seed_repo_path),
            mutable_paths=[],
            num_generations=num_generations,
            job_type="local",
            language=language,
            results_dir=str(results_dir),
        )
    )


def _build_default_db_values() -> Dict[str, Any]:
    return asdict(DatabaseConfig())


def _build_default_job_values(
    *,
    task_dir: Path,
    evaluation_mode: str,
) -> Dict[str, Any]:
    if evaluation_mode == "secure":
        return asdict(SecureJobConfig(evaluator_repo_path=str(task_dir)))
    return asdict(LocalJobConfig(eval_program_path=str(task_dir / "evaluate.py")))


def _resolve_job_paths(
    *,
    task_dir: Path,
    job_values: Dict[str, Any],
    evaluation_mode: str,
) -> None:
    field_names = (
        ("evaluator_repo_path", "dependency_manifest_path")
        if evaluation_mode == "secure"
        else ("eval_program_path",)
    )
    for field_name in field_names:
        raw_path = job_values.get(field_name)
        if raw_path is None:
            continue
        path = Path(str(raw_path))
        path = (task_dir / path).resolve() if not path.is_absolute() else path.resolve()
        job_values[field_name] = str(path)


def _validate_task_dir(task_dir: Path) -> None:
    if not task_dir.exists():
        raise FileNotFoundError(f"Task dir does not exist: {task_dir}")
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Task dir is not a directory: {task_dir}")


def _resolve_seed_repo_path(
    *,
    task_dir: Path,
    cli_seed_repo_path: Optional[Path],
    evo_overrides: Dict[str, Any],
    evaluation_mode: str,
) -> Path:
    raw_seed_path = evo_overrides.get("seed_repo_path")
    if raw_seed_path:
        seed_repo_path = Path(str(raw_seed_path))
    elif cli_seed_repo_path is not None:
        seed_repo_path = cli_seed_repo_path
    else:
        seed_repo_path = task_dir / "seed_repo"

    if not seed_repo_path.is_absolute():
        seed_repo_path = (task_dir / seed_repo_path).resolve()
    else:
        seed_repo_path = seed_repo_path.resolve()
    if not seed_repo_path.exists():
        raise FileNotFoundError(f"Seed repo does not exist: {seed_repo_path}")
    if seed_repo_path.is_symlink() or not seed_repo_path.is_dir():
        raise FileNotFoundError(
            f"Seed candidate is missing or unsafe: {seed_repo_path}"
        )
    return seed_repo_path


def _build_runner(
    *,
    args: argparse.Namespace,
    evo_config: EvolutionConfig,
    db_config: DatabaseConfig,
    job_config: JobConfig,
    evaluate_str: Optional[str],
) -> ShinkaEvolveRunner:
    runner_kwargs: Dict[str, Any] = {
        "evo_config": evo_config,
        "job_config": job_config,
        "db_config": db_config,
        "banner_style": "minimal",
        "verbose": args.verbose,
        "debug": args.debug,
        "evaluate_str": evaluate_str,
    }
    if args.max_evaluation_jobs is not None:
        runner_kwargs["max_evaluation_jobs"] = args.max_evaluation_jobs
    if args.max_proposal_jobs is not None:
        runner_kwargs["max_proposal_jobs"] = args.max_proposal_jobs
    if args.max_db_workers is not None:
        runner_kwargs["max_db_workers"] = args.max_db_workers
    return ShinkaEvolveRunner(**runner_kwargs)


def _resolve_secure_evo_paths(
    *,
    task_dir: Path,
    evo_values: Dict[str, Any],
) -> None:
    profiles = dict(evo_values.get("agent_auth_profiles") or {})
    for agent, raw_path in profiles.items():
        path = Path(str(raw_path)).expanduser()
        profiles[agent] = str(
            (task_dir / path).resolve() if not path.is_absolute() else path.resolve()
        )
    evo_values["agent_auth_profiles"] = profiles
    for field_name in ("secure_state_root", "headless_session_home_root"):
        raw_path = evo_values.get(field_name)
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser()
        evo_values[field_name] = str(
            (task_dir / path).resolve() if not path.is_absolute() else path.resolve()
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    task_dir = args.task_dir.resolve()
    results_dir = args.results_dir.resolve()

    try:
        _validate_task_dir(task_dir)
        allowed_types = _field_types()
        file_overrides, runner_config = load_optional_yaml_config(
            task_dir=task_dir,
            config_fname=args.config_fname,
            allowed_field_types=allowed_types,
        )
        parsed_overrides = _parse_overrides(args.overrides, allowed_types)

        merged_evo_overrides = {
            **file_overrides["evo"],
            **parsed_overrides["evo"],
        }
        if args.evaluation_mode is not None:
            merged_evo_overrides["evaluation_mode"] = args.evaluation_mode
        evaluation_mode = str(
            merged_evo_overrides.get("evaluation_mode", "trusted_local")
        )
        if evaluation_mode not in {"trusted_local", "secure"}:
            raise ValueError("evaluation_mode must be 'trusted_local' or 'secure'")
        language = str(merged_evo_overrides.get("language", "python"))
        seed_repo_path = _resolve_seed_repo_path(
            task_dir=task_dir,
            cli_seed_repo_path=args.seed_repo_path,
            evo_overrides=merged_evo_overrides,
            evaluation_mode=evaluation_mode,
        )
        evo_values = _build_default_evo_values(
            language=language,
            seed_repo_path=seed_repo_path,
            results_dir=results_dir,
            num_generations=args.num_generations,
        )
        evo_values.update(file_overrides["evo"])
        evo_values.update(parsed_overrides["evo"])
        evo_values["seed_repo_path"] = str(seed_repo_path)
        evo_values["results_dir"] = str(results_dir)
        evo_values["num_generations"] = args.num_generations
        evo_values["evaluation_mode"] = evaluation_mode
        if evaluation_mode == "secure":
            _resolve_secure_evo_paths(task_dir=task_dir, evo_values=evo_values)

        db_values = _build_default_db_values()
        db_values.update(file_overrides["db"])
        db_values.update(parsed_overrides["db"])

        job_values = _build_default_job_values(
            task_dir=task_dir,
            evaluation_mode=evaluation_mode,
        )
        job_values.update(file_overrides["job"])
        job_values.update(parsed_overrides["job"])
        _resolve_job_paths(
            task_dir=task_dir,
            job_values=job_values,
            evaluation_mode=evaluation_mode,
        )

        if args.max_evaluation_jobs is None:
            args.max_evaluation_jobs = runner_config.get("max_evaluation_jobs")
        if args.max_proposal_jobs is None:
            args.max_proposal_jobs = runner_config.get("max_proposal_jobs")
        if args.max_db_workers is None:
            args.max_db_workers = runner_config.get("max_db_workers")
        args.verbose = _resolve_runner_bool(
            args.verbose, runner_config, "verbose", True
        )
        args.debug = args.debug or bool(runner_config.get("debug", False))

        evo_config = EvolutionConfig(**evo_values)
        db_config = DatabaseConfig(**db_values)
        if evaluation_mode == "secure":
            job_config = SecureJobConfig(**job_values)
            validate_secure_job_config(
                job_config,
                mutation_image=evo_config.mutation_image,
            )
            evaluator_root = Path(job_config.evaluator_repo_path or "")
            evaluator_entrypoint = evaluator_root / job_config.evaluator_entrypoint
            if (
                evaluator_root.is_symlink()
                or not evaluator_root.is_dir()
                or evaluator_entrypoint.is_symlink()
                or not evaluator_entrypoint.is_file()
            ):
                raise FileNotFoundError(
                    "Secure evaluator repository or entrypoint is missing/unsafe: "
                    f"{evaluator_entrypoint}"
                )
            evaluate_str = None
        else:
            job_config = LocalJobConfig(**job_values)
            evaluate_path = Path(job_config.eval_program_path or "")
            if evaluate_path.is_symlink() or not evaluate_path.is_file():
                raise FileNotFoundError(
                    f"Trusted-local evaluator is missing or unsafe: {evaluate_path}"
                )
            evaluate_str = evaluate_path.read_text(encoding="utf-8")

        runner = _build_runner(
            args=args,
            evo_config=evo_config,
            db_config=db_config,
            job_config=job_config,
            evaluate_str=evaluate_str,
        )
    except Exception as exc:  # noqa: BLE001
        parser.error(str(exc))

    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
