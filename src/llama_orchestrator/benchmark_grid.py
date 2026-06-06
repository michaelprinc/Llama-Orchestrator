"""Grid benchmark planning, storage, and request-only execution."""

from __future__ import annotations

import itertools
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from llama_orchestrator.benchmark import (
    BenchmarkResult,
    BenchmarkSettings,
    get_benchmark_db_path,
    quick_benchmark_instance,
)
from llama_orchestrator.config import InstanceConfig, get_logs_dir

GridParameterCategory = Literal[
    "request",
    "runtime_static",
    "model_metadata",
    "model_runtime",
    "metadata_only",
    "blocked",
]
GridValueType = Literal["int", "float", "bool", "enum", "str"]
GridRunStatus = Literal["pending", "running", "ok", "failed", "stopped"]
GridSweepStatus = Literal["running", "ok", "failed", "stopped"]

DEFAULT_GRID_CONFIRM_LIMIT = 100
DEFAULT_GRID_HARD_LIMIT = 1000


@dataclass(frozen=True)
class GridParameterSpec:
    """One curated parameter that can be shown in the grid benchmark dialog."""

    name: str
    value_type: GridValueType
    category: GridParameterCategory
    default: int | float | str | bool | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    restart_required: bool = False
    choices: tuple[int | float | str | bool, ...] = ()
    read_only: bool = False
    description: str = ""
    execution_supported: bool = True


@dataclass(frozen=True)
class GridParameterRange:
    """User-selected values for one grid parameter."""

    name: str
    enabled: bool = True
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    values: tuple[int | float | str | bool, ...] = ()


@dataclass(frozen=True)
class GridCombination:
    """One concrete benchmark run in a grid sweep."""

    index: int
    parameters: dict[str, int | float | str | bool]


@dataclass(frozen=True)
class GridPlan:
    """Validated grid benchmark execution plan."""

    parameters: tuple[GridParameterRange, ...]
    confirm_limit: int = DEFAULT_GRID_CONFIRM_LIMIT
    hard_limit: int = DEFAULT_GRID_HARD_LIMIT

    def combinations(self) -> tuple[GridCombination, ...]:
        """Enumerate deterministic parameter combinations."""
        enabled_ranges = tuple(parameter for parameter in self.parameters if parameter.enabled)
        if not enabled_ranges:
            return (GridCombination(index=1, parameters={}),)

        value_sets = tuple(_enumerate_range(parameter) for parameter in enabled_ranges)
        total = 1
        for values in value_sets:
            total *= len(values)
        if total > self.hard_limit:
            raise ValueError(
                f"Grid has {total} combinations, above hard limit {self.hard_limit}."
            )

        combinations: list[GridCombination] = []
        for index, values in enumerate(itertools.product(*value_sets), start=1):
            combinations.append(
                GridCombination(
                    index=index,
                    parameters={
                        parameter.name: value
                        for parameter, value in zip(enabled_ranges, values, strict=True)
                    },
                )
            )
        return tuple(combinations)

    def combination_count(self) -> int:
        """Return the number of generated combinations."""
        return len(self.combinations())

    def needs_confirmation(self) -> bool:
        """Return True when the plan exceeds the soft confirmation threshold."""
        return self.combination_count() > self.confirm_limit

    def to_json_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable plan representation."""
        return {
            "parameters": [
                {
                    "name": parameter.name,
                    "enabled": parameter.enabled,
                    "minimum": parameter.minimum,
                    "maximum": parameter.maximum,
                    "step": parameter.step,
                    "values": list(parameter.values),
                }
                for parameter in self.parameters
            ],
            "confirm_limit": self.confirm_limit,
            "hard_limit": self.hard_limit,
        }


@dataclass(frozen=True)
class GridSweepRecord:
    """Persisted grid sweep identity."""

    sweep_id: str
    total_runs: int


@dataclass(frozen=True)
class GridRunRecord:
    """Persisted grid run identity."""

    sweep_id: str
    run_id: str
    combination_index: int
    parameters: dict[str, int | float | str | bool]


def sampling_parameter_catalog(settings: BenchmarkSettings | None = None) -> tuple[GridParameterSpec, ...]:
    """Return curated dynamic request parameters supported without restart."""
    active = settings
    return (
        GridParameterSpec(
            name="max_tokens",
            value_type="int",
            category="request",
            default=active.max_tokens if active else 200,
            minimum=1,
            maximum=131072,
            description="Maximum generated tokens.",
        ),
        GridParameterSpec(
            name="temperature",
            value_type="float",
            category="request",
            default=active.temperature if active else 0.0,
            minimum=0.0,
            maximum=5.0,
            description="Sampling temperature.",
        ),
        GridParameterSpec(
            name="top_p",
            value_type="float",
            category="request",
            default=active.top_p if active else None,
            minimum=0.0,
            maximum=1.0,
            description="Nucleus sampling probability.",
        ),
        GridParameterSpec(
            name="top_k",
            value_type="int",
            category="request",
            default=active.top_k if active else None,
            minimum=0,
            maximum=1000,
            description="Top-k sampling candidates.",
        ),
        GridParameterSpec(
            name="repeat_penalty",
            value_type="float",
            category="request",
            default=active.repeat_penalty if active else None,
            minimum=0.0,
            maximum=10.0,
            description="Repeat penalty.",
        ),
        GridParameterSpec(
            name="seed",
            value_type="int",
            category="request",
            default=active.seed if active else None,
            minimum=-1,
            maximum=2_147_483_647,
            description="Sampling seed.",
        ),
        GridParameterSpec(
            name="endpoint",
            value_type="enum",
            category="request",
            default=active.endpoint if active else "chat_completions",
            choices=("chat_completions", "completion"),
            description="HTTP endpoint used by the benchmark request.",
        ),
        GridParameterSpec(
            name="ignore_eos",
            value_type="bool",
            category="request",
            default=active.ignore_eos if active else False,
            choices=(False, True),
            description="Ask llama.cpp to ignore EOS for the request.",
        ),
    )


def request_parameter_catalog(settings: BenchmarkSettings | None = None) -> tuple[GridParameterSpec, ...]:
    """Backward-compatible alias for dynamic sampling request parameters."""
    return sampling_parameter_catalog(settings)


def runtime_static_parameter_catalog() -> tuple[GridParameterSpec, ...]:
    """Return curated restart-required runtime parameters for the GUI catalog."""
    return (
        GridParameterSpec("model.context_size", "int", "runtime_static", minimum=512, maximum=262144, restart_required=True, execution_supported=False),
        GridParameterSpec("model.batch_size", "int", "runtime_static", minimum=1, maximum=8192, restart_required=True, execution_supported=False),
        GridParameterSpec("server.parallel", "int", "runtime_static", minimum=1, maximum=64, restart_required=True, execution_supported=False),
        GridParameterSpec("gpu.layers", "int", "runtime_static", minimum=0, maximum=999, restart_required=True, execution_supported=False),
        GridParameterSpec("--ubatch-size", "int", "model_runtime", minimum=1, maximum=8192, restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-type-k", "enum", "model_runtime", choices=("f16", "q8_0", "q4_0", "q4_1", "iq4_nl"), restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-type-v", "enum", "model_runtime", choices=("f16", "q8_0", "q4_0", "q4_1", "iq4_nl"), restart_required=True, execution_supported=False),
        GridParameterSpec("--kv-offload", "bool", "model_runtime", choices=(False, True), restart_required=True, execution_supported=False),
        GridParameterSpec("--kv-unified", "bool", "model_runtime", choices=(False, True), restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-ram", "int", "model_runtime", minimum=0, maximum=1048576, restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-idle-slots", "int", "model_runtime", minimum=0, maximum=1024, restart_required=True, execution_supported=False),
        GridParameterSpec("--ctx-checkpoints", "int", "model_runtime", minimum=0, maximum=1024, restart_required=True, execution_supported=False),
        GridParameterSpec("--checkpoint-every-n-tokens", "int", "model_runtime", minimum=0, maximum=131072, restart_required=True, execution_supported=False),
        GridParameterSpec("--swa-full", "bool", "model_runtime", choices=(False, True), restart_required=True, execution_supported=False),
        GridParameterSpec("--flash-attn", "enum", "model_runtime", choices=("auto", "on", "off"), restart_required=True, execution_supported=False),
        GridParameterSpec("--spec-type", "enum", "model_runtime", choices=("none", "draft-simple", "draft-eagle3", "draft-mtp"), restart_required=True, execution_supported=False),
        GridParameterSpec("--spec-draft-n-max", "int", "model_runtime", minimum=1, maximum=64, restart_required=True, execution_supported=False),
        GridParameterSpec("--spec-draft-n-min", "int", "model_runtime", minimum=0, maximum=64, restart_required=True, execution_supported=False),
        GridParameterSpec("--spec-draft-p-min", "float", "model_runtime", minimum=0.0, maximum=1.0, restart_required=True, execution_supported=False),
        GridParameterSpec("--spec-draft-p-split", "float", "model_runtime", minimum=0.0, maximum=1.0, restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-type-k-draft", "enum", "model_runtime", choices=("f16", "q8_0", "q4_0", "q4_1", "iq4_nl"), restart_required=True, execution_supported=False),
        GridParameterSpec("--cache-type-v-draft", "enum", "model_runtime", choices=("f16", "q8_0", "q4_0", "q4_1", "iq4_nl"), restart_required=True, execution_supported=False),
        GridParameterSpec("--n-gpu-layers-draft", "int", "model_runtime", minimum=0, maximum=999, restart_required=True, execution_supported=False),
        GridParameterSpec("--model-draft", "str", "model_runtime", restart_required=True, execution_supported=False),
    )


def grid_parameter_catalog(
    config: InstanceConfig | None = None,
    settings: BenchmarkSettings | None = None,
) -> tuple[GridParameterSpec, ...]:
    """Return the full curated Grid benchmark dialog catalog."""
    metadata_specs = model_metadata_catalog(config) if config is not None else ()
    return (
        *runtime_static_parameter_catalog(),
        *sampling_parameter_catalog(settings),
        *metadata_specs,
    )


def model_metadata_catalog(config: InstanceConfig) -> tuple[GridParameterSpec, ...]:
    """Return read-only model metadata rows for dialog display."""
    metadata = config.model_metadata
    identity = metadata.identity if metadata else None
    context = metadata.context if metadata else None
    gguf = metadata.gguf_extracted if metadata else None
    values = {
        "architecture": identity.architecture if identity else "",
        "native_context_length": context.native_context_length if context else None,
        "n_layers": gguf.n_layers if gguf else None,
        "n_embd": gguf.n_embd if gguf else None,
        "n_attention_heads": gguf.n_attention_heads if gguf else None,
        "n_kv_heads": gguf.n_kv_heads if gguf else None,
        "head_dim_k": gguf.head_dim_k if gguf else None,
        "head_dim_v": gguf.head_dim_v if gguf else None,
        "n_experts": gguf.n_experts if gguf else None,
        "n_experts_used": gguf.n_experts_used if gguf else None,
    }
    return tuple(
        GridParameterSpec(
            name=name,
            value_type="str" if isinstance(value, str) else "int",
            category="model_metadata",
            default=value,
            read_only=True,
            description="Read-only GGUF/model metadata.",
        )
        for name, value in values.items()
    )


def default_request_grid_plan(settings: BenchmarkSettings) -> GridPlan:
    """Return a conservative two-run request-only plan for the GUI dialog default."""
    return GridPlan(
        parameters=(
            GridParameterRange("temperature", values=(settings.temperature, 0.2)),
        )
    )


def settings_for_combination(
    base_settings: BenchmarkSettings,
    combination: GridCombination,
) -> BenchmarkSettings:
    """Apply request parameters to benchmark settings without mutating the base settings."""
    request_names = {spec.name for spec in sampling_parameter_catalog(base_settings)}
    changes = {
        name: value
        for name, value in combination.parameters.items()
        if name in request_names
    }
    return replace(base_settings, **changes)


def unsupported_execution_parameters(plan: GridPlan) -> tuple[str, ...]:
    """Return enabled parameters that cannot run in the current request-only runner."""
    supported = {
        spec.name: spec.execution_supported
        for spec in grid_parameter_catalog(settings=None)
        if not spec.read_only
    }
    unsupported: list[str] = []
    for parameter in plan.parameters:
        if parameter.enabled and not supported.get(parameter.name, True):
            unsupported.append(parameter.name)
    return tuple(unsupported)


def init_grid_benchmark_db(db_path: Path | None = None) -> Path:
    """Initialize additive grid benchmark storage."""
    path = db_path or get_benchmark_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_sweeps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                instance_names_json TEXT NOT NULL,
                prompt_file TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                grid_spec_json TEXT NOT NULL,
                total_runs INTEGER NOT NULL,
                completed_runs INTEGER NOT NULL DEFAULT 0,
                stopped_at TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sweep_id TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                instance_name TEXT NOT NULL,
                combination_index INTEGER NOT NULL,
                parameters_json TEXT NOT NULL,
                quick_benchmark_id INTEGER,
                status TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                artifact_file TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(sweep_id) REFERENCES benchmark_sweeps(sweep_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_benchmark_runs_sweep "
            "ON benchmark_runs(sweep_id, combination_index)"
        )
    return path


def create_grid_sweep(
    *,
    instance_names: Sequence[str],
    settings: BenchmarkSettings,
    prompt_sha256: str,
    plan: GridPlan,
    db_path: Path | None = None,
    sweep_id: str | None = None,
) -> GridSweepRecord:
    """Create a running sweep row."""
    combinations = plan.combinations()
    created_at = _timestamp()
    active_sweep_id = sweep_id or f"grid-{created_at}-{uuid.uuid4().hex[:8]}"
    path = init_grid_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO benchmark_sweeps (
                sweep_id, created_at, instance_names_json, prompt_file, prompt_sha256,
                status, grid_spec_json, total_runs, completed_runs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                active_sweep_id,
                created_at,
                json.dumps(list(instance_names), ensure_ascii=False),
                str(settings.prompt_file),
                prompt_sha256,
                "running",
                json.dumps(plan.to_json_dict(), sort_keys=True, ensure_ascii=False),
                len(combinations) * len(instance_names),
                0,
            ),
        )
    return GridSweepRecord(sweep_id=active_sweep_id, total_runs=len(combinations) * len(instance_names))


def record_grid_run_start(
    *,
    sweep_id: str,
    instance_name: str,
    combination: GridCombination,
    db_path: Path | None = None,
) -> GridRunRecord:
    """Insert a running grid run row."""
    run_id = f"{sweep_id}-run-{combination.index:04d}-{uuid.uuid4().hex[:8]}"
    path = init_grid_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO benchmark_runs (
                sweep_id, run_id, instance_name, combination_index, parameters_json,
                status, metrics_json, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sweep_id,
                run_id,
                instance_name,
                combination.index,
                json.dumps(combination.parameters, sort_keys=True, ensure_ascii=False),
                "running",
                "{}",
                _timestamp(),
            ),
        )
    return GridRunRecord(
        sweep_id=sweep_id,
        run_id=run_id,
        combination_index=combination.index,
        parameters=dict(combination.parameters),
    )


def record_grid_run_finish(
    run: GridRunRecord,
    *,
    status: GridRunStatus,
    metrics: dict[str, Any] | None = None,
    quick_benchmark_id: int | None = None,
    artifact_file: str | None = None,
    error: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Finish a grid run row and increment completed sweep count."""
    path = init_grid_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE benchmark_runs
            SET status = ?, metrics_json = ?, quick_benchmark_id = ?, artifact_file = ?,
                error = ?, finished_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                json.dumps(metrics or {}, sort_keys=True, ensure_ascii=False),
                quick_benchmark_id,
                artifact_file,
                error,
                _timestamp(),
                run.run_id,
            ),
        )
        conn.execute(
            """
            UPDATE benchmark_sweeps
            SET completed_runs = completed_runs + 1
            WHERE sweep_id = ?
            """,
            (run.sweep_id,),
        )


def finish_grid_sweep(
    sweep_id: str,
    *,
    status: GridSweepStatus,
    error: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Mark a sweep finished, failed, or stopped."""
    path = init_grid_benchmark_db(db_path)
    stopped_at = _timestamp() if status in {"failed", "stopped"} else None
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE benchmark_sweeps
            SET status = ?, stopped_at = ?, error = ?
            WHERE sweep_id = ?
            """,
            (status, stopped_at, error, sweep_id),
        )


def latest_grid_runs(sweep_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return persisted runs for one sweep."""
    path = init_grid_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM benchmark_runs
            WHERE sweep_id = ?
            ORDER BY combination_index, id
            """,
            (sweep_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def run_request_grid_for_instance(
    config: InstanceConfig,
    *,
    base_settings: BenchmarkSettings,
    plan: GridPlan,
    should_stop: Callable[[], bool] | None = None,
    post_message: Callable[[str], None] | None = None,
    run_benchmark: Callable[[InstanceConfig, BenchmarkSettings], BenchmarkResult] = quick_benchmark_instance,
    db_path: Path | None = None,
) -> GridSweepRecord:
    """Run a request-only grid against one already reachable instance."""
    unsupported = unsupported_execution_parameters(plan)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Request-only grid cannot execute restart-required parameters: {joined}")
    prompt_sha256 = _prompt_sha256(base_settings.prompt_file)
    sweep = create_grid_sweep(
        instance_names=(config.name,),
        settings=base_settings,
        prompt_sha256=prompt_sha256,
        plan=plan,
        db_path=db_path,
    )
    combinations = plan.combinations()
    stopped = False
    try:
        for combination in combinations:
            if should_stop and should_stop():
                stopped = True
                break
            if post_message:
                post_message(
                    f"[Grid benchmark] {combination.index}/{len(combinations)} running: "
                    f"{config.name}"
                )
            run = record_grid_run_start(
                sweep_id=sweep.sweep_id,
                instance_name=config.name,
                combination=combination,
                db_path=db_path,
            )
            try:
                result = run_benchmark(config, settings_for_combination(base_settings, combination))
            except Exception as exc:
                record_grid_run_finish(
                    run,
                    status="failed",
                    error=str(exc),
                    db_path=db_path,
                )
                if post_message:
                    post_message(
                        f"[Grid benchmark] {combination.index}/{len(combinations)} failed: "
                        f"{config.name}: {exc}"
                    )
                continue

            record_grid_run_finish(
                run,
                status="ok" if result.status == "ok" else "failed",
                metrics=_metrics_from_benchmark_result(result),
                artifact_file=result.artifact_file,
                error=result.error,
                db_path=db_path,
            )
            if post_message:
                post_message(
                    f"[Grid benchmark] {combination.index}/{len(combinations)} "
                    f"{result.status}: {config.name}"
                )
        finish_grid_sweep(sweep.sweep_id, status="stopped" if stopped else "ok", db_path=db_path)
    except Exception as exc:
        finish_grid_sweep(sweep.sweep_id, status="failed", error=str(exc), db_path=db_path)
        raise
    return sweep


def get_grid_artifact_dir(instance_name: str, sweep_id: str) -> Path:
    """Return the per-sweep grid artifact directory."""
    return get_logs_dir() / instance_name / "benchmarks" / "grid" / sweep_id


def write_grid_summary_artifact(
    *,
    instance_name: str,
    sweep_id: str,
    runs: Sequence[dict[str, Any]],
) -> Path:
    """Write a small Markdown summary for a completed grid sweep."""
    artifact = get_grid_artifact_dir(instance_name, sweep_id) / "summary.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Grid Benchmark - {instance_name}",
        "",
        f"- Sweep ID: `{sweep_id}`",
        f"- Runs: `{len(runs)}`",
        "",
        "| # | Status | Parameters | Artifact | Error |",
        "|---|--------|------------|----------|-------|",
    ]
    for row in runs:
        lines.append(
            "| {index} | {status} | `{params}` | {artifact} | {error} |".format(
                index=row.get("combination_index", "-"),
                status=row.get("status", "-"),
                params=row.get("parameters_json", "{}"),
                artifact=row.get("artifact_file") or "-",
                error=row.get("error") or "-",
            )
        )
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return artifact


def _enumerate_range(parameter: GridParameterRange) -> tuple[int | float | str | bool, ...]:
    if parameter.values:
        return parameter.values
    if parameter.minimum is None or parameter.maximum is None or parameter.step is None:
        raise ValueError(f"{parameter.name} requires values or min/max/step.")
    if parameter.step <= 0:
        raise ValueError(f"{parameter.name} step must be positive.")
    if parameter.minimum > parameter.maximum:
        raise ValueError(f"{parameter.name} minimum cannot exceed maximum.")

    values: list[int | float] = []
    current = parameter.minimum
    decimal = any(isinstance(value, float) for value in (parameter.minimum, parameter.maximum, parameter.step))
    while current <= parameter.maximum:
        values.append(round(float(current), 10) if decimal else int(current))
        current = current + parameter.step
    return tuple(values)


def _prompt_sha256(path: Path) -> str:
    try:
        import hashlib

        return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    except OSError:
        return ""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _metrics_from_benchmark_result(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "output_tokens": result.output_tokens,
        "tokens_per_second": result.tokens_per_second,
        "latency_ms": result.latency_ms,
        "elapsed_ms": result.elapsed_ms,
        "dedicated_vram_mb": result.dedicated_vram_mb,
        "shared_ram_mb": result.shared_ram_mb,
        "total_gpu_memory_mb": result.total_gpu_memory_mb,
        "prompt_tokens": result.prompt_tokens,
        "tokens_cached": result.tokens_cached,
        "cache_hit_rate": result.cache_hit_rate,
        "speculative_mode": result.speculative_mode,
        "draft_acceptance_rate": result.draft_acceptance_rate,
    }
