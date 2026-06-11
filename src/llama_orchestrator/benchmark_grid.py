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
from llama_orchestrator.config import InstanceConfig, get_logs_dir, get_state_dir

GridParameterCategory = Literal[
    "request",
    "runtime_static",
    "model_metadata",
    "model_runtime",
    "metadata_only",
    "blocked",
]
GridValueType = Literal["int", "float", "bool", "enum", "str"]
ParameterKind = Literal["scalar_range", "enum_list", "boolean", "composite", "read_only_metadata"]
GridRunStatus = Literal["pending", "running", "ok", "failed", "stopped"]
GridSweepStatus = Literal["running", "ok", "failed", "stopped"]

DEFAULT_GRID_CONFIRM_LIMIT = 100
DEFAULT_GRID_HARD_LIMIT = 1000
KV_CACHE_PARAMETER_NAME = "kv_cache"
KV_CACHE_PROFILE_PARAMETER_NAME = "kv_cache_profile"
KV_CACHE_TYPES = ("f16", "q8_0", "q4_0", "q4_1", "iq4_nl")
DRAFT_GRID_PARAMETER_NAMES = {
    "--cache-type-k-draft",
    "--cache-type-v-draft",
    "--n-gpu-layers-draft",
    "--model-draft",
    "--spec-draft-n-max",
    "--spec-draft-n-min",
    "--spec-draft-p-min",
    "--spec-draft-p-split",
}


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
    kind: ParameterKind = "scalar_range"
    display_name: str = ""


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
class KvCacheProfile:
    """One named K/V cache benchmark profile."""

    id: str
    label: str
    cache_type_k: str
    cache_type_v: str
    enabled: bool = True
    cache_type_k_draft: str | None = None
    cache_type_v_draft: str | None = None
    notes: str = ""


DEFAULT_KV_CACHE_PROFILES: tuple[KvCacheProfile, ...] = (
    KvCacheProfile("f16_baseline", "f16 / f16 baseline", "f16", "f16", notes="baseline"),
    KvCacheProfile("q8_pair", "q8_0 / q8_0", "q8_0", "q8_0", notes="safe quantized"),
    KvCacheProfile("q4_0_pair", "q4_0 / q4_0", "q4_0", "q4_0", notes="memory saving"),
    KvCacheProfile("q4_1_pair", "q4_1 / q4_1", "q4_1", "q4_1", notes="alternative q4"),
    KvCacheProfile("iq4_nl_pair", "iq4_nl / iq4_nl", "iq4_nl", "iq4_nl", notes="low memory"),
)
ASYMMETRIC_KV_CACHE_PROFILES: tuple[KvCacheProfile, ...] = (
    KvCacheProfile("q8_k_q4_v", "q8_0 / q4_0", "q8_0", "q4_0", enabled=False, notes="asymmetric test"),
    KvCacheProfile("f16_k_q4_v", "f16 / q4_0", "f16", "q4_0", enabled=False, notes="asymmetric test"),
    KvCacheProfile("q8_k_iq4_v", "q8_0 / iq4_nl", "q8_0", "iq4_nl", enabled=False, notes="asymmetric test"),
)
DEFAULT_KV_CACHE_PROFILE_IDS = tuple(profile.id for profile in DEFAULT_KV_CACHE_PROFILES)


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
            raw_parameters = {
                parameter.name: value
                for parameter, value in zip(enabled_ranges, values, strict=True)
            }
            combinations.append(
                GridCombination(
                    index=index,
                    parameters=_expand_composite_parameters(raw_parameters),
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
            kind="enum_list",
            description="HTTP endpoint used by the benchmark request.",
        ),
        GridParameterSpec(
            name="ignore_eos",
            value_type="bool",
            category="request",
            default=active.ignore_eos if active else False,
            choices=(False, True),
            kind="boolean",
            description="Ask llama.cpp to ignore EOS for the request.",
        ),
    )


def request_parameter_catalog(settings: BenchmarkSettings | None = None) -> tuple[GridParameterSpec, ...]:
    """Backward-compatible alias for dynamic sampling request parameters."""
    return sampling_parameter_catalog(settings)


def all_kv_cache_profiles() -> tuple[KvCacheProfile, ...]:
    """Return default, asymmetric, and full-matrix KV cache profile definitions."""
    generated: list[KvCacheProfile] = []
    known_ids = {
        profile.id
        for profile in (*DEFAULT_KV_CACHE_PROFILES, *ASYMMETRIC_KV_CACHE_PROFILES)
    }
    for cache_type_k in KV_CACHE_TYPES:
        for cache_type_v in KV_CACHE_TYPES:
            profile_id = _kv_cache_matrix_profile_id(cache_type_k, cache_type_v)
            if profile_id in known_ids:
                continue
            generated.append(
                KvCacheProfile(
                    profile_id,
                    f"{cache_type_k} / {cache_type_v}",
                    cache_type_k,
                    cache_type_v,
                    enabled=cache_type_k == cache_type_v,
                    notes="full matrix",
                )
            )
    return (*DEFAULT_KV_CACHE_PROFILES, *ASYMMETRIC_KV_CACHE_PROFILES, *generated)


def kv_cache_profile_from_id(profile_id: str) -> KvCacheProfile:
    """Resolve a persisted KV cache profile id."""
    for profile in all_kv_cache_profiles():
        if profile.id == profile_id:
            return profile
    raise ValueError(f"Unknown KV cache profile: {profile_id}")


def kv_cache_profiles_for_preset(preset: str) -> tuple[KvCacheProfile, ...]:
    """Return profiles for the supported GUI preset modes."""
    if preset == "baseline":
        return DEFAULT_KV_CACHE_PROFILES[:1]
    if preset == "paired":
        return DEFAULT_KV_CACHE_PROFILES
    if preset == "memory":
        return tuple(
            profile
            for profile in DEFAULT_KV_CACHE_PROFILES
            if profile.cache_type_k in {"q4_0", "q4_1", "iq4_nl"}
        )
    if preset == "asymmetric":
        return (*DEFAULT_KV_CACHE_PROFILES, *ASYMMETRIC_KV_CACHE_PROFILES)
    if preset == "full":
        return tuple(
            KvCacheProfile(
                _kv_cache_matrix_profile_id(cache_type_k, cache_type_v),
                f"{cache_type_k} / {cache_type_v}",
                cache_type_k,
                cache_type_v,
                notes="full matrix",
            )
            for cache_type_k in KV_CACHE_TYPES
            for cache_type_v in KV_CACHE_TYPES
        )
    raise ValueError(f"Unknown KV cache preset: {preset}")


def runtime_static_parameter_catalog() -> tuple[GridParameterSpec, ...]:
    """Return curated restart-required runtime parameters for the GUI catalog."""
    return _runtime_static_parameter_catalog()


def _runtime_static_parameter_catalog(
    config: InstanceConfig | None = None,
    *,
    include_expert_raw_kv: bool = False,
) -> tuple[GridParameterSpec, ...]:
    draft_supported = config is None or _config_has_draft_runtime(config)
    draft_status = "" if draft_supported else "Requires --model-draft or speculative decoding."
    raw_kv_specs: tuple[GridParameterSpec, ...] = ()
    if include_expert_raw_kv:
        raw_kv_specs = (
            GridParameterSpec(
                "--cache-type-k",
                "enum",
                "model_runtime",
                choices=KV_CACHE_TYPES,
                restart_required=True,
                kind="enum_list",
                description="Expert raw K cache override.",
            ),
            GridParameterSpec(
                "--cache-type-v",
                "enum",
                "model_runtime",
                choices=KV_CACHE_TYPES,
                restart_required=True,
                kind="enum_list",
                description="Expert raw V cache override.",
            ),
        )
    return (
        GridParameterSpec("model.context_size", "int", "runtime_static", minimum=512, maximum=262144, restart_required=True),
        GridParameterSpec("model.batch_size", "int", "runtime_static", minimum=1, maximum=8192, restart_required=True),
        GridParameterSpec("server.parallel", "int", "runtime_static", minimum=1, maximum=64, restart_required=True),
        GridParameterSpec("gpu.layers", "int", "runtime_static", minimum=0, maximum=999, restart_required=True),
        GridParameterSpec("--ubatch-size", "int", "model_runtime", minimum=1, maximum=8192, restart_required=True),
        GridParameterSpec(
            KV_CACHE_PARAMETER_NAME,
            "enum",
            "model_runtime",
            default="Custom",
            choices=DEFAULT_KV_CACHE_PROFILE_IDS,
            restart_required=True,
            kind="composite",
            display_name="KV Cache profiles",
            description="Named K/V cache combinations expanded to --cache-type-k and --cache-type-v.",
        ),
        *raw_kv_specs,
        GridParameterSpec("--kv-offload", "bool", "model_runtime", choices=(False, True), restart_required=True),
        GridParameterSpec("--kv-unified", "bool", "model_runtime", choices=(False, True), restart_required=True),
        GridParameterSpec("--cache-ram", "int", "model_runtime", minimum=0, maximum=1048576, restart_required=True),
        GridParameterSpec("--cache-idle-slots", "int", "model_runtime", minimum=0, maximum=1024, restart_required=True),
        GridParameterSpec("--ctx-checkpoints", "int", "model_runtime", minimum=0, maximum=1024, restart_required=True),
        GridParameterSpec("--checkpoint-every-n-tokens", "int", "model_runtime", minimum=0, maximum=131072, restart_required=True),
        GridParameterSpec("--swa-full", "bool", "model_runtime", choices=(False, True), restart_required=True),
        GridParameterSpec("--flash-attn", "enum", "model_runtime", choices=("auto", "on", "off"), restart_required=True),
        GridParameterSpec("--spec-type", "enum", "model_runtime", choices=("none", "draft-simple", "draft-eagle3", "draft-mtp"), restart_required=True),
        GridParameterSpec("--spec-draft-n-max", "int", "model_runtime", minimum=1, maximum=64, restart_required=True, execution_supported=draft_supported, description=draft_status),
        GridParameterSpec("--spec-draft-n-min", "int", "model_runtime", minimum=0, maximum=64, restart_required=True, execution_supported=draft_supported, description=draft_status),
        GridParameterSpec("--spec-draft-p-min", "float", "model_runtime", minimum=0.0, maximum=1.0, restart_required=True, execution_supported=draft_supported, description=draft_status),
        GridParameterSpec("--spec-draft-p-split", "float", "model_runtime", minimum=0.0, maximum=1.0, restart_required=True, execution_supported=draft_supported, description=draft_status),
        GridParameterSpec("--cache-type-k-draft", "enum", "model_runtime", choices=KV_CACHE_TYPES, restart_required=True, execution_supported=draft_supported, kind="enum_list", description=draft_status),
        GridParameterSpec("--cache-type-v-draft", "enum", "model_runtime", choices=KV_CACHE_TYPES, restart_required=True, execution_supported=draft_supported, kind="enum_list", description=draft_status),
        GridParameterSpec("--n-gpu-layers-draft", "int", "model_runtime", minimum=0, maximum=999, restart_required=True, execution_supported=draft_supported, description=draft_status),
        GridParameterSpec("--model-draft", "str", "model_runtime", restart_required=True, execution_supported=draft_supported, description=draft_status),
    )


def grid_parameter_catalog(
    config: InstanceConfig | None = None,
    settings: BenchmarkSettings | None = None,
    *,
    include_expert_raw_kv: bool = False,
) -> tuple[GridParameterSpec, ...]:
    """Return the full curated Grid benchmark dialog catalog."""
    metadata_specs = model_metadata_catalog(config) if config is not None else ()
    return (
        *_runtime_static_parameter_catalog(config, include_expert_raw_kv=include_expert_raw_kv),
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
            kind="read_only_metadata",
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


def get_grid_settings_path() -> Path:
    """Return the persisted GUI grid benchmark settings path."""
    return get_state_dir() / "grid_benchmark_settings.json"


def save_grid_plan(plan: GridPlan, path: Path | None = None) -> Path:
    """Persist the Grid benchmark dialog configuration."""
    settings_path = path or get_grid_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(plan.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return settings_path


def load_grid_plan(path: Path | None = None) -> GridPlan:
    """Load the persisted Grid benchmark dialog configuration."""
    settings_path = path or get_grid_settings_path()
    if not settings_path.exists():
        return GridPlan(parameters=())
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GridPlan(parameters=())
    parameters = []
    for item in data.get("parameters", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parameters.append(
            GridParameterRange(
                name=name,
                enabled=bool(item.get("enabled", True)),
                minimum=item.get("minimum"),
                maximum=item.get("maximum"),
                step=item.get("step"),
                values=tuple(item.get("values") or ()),
            )
        )
    return GridPlan(
        parameters=tuple(parameters),
        confirm_limit=int(data.get("confirm_limit") or DEFAULT_GRID_CONFIRM_LIMIT),
        hard_limit=int(data.get("hard_limit") or DEFAULT_GRID_HARD_LIMIT),
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


def plan_requires_restart(plan: GridPlan) -> bool:
    """Return True when any enabled parameter requires a runtime restart."""
    specs = {
        spec.name: spec
        for spec in grid_parameter_catalog(settings=None, include_expert_raw_kv=True)
    }
    return any(
        parameter.enabled and specs.get(parameter.name, GridParameterSpec(parameter.name, "str", "request")).restart_required
        for parameter in plan.parameters
    )


def unsupported_execution_parameters(plan: GridPlan) -> tuple[str, ...]:
    """Return enabled parameters that cannot run in the current request-only runner."""
    supported = {
        spec.name: spec.execution_supported
        for spec in grid_parameter_catalog(settings=None, include_expert_raw_kv=True)
        if not spec.read_only
    }
    unsupported: list[str] = []
    for parameter in plan.parameters:
        if parameter.enabled and not supported.get(parameter.name, True):
            unsupported.append(parameter.name)
    return tuple(unsupported)


def apply_runtime_combination(config: InstanceConfig, combination: GridCombination) -> InstanceConfig:
    """Apply restart-required grid values to an in-memory config copy."""
    updated = config.model_copy(deep=True)
    request_names = {spec.name for spec in sampling_parameter_catalog()}
    for name, value in combination.parameters.items():
        if name in request_names:
            continue
        if name == "model.context_size":
            updated.model = updated.model.model_copy(update={"context_size": int(value)})
        elif name == "model.batch_size":
            updated.model = updated.model.model_copy(update={"batch_size": int(value)})
        elif name == "server.parallel":
            updated.server = updated.server.model_copy(update={"parallel": int(value)})
        elif name == "gpu.layers":
            updated.gpu = updated.gpu.model_copy(update={"layers": int(value)})
        elif name.startswith("--"):
            updated.args = _set_runtime_arg(updated.args, name, value)
    return updated


def format_cli_overrides(combination: GridCombination) -> str:
    """Return a deterministic CLI override preview for one combination."""
    parts: list[str] = []
    for name, value in combination.parameters.items():
        if not name.startswith("--"):
            continue
        if isinstance(value, bool):
            if value:
                parts.append(name)
            continue
        if value == "":
            continue
        parts.extend([name, str(value)])
    return " ".join(parts)


def format_grid_plan_preview(
    plan: GridPlan,
    *,
    instance_count: int = 1,
    max_runs: int = 3,
) -> str:
    """Format a concise, executable preview for the GUI dialog."""
    combinations = plan.combinations()
    total_runs = len(combinations) * max(instance_count, 1)
    restart_count = total_runs if plan_requires_restart(plan) else 0
    request_count = total_runs
    lines = [
        f"Combinations: {len(combinations)}",
        f"Model restarts: {restart_count}",
        f"Requests: {request_count}",
    ]
    for combination in combinations[:max_runs]:
        profile = combination.parameters.get(KV_CACHE_PROFILE_PARAMETER_NAME)
        prefix = f"Run {combination.index}"
        if profile:
            prefix = f"{prefix} {profile}"
        overrides = format_cli_overrides(combination) or "(request-only)"
        lines.append(f"{prefix}: {overrides}")
    if len(combinations) > max_runs:
        lines.append(f"... {len(combinations) - max_runs} more run(s)")
    return "\n".join(lines)


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
    if plan_requires_restart(plan):
        raise ValueError("Request-only grid cannot execute restart-required parameters.")
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


def run_grid_for_instance(
    config: InstanceConfig,
    *,
    base_settings: BenchmarkSettings,
    plan: GridPlan,
    should_stop: Callable[[], bool] | None = None,
    post_message: Callable[[str], None] | None = None,
    run_benchmark: Callable[[InstanceConfig, BenchmarkSettings], BenchmarkResult] = quick_benchmark_instance,
    restart_runtime: Callable[[InstanceConfig], None] | None = None,
    db_path: Path | None = None,
) -> GridSweepRecord:
    """Run a grid, restarting the runtime before restart-required combinations."""
    if plan_requires_restart(plan) and restart_runtime is None:
        raise ValueError("Restart-required grid needs a restart_runtime callback.")
    unsupported = unsupported_execution_parameters(plan)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Grid cannot execute unsupported parameters: {joined}")

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
    restart_needed = plan_requires_restart(plan)
    try:
        for combination in combinations:
            if should_stop and should_stop():
                stopped = True
                break
            run = record_grid_run_start(
                sweep_id=sweep.sweep_id,
                instance_name=config.name,
                combination=combination,
                db_path=db_path,
            )
            try:
                runtime_config = apply_runtime_combination(config, combination)
                if restart_needed and restart_runtime is not None:
                    if post_message:
                        post_message(
                            f"[Grid benchmark] {combination.index}/{len(combinations)} "
                            f"restarting: {config.name}"
                        )
                    restart_runtime(runtime_config)
                if post_message:
                    post_message(
                        f"[Grid benchmark] {combination.index}/{len(combinations)} "
                        f"running: {config.name}"
                    )
                result = run_benchmark(
                    runtime_config,
                    settings_for_combination(base_settings, combination),
                )
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


def _expand_composite_parameters(
    parameters: dict[str, int | float | str | bool],
) -> dict[str, int | float | str | bool]:
    expanded: dict[str, int | float | str | bool] = {}
    for name, value in parameters.items():
        if name != KV_CACHE_PARAMETER_NAME:
            expanded[name] = value
            continue
        profile = kv_cache_profile_from_id(str(value))
        expanded[KV_CACHE_PROFILE_PARAMETER_NAME] = profile.id
        expanded["--cache-type-k"] = profile.cache_type_k
        expanded["--cache-type-v"] = profile.cache_type_v
        if profile.cache_type_k_draft:
            expanded["--cache-type-k-draft"] = profile.cache_type_k_draft
        if profile.cache_type_v_draft:
            expanded["--cache-type-v-draft"] = profile.cache_type_v_draft
    return expanded


def _config_has_draft_runtime(config: InstanceConfig) -> bool:
    args = tuple(config.args)
    for index, item in enumerate(args):
        if item == "--model-draft":
            return True
        if item == "--spec-type" and index + 1 < len(args):
            return args[index + 1] not in {"", "none", "disabled", "off"}
    metadata = config.model_metadata
    if metadata is None:
        return False
    speculative = metadata.speculative_decoding
    return bool(speculative.builtin_mtp or speculative.external_draft or speculative.dflash)


def _kv_cache_matrix_profile_id(cache_type_k: str, cache_type_v: str) -> str:
    return f"k_{cache_type_k}__v_{cache_type_v}"


def _set_runtime_arg(
    args: list[str],
    flag: str,
    value: int | float | str | bool,
) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for index, item in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if item == flag:
            if index + 1 < len(args) and not args[index + 1].startswith("--"):
                skip_next = True
            continue
        cleaned.append(item)

    if isinstance(value, bool):
        if value:
            cleaned.append(flag)
        return cleaned
    if value == "":
        return cleaned
    cleaned.extend([flag, str(value)])
    return cleaned


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
