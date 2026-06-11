"""Quick benchmark support for llama-orchestrator.

The module is intentionally synchronous so it can be called from the Tkinter
GUI inside its existing background worker thread.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from llama_orchestrator.config import InstanceConfig, get_logs_dir, get_project_root, get_state_dir
from llama_orchestrator.engine.detection import describe_effective_runtime, get_backend_device_label
from llama_orchestrator.engine.process import get_log_files
from llama_orchestrator.engine.validator import validate_process

DEFAULT_PROMPT_TEXT = (
    "You are benchmarking a local llama.cpp server. Summarize the practical "
    "tradeoffs of GPU inference in exactly five concise bullet points."
)
DEFAULT_MAX_TOKENS = 200
DEFAULT_TEMPERATURE = 0.0
DEFAULT_ENDPOINT = "chat_completions"
BENCHMARK_ENDPOINTS = {"chat_completions", "completion"}


@dataclass(frozen=True)
class BenchmarkSettings:
    """User-editable quick benchmark settings."""

    prompt_file: Path
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    seed: int | None = None
    endpoint: str = DEFAULT_ENDPOINT
    ignore_eos: bool = False


@dataclass(frozen=True)
class BenchmarkResult:
    """One benchmark attempt, successful or failed."""

    instance_name: str
    timestamp: str
    config_hash: str
    prompt_file: str
    prompt_sha256: str
    prompt_chars: int
    output_tokens: int | None
    tokens_per_second: float | None
    latency_ms: float | None
    elapsed_ms: float | None
    vram_mb: float | None
    status: str
    dedicated_vram_mb: float | None = None
    shared_ram_mb: float | None = None
    total_gpu_memory_mb: float | None = None
    memory_source: str | None = None
    memory_scope: str | None = None
    memory_confidence: str | None = None
    sampled_pid: int | None = None
    sampled_device_label: str | None = None
    memory_sample_error: str | None = None
    error: str | None = None
    artifact_file: str | None = None
    prompt_tokens: int | None = None
    tokens_cached: int | None = None
    cache_hit_rate: float | None = None
    prompt_ms: float | None = None
    prompt_tokens_per_second: float | None = None
    time_per_output_token_ms: float | None = None
    end_to_end_tokens_per_second: float | None = None
    speculative_mode: str | None = None
    draft_tokens: int | None = None
    draft_tokens_accepted: int | None = None
    draft_acceptance_rate: float | None = None
    instance_uid: str | None = None
    instance_no: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class GpuMemorySample:
    """Best-effort GPU memory split for one benchmarked instance."""

    dedicated_vram_mb: float | None
    shared_ram_mb: float | None
    total_gpu_memory_mb: float | None
    memory_source: str = "unavailable"
    memory_scope: str = "unavailable"
    memory_confidence: str = "low"
    sampled_pid: int | None = None
    sampled_device_label: str | None = None
    sample_error: str | None = None


def get_validated_benchmark_pid(instance_name: str) -> int | None:
    """Return a validated llama-server PID for process-scoped memory sampling."""
    validation = validate_process(instance_name)
    if not validation.is_valid():
        return None
    return validation.actual_pid or validation.expected_pid


def _normalize_gpu_memory_sample(
    dedicated_vram_mb: float | None,
    shared_ram_mb: float | None,
    *,
    memory_source: str = "unknown",
    memory_scope: str = "unknown",
    memory_confidence: str = "low",
    sampled_pid: int | None = None,
    sampled_device_label: str | None = None,
    sample_error: str | None = None,
) -> GpuMemorySample:
    total_gpu_memory_mb: float | None = None
    if dedicated_vram_mb is not None:
        total_gpu_memory_mb = dedicated_vram_mb + (shared_ram_mb or 0.0)
    elif shared_ram_mb is not None:
        total_gpu_memory_mb = shared_ram_mb
    return GpuMemorySample(
        dedicated_vram_mb=dedicated_vram_mb,
        shared_ram_mb=shared_ram_mb,
        total_gpu_memory_mb=total_gpu_memory_mb,
        memory_source=memory_source,
        memory_scope=memory_scope,
        memory_confidence=memory_confidence,
        sampled_pid=sampled_pid,
        sampled_device_label=sampled_device_label,
        sample_error=sample_error,
    )


def get_default_prompt_file(project_root: Path | None = None) -> Path:
    """Return the default benchmark prompt file path and create it if missing."""
    root = project_root or get_project_root()
    prompt_file = root / "benchmarks" / "prompts" / "default.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    if not prompt_file.exists():
        prompt_file.write_text(DEFAULT_PROMPT_TEXT + "\n", encoding="utf-8")
    return prompt_file


def get_settings_path() -> Path:
    """Return the persisted GUI benchmark settings path."""
    return get_state_dir() / "benchmark_settings.json"


def _normalize_prompt_file(path: Path, project_root: Path) -> Path:
    if path.is_absolute():
        return path
    return project_root / path


def _coerce_int(
    value: Any,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_float(
    value: Any,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_optional_int(
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value in (None, ""):
        return None
    return _coerce_int(value, 0, minimum=minimum, maximum=maximum)


def _coerce_optional_float(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value in (None, ""):
        return None
    return _coerce_float(value, 0.0, minimum=minimum, maximum=maximum)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return default


def _coerce_endpoint(value: Any) -> str:
    endpoint = str(value or DEFAULT_ENDPOINT)
    if endpoint in BENCHMARK_ENDPOINTS:
        return endpoint
    return DEFAULT_ENDPOINT


def load_benchmark_settings(project_root: Path | None = None) -> BenchmarkSettings:
    """Load benchmark settings, falling back to a default prompt file."""
    root = project_root or get_project_root()
    settings_path = get_settings_path()
    default_prompt = get_default_prompt_file(root)

    if not settings_path.exists():
        settings = BenchmarkSettings(prompt_file=default_prompt)
        save_benchmark_settings(settings, root)
        return settings

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BenchmarkSettings(prompt_file=default_prompt)

    prompt_value = str(data.get("prompt_file") or default_prompt)
    return BenchmarkSettings(
        prompt_file=_normalize_prompt_file(Path(prompt_value), root),
        max_tokens=_coerce_int(data.get("max_tokens"), DEFAULT_MAX_TOKENS, minimum=1),
        temperature=_coerce_float(data.get("temperature"), DEFAULT_TEMPERATURE, minimum=0.0),
        top_p=_coerce_optional_float(data.get("top_p"), minimum=0.0, maximum=1.0),
        top_k=_coerce_optional_int(data.get("top_k"), minimum=0),
        repeat_penalty=_coerce_optional_float(data.get("repeat_penalty"), minimum=0.0),
        seed=_coerce_optional_int(data.get("seed"), minimum=-1),
        endpoint=_coerce_endpoint(data.get("endpoint")),
        ignore_eos=_coerce_bool(data.get("ignore_eos")),
    )


def save_benchmark_settings(
    settings: BenchmarkSettings,
    project_root: Path | None = None,
) -> Path:
    """Persist benchmark settings with prompt paths relative to project root."""
    root = project_root or get_project_root()
    settings_path = get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = settings.prompt_file
    try:
        prompt_value = str(prompt_file.resolve().relative_to(root.resolve()))
    except ValueError:
        prompt_value = str(prompt_file)

    settings_path.write_text(
        json.dumps(
            {
                "prompt_file": prompt_value,
                "max_tokens": settings.max_tokens,
                "temperature": settings.temperature,
                "top_p": settings.top_p,
                "top_k": settings.top_k,
                "repeat_penalty": settings.repeat_penalty,
                "seed": settings.seed,
                "endpoint": settings.endpoint,
                "ignore_eos": settings.ignore_eos,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return settings_path


def read_prompt(settings: BenchmarkSettings) -> tuple[str, str]:
    """Read the configured prompt and return text plus SHA256 digest."""
    prompt = settings.prompt_file.read_text(encoding="utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, digest


def config_hash(config: InstanceConfig) -> str:
    """Return a stable short hash for an instance configuration."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _display_project_path(path: Path, project_root: Path | None = None) -> str:
    root = project_root or get_project_root()
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def get_benchmark_db_path() -> Path:
    """Return the SQLite benchmark history path."""
    return get_state_dir() / "benchmark_history.sqlite"


def get_benchmark_runs_dir(instance_name: str) -> Path:
    """Return the per-run benchmark artifact directory."""
    return get_logs_dir() / instance_name / "benchmarks"


def init_benchmark_db(db_path: Path | None = None) -> Path:
    """Initialize benchmark history storage."""
    path = db_path or get_benchmark_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                instance_name TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                prompt_file TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                prompt_chars INTEGER NOT NULL,
                output_tokens INTEGER,
                tokens_per_second REAL,
                latency_ms REAL,
                elapsed_ms REAL,
                vram_mb REAL,
                dedicated_vram_mb REAL,
                shared_ram_mb REAL,
                total_gpu_memory_mb REAL,
                memory_source TEXT,
                memory_scope TEXT,
                memory_confidence TEXT,
                sampled_pid INTEGER,
                sampled_device_label TEXT,
                memory_sample_error TEXT,
                prompt_tokens INTEGER,
                tokens_cached INTEGER,
                cache_hit_rate REAL,
                prompt_ms REAL,
                prompt_tokens_per_second REAL,
                time_per_output_token_ms REAL,
                end_to_end_tokens_per_second REAL,
                speculative_mode TEXT,
                draft_tokens INTEGER,
                draft_tokens_accepted INTEGER,
                draft_acceptance_rate REAL,
                status TEXT NOT NULL,
                error TEXT,
                artifact_file TEXT
            )
            """
        )
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(benchmarks)").fetchall()
        }
        for column_name in (
            "instance_uid",
            "instance_no",
            "display_name",
            "dedicated_vram_mb",
            "shared_ram_mb",
            "total_gpu_memory_mb",
            "memory_source",
            "memory_scope",
            "memory_confidence",
            "sampled_pid",
            "sampled_device_label",
            "memory_sample_error",
            "prompt_tokens",
            "tokens_cached",
            "cache_hit_rate",
            "prompt_ms",
            "prompt_tokens_per_second",
            "time_per_output_token_ms",
            "end_to_end_tokens_per_second",
            "speculative_mode",
            "draft_tokens",
            "draft_tokens_accepted",
            "draft_acceptance_rate",
            "artifact_file",
        ):
            if column_name not in existing_columns:
                if column_name in {
                    "artifact_file",
                    "speculative_mode",
                    "memory_source",
                    "memory_scope",
                    "memory_confidence",
                    "sampled_device_label",
                    "memory_sample_error",
                    "instance_uid",
                    "instance_no",
                    "display_name",
                }:
                    column_type = "TEXT"
                elif column_name in {
                    "prompt_tokens",
                    "tokens_cached",
                    "draft_tokens",
                    "draft_tokens_accepted",
                    "sampled_pid",
                }:
                    column_type = "INTEGER"
                else:
                    column_type = "REAL"
                conn.execute(f"ALTER TABLE benchmarks ADD COLUMN {column_name} {column_type}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_benchmarks_instance_time "
            "ON benchmarks(instance_name, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_benchmarks_uid_time "
            "ON benchmarks(instance_uid, timestamp)"
        )
    return path


def _resolve_benchmark_identity(instance_name: str) -> tuple[str | None, str | None, str]:
    """Best-effort resolve persisted identity metadata for benchmark rows."""
    try:
        from llama_orchestrator.config import get_instance_config

        config = get_instance_config(instance_name)
        return config.instance_uid, config.instance_no, config.display_name or config.name
    except Exception:
        return None, None, instance_name


def record_benchmark_result(
    result: BenchmarkResult,
    db_path: Path | None = None,
) -> None:
    """Append a benchmark result to SQLite history."""
    path = init_benchmark_db(db_path)
    instance_uid, instance_no, display_name = (
        result.instance_uid,
        result.instance_no,
        result.display_name,
    )
    if instance_uid is None or display_name is None:
        resolved_uid, resolved_no, resolved_display_name = _resolve_benchmark_identity(result.instance_name)
        instance_uid = instance_uid or resolved_uid
        instance_no = instance_no or resolved_no
        display_name = display_name or resolved_display_name
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO benchmarks (
                timestamp, instance_name, instance_uid, instance_no, display_name, config_hash, prompt_file, prompt_sha256,
                prompt_chars, output_tokens, tokens_per_second, latency_ms,
                elapsed_ms, vram_mb, dedicated_vram_mb, shared_ram_mb,
                total_gpu_memory_mb, memory_source, memory_scope,
                memory_confidence, sampled_pid, sampled_device_label,
                memory_sample_error, prompt_tokens, tokens_cached,
                cache_hit_rate, prompt_ms, prompt_tokens_per_second,
                time_per_output_token_ms, end_to_end_tokens_per_second,
                speculative_mode, draft_tokens, draft_tokens_accepted,
                draft_acceptance_rate, status, error, artifact_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.timestamp,
                result.instance_name,
                instance_uid,
                instance_no,
                display_name,
                result.config_hash,
                result.prompt_file,
                result.prompt_sha256,
                result.prompt_chars,
                result.output_tokens,
                result.tokens_per_second,
                result.latency_ms,
                result.elapsed_ms,
                result.vram_mb,
                result.dedicated_vram_mb,
                result.shared_ram_mb,
                result.total_gpu_memory_mb,
                result.memory_source,
                result.memory_scope,
                result.memory_confidence,
                result.sampled_pid,
                result.sampled_device_label,
                result.memory_sample_error,
                result.prompt_tokens,
                result.tokens_cached,
                result.cache_hit_rate,
                result.prompt_ms,
                result.prompt_tokens_per_second,
                result.time_per_output_token_ms,
                result.end_to_end_tokens_per_second,
                result.speculative_mode,
                result.draft_tokens,
                result.draft_tokens_accepted,
                result.draft_acceptance_rate,
                result.status,
                result.error,
                result.artifact_file,
            ),
        )


def latest_benchmark_results(db_path: Path | None = None) -> dict[str, BenchmarkResult]:
    """Return the latest benchmark result per instance."""
    path = init_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT b.*
            FROM benchmarks b
            JOIN (
                SELECT instance_name, MAX(id) AS id
                FROM benchmarks
                GROUP BY instance_name
            ) latest ON b.instance_name = latest.instance_name AND b.id = latest.id
            """
        ).fetchall()

    results: dict[str, BenchmarkResult] = {}
    for row in rows:
        row_data = dict(row)
        dedicated_vram_mb = row_data.get("dedicated_vram_mb")
        if dedicated_vram_mb is None:
            dedicated_vram_mb = row_data.get("vram_mb")
        shared_ram_mb = row_data.get("shared_ram_mb")
        total_gpu_memory_mb = row_data.get("total_gpu_memory_mb")
        if total_gpu_memory_mb is None and dedicated_vram_mb is not None:
            total_gpu_memory_mb = dedicated_vram_mb + (shared_ram_mb or 0.0)

        results[row_data["instance_name"]] = BenchmarkResult(
            instance_name=row_data["instance_name"],
            instance_uid=row_data.get("instance_uid"),
            instance_no=row_data.get("instance_no"),
            display_name=row_data.get("display_name"),
            timestamp=row_data["timestamp"],
            config_hash=row_data["config_hash"],
            prompt_file=row_data["prompt_file"],
            prompt_sha256=row_data["prompt_sha256"],
            prompt_chars=row_data["prompt_chars"],
            output_tokens=row_data["output_tokens"],
            tokens_per_second=row_data["tokens_per_second"],
            latency_ms=row_data["latency_ms"],
            elapsed_ms=row_data["elapsed_ms"],
            vram_mb=row_data.get("vram_mb"),
            status=row_data["status"],
            dedicated_vram_mb=dedicated_vram_mb,
            shared_ram_mb=shared_ram_mb,
            total_gpu_memory_mb=total_gpu_memory_mb,
            memory_source=row_data.get("memory_source") or ("legacy_unknown" if row_data.get("vram_mb") is not None else None),
            memory_scope=row_data.get("memory_scope"),
            memory_confidence=row_data.get("memory_confidence"),
            sampled_pid=row_data.get("sampled_pid"),
            sampled_device_label=row_data.get("sampled_device_label"),
            memory_sample_error=row_data.get("memory_sample_error"),
            error=row_data["error"],
            artifact_file=row_data.get("artifact_file"),
            prompt_tokens=row_data.get("prompt_tokens"),
            tokens_cached=row_data.get("tokens_cached"),
            cache_hit_rate=row_data.get("cache_hit_rate"),
            prompt_ms=row_data.get("prompt_ms"),
            prompt_tokens_per_second=row_data.get("prompt_tokens_per_second"),
            time_per_output_token_ms=row_data.get("time_per_output_token_ms"),
            end_to_end_tokens_per_second=row_data.get("end_to_end_tokens_per_second"),
            speculative_mode=row_data.get("speculative_mode"),
            draft_tokens=row_data.get("draft_tokens"),
            draft_tokens_accepted=row_data.get("draft_tokens_accepted"),
            draft_acceptance_rate=row_data.get("draft_acceptance_rate"),
        )

    return results


def sync_benchmark_instance_identity(rows: list[dict[str, str]], db_path: Path | None = None) -> None:
    """Backfill additive instance identity columns for benchmark history rows."""
    if not rows:
        return
    path = init_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        for row in rows:
            conn.execute(
                """
                UPDATE benchmarks
                SET instance_uid = ?, instance_no = ?, display_name = ?
                WHERE instance_name = ?
                """,
                (row["instance_uid"], row["instance_no"], row["display_name"], row["name"]),
            )
        conn.commit()


def _sample_windows_gpu_process_memory(pid: int) -> GpuMemorySample | None:
    script = "\n".join(
        [
            f"$PidValue = {pid}",
            "$samples = Get-Counter -Counter '\\GPU Process Memory(*)\\Dedicated Usage','\\GPU Process Memory(*)\\Shared Usage' -ErrorAction Stop",
            "$dedicated = 0.0",
            "$shared = 0.0",
            "$matched = $false",
            "foreach ($sample in $samples.CounterSamples) {",
            "    if ($sample.Path -notlike \"*pid_$PidValue_*\") { continue }",
            "    $matched = $true",
            "    $valueMb = [math]::Round(($sample.CookedValue / 1MB), 3)",
            "    if ($sample.Path -like '*\\Dedicated Usage') { $dedicated += $valueMb }",
            "    elseif ($sample.Path -like '*\\Shared Usage') { $shared += $valueMb }",
            "}",
            "if ($matched) {",
            "    [pscustomobject]@{",
            "        dedicated_vram_mb = [math]::Round($dedicated, 3)",
            "        shared_ram_mb = [math]::Round($shared, 3)",
            "    } | ConvertTo-Json -Compress",
            "}",
        ]
    )

    for shell in ("pwsh", "powershell"):
        try:
            proc = subprocess.run(
                [shell, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        dedicated_vram_mb = payload.get("dedicated_vram_mb")
        shared_ram_mb = payload.get("shared_ram_mb")
        if not isinstance(dedicated_vram_mb, (int, float)) or not isinstance(shared_ram_mb, (int, float)):
            continue
        return _normalize_gpu_memory_sample(
            float(dedicated_vram_mb),
            float(shared_ram_mb),
            memory_source="windows_process_counter",
            memory_scope="process",
            memory_confidence="high",
            sampled_pid=pid,
        )

    return None


def _parse_vram_from_text(text: str) -> float | None:
    """Parse explicit memory-used text without accepting arbitrary numbers."""
    patterns = (
        r"(?:memory\s+used|used\s+memory|vram\s+used|gpu\s+memory\s+used)\D+(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)?",
        r"^\s*(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)?\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _normalize_vram_value(float(match.group(1)), match.group(2))
    return None


def parse_nvidia_smi_memory_used(text: str) -> float | None:
    """Parse `nvidia-smi --query-gpu=memory.used` output."""
    return _parse_vram_from_text(text)


def parse_amd_smi_memory_used(text: str, device_id: int) -> float | None:
    """Parse common `amd-smi metric -m --json` memory-used payloads."""
    return _parse_vendor_json_memory_used(text, device_id)


def parse_rocm_smi_memory_used(text: str, device_id: int) -> float | None:
    """Parse common `rocm-smi --showmemuse --json` memory-used payloads."""
    return _parse_vendor_json_memory_used(text, device_id)


def _parse_vendor_json_memory_used(text: str, device_id: int) -> float | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    candidates = _select_vendor_device_payloads(payload, device_id)
    for candidate in candidates:
        value = _find_memory_used_value(candidate)
        if value is not None:
            return value
    return _find_memory_used_value(payload)


def _select_vendor_device_payloads(payload: Any, device_id: int) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in (str(device_id), f"gpu{device_id}", f"GPU{device_id}", f"card{device_id}"):
            if key in payload:
                candidates.append(payload[key])
        for value in payload.values():
            if _payload_matches_device_id(value, device_id):
                candidates.append(value)
            candidates.extend(_select_vendor_device_payloads(value, device_id))
    elif isinstance(payload, list):
        for value in payload:
            if _payload_matches_device_id(value, device_id):
                candidates.append(value)
            candidates.extend(_select_vendor_device_payloads(value, device_id))
    return candidates


def _payload_matches_device_id(payload: Any, device_id: int) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("gpu", "gpu_id", "device_id", "card", "card_id", "id"):
        value = payload.get(key)
        if isinstance(value, int) and value == device_id:
            return True
        if isinstance(value, str) and value.strip().lower() in {str(device_id), f"gpu{device_id}", f"card{device_id}"}:
            return True
    return False


def _find_memory_used_value(payload: Any) -> float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key).lower()
            if ("memory" in key_text or "vram" in key_text) and any(
                marker in key_text for marker in ("used", "use", "busy")
            ):
                parsed = _coerce_memory_value(value, key_text)
                if parsed is not None:
                    return parsed
        for value in payload.values():
            parsed = _find_memory_used_value(value)
            if parsed is not None:
                return parsed
    elif isinstance(payload, list):
        for value in payload:
            parsed = _find_memory_used_value(value)
            if parsed is not None:
                return parsed
    return None


def _coerce_memory_value(value: Any, key_text: str = "") -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if "(b)" in key_text or "bytes" in key_text:
            return float(value) / (1024 * 1024)
        return float(value)
    if isinstance(value, str):
        return _parse_vram_from_text(value)
    return None


def _normalize_vram_value(value: float, unit: str | None) -> float:
    if unit and unit.lower() in {"gib", "gb"}:
        return value * 1024
    return value


def sample_vram_mb_from_log(
    stderr_log: Path,
    *,
    backend: str,
    device_id: int,
) -> float | None:
    """Best-effort VRAM estimate from llama.cpp stderr when vendor CLIs are unavailable."""
    sample = sample_gpu_memory_from_log(stderr_log, backend=backend, device_id=device_id)
    return sample.dedicated_vram_mb


def sample_gpu_memory_from_log(
    stderr_log: Path,
    *,
    backend: str,
    device_id: int,
) -> GpuMemorySample:
    """Return source-labeled GPU memory estimates from llama.cpp stderr."""
    if not stderr_log.exists():
        return _normalize_gpu_memory_sample(
            None,
            None,
            memory_source="unavailable",
            memory_scope="unavailable",
            memory_confidence="low",
            sample_error="stderr_log_missing",
        )

    device_label = get_backend_device_label(backend, device_id)
    if device_label is None:
        return _normalize_gpu_memory_sample(
            None,
            None,
            memory_source="unavailable",
            memory_scope="unavailable",
            memory_confidence="low",
            sample_error="device_label_unavailable",
        )

    try:
        lines = stderr_log.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return _normalize_gpu_memory_sample(
            None,
            None,
            memory_source="unavailable",
            memory_scope="unavailable",
            memory_confidence="low",
            sampled_device_label=device_label,
            sample_error="stderr_log_unreadable",
        )

    buffer_pattern = re.compile(
        rf"{re.escape(device_label)}\s+model buffer size\s*=\s*(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)",
        re.IGNORECASE,
    )
    current_free_pattern = re.compile(
        rf"{re.escape(device_label)}.*-\s*(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)\s+free",
        re.IGNORECASE,
    )
    inventory_pattern = re.compile(
        rf"{re.escape(device_label)}\s*:.*\((\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB),\s*(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)\s+free\)",
        re.IGNORECASE,
    )

    for line in reversed(lines):
        match = buffer_pattern.search(line)
        if match:
            return _normalize_gpu_memory_sample(
                _normalize_vram_value(float(match.group(1)), match.group(2)),
                None,
                memory_source="log_model_buffer",
                memory_scope="log_estimate",
                memory_confidence="low",
                sampled_device_label=device_label,
            )

    latest_free_mb: float | None = None
    total_mb: float | None = None
    for line in reversed(lines):
        if latest_free_mb is None:
            match = current_free_pattern.search(line)
            if match:
                latest_free_mb = _normalize_vram_value(float(match.group(1)), match.group(2))
        if total_mb is None:
            match = inventory_pattern.search(line)
            if match:
                total_mb = _normalize_vram_value(float(match.group(1)), match.group(2))
        if latest_free_mb is not None and total_mb is not None:
            return _normalize_gpu_memory_sample(
                max(0.0, total_mb - latest_free_mb),
                None,
                memory_source="log_device_free_delta",
                memory_scope="log_estimate",
                memory_confidence="low",
                sampled_device_label=device_label,
            )

    return _normalize_gpu_memory_sample(
        None,
        None,
        memory_source="unavailable",
        memory_scope="unavailable",
        memory_confidence="low",
        sampled_device_label=device_label,
        sample_error="no_log_memory_match",
    )


def sample_vram_mb(
    device_id: int = 0,
    *,
    backend: str | None = None,
    stderr_log: Path | None = None,
) -> float | None:
    """Best-effort current GPU memory sampling for common Windows GPU tools."""
    return sample_gpu_memory_from_vendor_or_log(
        device_id,
        backend=backend,
        stderr_log=stderr_log,
    ).dedicated_vram_mb


def sample_gpu_memory_from_vendor_or_log(
    device_id: int = 0,
    *,
    backend: str | None = None,
    stderr_log: Path | None = None,
) -> GpuMemorySample:
    """Best-effort dedicated memory fallback with explicit provenance."""
    commands = [
        (
            [
                "nvidia-smi",
                f"--id={device_id}",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            lambda text: parse_nvidia_smi_memory_used(text),
        ),
        (["amd-smi", "metric", "-g", str(device_id), "-m", "--json"], lambda text: parse_amd_smi_memory_used(text, device_id)),
        (["rocm-smi", "--showmemuse", "--json"], lambda text: parse_rocm_smi_memory_used(text, device_id)),
    ]

    errors: list[str] = []
    device_label = get_backend_device_label(backend, device_id) if backend else None
    for command, parser in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            errors.append(f"{command[0]} unavailable")
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            errors.append(f"{command[0]} returned no memory data")
            continue
        parsed = parser(proc.stdout)
        if parsed is not None:
            return _normalize_gpu_memory_sample(
                parsed,
                None,
                memory_source="vendor_device_cli",
                memory_scope="device",
                memory_confidence="low",
                sampled_device_label=device_label,
            )
        errors.append(f"{command[0]} output did not match memory-used schema")

    if backend and stderr_log is not None:
        sample = sample_gpu_memory_from_log(stderr_log, backend=backend, device_id=device_id)
        if sample.sample_error is None and errors:
            return _normalize_gpu_memory_sample(
                sample.dedicated_vram_mb,
                sample.shared_ram_mb,
                memory_source=sample.memory_source,
                memory_scope=sample.memory_scope,
                memory_confidence=sample.memory_confidence,
                sampled_device_label=sample.sampled_device_label,
                sample_error="; ".join(errors),
            )
        return sample

    return _normalize_gpu_memory_sample(
        None,
        None,
        memory_source="unavailable",
        memory_scope="unavailable",
        memory_confidence="low",
        sampled_device_label=device_label,
        sample_error="; ".join(errors) if errors else "no_memory_source_available",
    )


def sample_gpu_memory(
    *,
    pid: int | None,
    device_id: int = 0,
    backend: str | None = None,
    stderr_log: Path | None = None,
) -> GpuMemorySample:
    """Best-effort GPU memory split, preferring process-scoped Windows counters."""
    if pid is not None:
        windows_sample = _sample_windows_gpu_process_memory(pid)
        if windows_sample is not None:
            return windows_sample

    fallback_sample = sample_gpu_memory_from_vendor_or_log(
        device_id,
        backend=backend,
        stderr_log=stderr_log,
    )
    if pid is not None and fallback_sample.sample_error:
        sample_error = f"process_counter_unavailable; {fallback_sample.sample_error}"
    elif pid is not None:
        sample_error = "process_counter_unavailable"
    else:
        sample_error = fallback_sample.sample_error
    return _normalize_gpu_memory_sample(
        fallback_sample.dedicated_vram_mb,
        fallback_sample.shared_ram_mb,
        memory_source=fallback_sample.memory_source,
        memory_scope=fallback_sample.memory_scope,
        memory_confidence=fallback_sample.memory_confidence,
        sampled_device_label=fallback_sample.sampled_device_label,
        sample_error=sample_error,
    )


def _resolve_benchmark_sampling_device_id(config: InstanceConfig) -> int:
    """Return the primary effective device id for benchmark-side memory sampling."""
    selection = describe_effective_runtime(config)
    if selection.primary_device_id is not None:
        return selection.primary_device_id
    return int(config.gpu.device_id)


def _extract_token_count(payload: dict[str, Any], fallback_text: str) -> int | None:
    timings = payload.get("timings")
    if isinstance(timings, dict):
        for key in ("predicted_n", "predicted_tokens", "tokens_predicted"):
            value = timings.get(key)
            if isinstance(value, int) and value > 0:
                return value

    for key in ("tokens_predicted", "predicted_n", "completion_tokens"):
        value = payload.get(key)
        if isinstance(value, int) and value > 0:
            return value

    usage = payload.get("usage")
    if isinstance(usage, dict):
        value = usage.get("completion_tokens")
        if isinstance(value, int) and value > 0:
            return value

    return None


def _extract_nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_int_metric(payload: dict[str, Any], *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        value = _extract_nested_value(payload, path)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return None


def _extract_float_metric(payload: dict[str, Any], *paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value = _extract_nested_value(payload, path)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _extract_string_metric(payload: dict[str, Any], *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        value = _extract_nested_value(payload, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_runtime_arg_value(args: list[str], flag: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == flag:
            if index + 1 < len(args):
                return args[index + 1]
            return None
        prefix = f"{flag}="
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def _normalize_speculative_mode(value: str | None) -> str | None:
    if not value:
        return None
    modes: list[str] = []
    for part in value.split(","):
        normalized = part.strip()
        if not normalized or normalized.lower() == "none":
            continue
        if normalized not in modes:
            modes.append(normalized)
    if not modes:
        return None
    return ", ".join(modes)


def _extract_benchmark_telemetry(
    payload: dict[str, Any],
    *,
    args: list[str],
    output_tokens: int | None,
    elapsed_ms: float | None,
    derived_tokens_per_second: float | None,
) -> dict[str, float | int | str | None]:
    prompt_tokens = _extract_int_metric(
        payload,
        ("timings", "prompt_n"),
        ("usage", "prompt_tokens"),
        ("prompt_tokens",),
    )
    tokens_cached = _extract_int_metric(
        payload,
        ("tokens_cached",),
        ("usage", "prompt_tokens_cached"),
        ("timings", "tokens_cached"),
        ("timings", "cache_tokens"),
        ("timings", "cache_n"),
    )
    cache_hit_rate: float | None = None
    if (
        prompt_tokens is not None
        and prompt_tokens > 0
        and tokens_cached is not None
        and 0 <= tokens_cached <= prompt_tokens
    ):
        cache_hit_rate = (tokens_cached / prompt_tokens) * 100.0

    prompt_ms = _extract_float_metric(payload, ("timings", "prompt_ms"))
    prompt_tokens_per_second = _extract_float_metric(payload, ("timings", "prompt_per_second"))
    time_per_output_token_ms = _extract_float_metric(payload, ("timings", "predicted_per_token_ms"))
    if time_per_output_token_ms is None:
        predicted_ms = _extract_float_metric(payload, ("timings", "predicted_ms"))
        if predicted_ms is not None and output_tokens and output_tokens > 0:
            time_per_output_token_ms = predicted_ms / output_tokens

    tokens_per_second = _extract_float_metric(
        payload,
        ("timings", "predicted_per_second"),
        ("timings", "tokens_per_second"),
    )
    if tokens_per_second is None:
        tokens_per_second = derived_tokens_per_second

    end_to_end_tokens_per_second: float | None = None
    if output_tokens and output_tokens > 0 and elapsed_ms and elapsed_ms > 0:
        end_to_end_tokens_per_second = output_tokens / max(elapsed_ms / 1000.0, 0.001)

    speculative_mode = _normalize_speculative_mode(
        _extract_string_metric(
            payload,
            ("__verbose", "speculative.types"),
            ("speculative.types",),
            ("speculative_types",),
        )
        or _extract_runtime_arg_value(args, "--spec-type")
    )

    draft_tokens = _extract_int_metric(payload, ("timings", "draft_n"), ("draft_n",))
    draft_tokens_accepted = _extract_int_metric(
        payload,
        ("timings", "draft_n_accepted"),
        ("draft_n_accepted",),
    )
    draft_acceptance_rate: float | None = None
    if draft_tokens is not None and draft_tokens >= 0 and draft_tokens_accepted is not None:
        draft_tokens_accepted = max(0, min(draft_tokens_accepted, draft_tokens))
        if draft_tokens > 0:
            draft_acceptance_rate = (draft_tokens_accepted / draft_tokens) * 100.0

    return {
        "prompt_tokens": prompt_tokens,
        "tokens_cached": tokens_cached,
        "cache_hit_rate": cache_hit_rate,
        "prompt_ms": prompt_ms,
        "prompt_tokens_per_second": prompt_tokens_per_second,
        "time_per_output_token_ms": time_per_output_token_ms,
        "end_to_end_tokens_per_second": end_to_end_tokens_per_second,
        "speculative_mode": speculative_mode,
        "draft_tokens": draft_tokens,
        "draft_tokens_accepted": draft_tokens_accepted,
        "draft_acceptance_rate": draft_acceptance_rate,
        "tokens_per_second": tokens_per_second,
    }


def _collect_speculative_runtime_config(args: list[str]) -> dict[str, int | str | None]:
    return {
        "speculative_mode": _normalize_speculative_mode(_extract_runtime_arg_value(args, "--spec-type")),
        "draft_model": _extract_runtime_arg_value(args, "--mtp-head")
        or _extract_runtime_arg_value(args, "--draft-model")
        or _extract_runtime_arg_value(args, "--model-draft"),
        "draft_max_tokens": _coerce_optional_int(
            _extract_runtime_arg_value(args, "--spec-draft-n-max")
            or _extract_runtime_arg_value(args, "--draft-max")
        ),
        "draft_min_tokens": _coerce_optional_int(
            _extract_runtime_arg_value(args, "--spec-draft-n-min")
            or _extract_runtime_arg_value(args, "--draft-min")
        ),
        "draft_block_size": _coerce_optional_int(_extract_runtime_arg_value(args, "--draft-block-size")),
    }


def get_benchmark_endpoint_path(settings: BenchmarkSettings) -> str:
    """Return the llama.cpp HTTP endpoint for the configured benchmark mode."""
    if settings.endpoint == "completion":
        return "/completion"
    return "/v1/chat/completions"


def build_benchmark_request_body(settings: BenchmarkSettings, prompt: str) -> dict[str, Any]:
    """Build the llama.cpp benchmark request body."""
    if settings.endpoint == "completion":
        request_body: dict[str, Any] = {
            "prompt": prompt,
            "n_predict": settings.max_tokens,
            "temperature": settings.temperature,
            "stream": True,
            "cache_prompt": False,
        }
    else:
        request_body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": settings.max_tokens,
            "temperature": settings.temperature,
            "stream": True,
        }
    optional_values = {
        "top_p": settings.top_p,
        "top_k": settings.top_k,
        "repeat_penalty": settings.repeat_penalty,
        "seed": settings.seed,
    }
    request_body.update(
        {key: value for key, value in optional_values.items() if value is not None}
    )
    if settings.ignore_eos:
        request_body["ignore_eos"] = True
    return request_body


def _extract_stream_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return ""

    parts: list[str] = []
    reasoning_content = delta.get("reasoning_content")
    if isinstance(reasoning_content, str):
        parts.append(reasoning_content)
    chat_content = delta.get("content")
    if isinstance(chat_content, str):
        parts.append(chat_content)
    return "".join(parts)


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return safe or "benchmark"


def _benchmark_artifact_path(result: BenchmarkResult) -> Path:
    instance_dir = get_benchmark_runs_dir(result.instance_name)
    filename = (
        f"{_safe_filename_part(result.timestamp)}-"
        f"{_safe_filename_part(result.config_hash)}-{result.status}.md"
    )
    return instance_dir / filename


def _display_artifact_path(path: Path) -> str:
    return _display_project_path(path)


def _format_optional_metric(value: float | int | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _markdown_table_lines(rows: list[tuple[str, str]]) -> list[str]:
    return [
        "| Metric | Value |",
        "|--------|-------|",
        *[f"| {label} | {value} |" for label, value in rows],
    ]


def _markdown_fence(text: str) -> str:
    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest_run + 1)


def _settings_summary(settings: BenchmarkSettings) -> dict[str, int | float | str | bool | None]:
    return {
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "top_k": settings.top_k,
        "repeat_penalty": settings.repeat_penalty,
        "seed": settings.seed,
        "endpoint": settings.endpoint,
        "ignore_eos": settings.ignore_eos,
    }


def write_benchmark_artifact(
    result: BenchmarkResult,
    *,
    config: InstanceConfig,
    settings: BenchmarkSettings,
    prompt_text: str,
    output_text: str,
    request_body: dict[str, Any],
    final_payload: dict[str, Any],
) -> Path:
    """Write a Markdown artifact with prompt, model output, and benchmark stats."""
    artifact_path = _benchmark_artifact_path(result)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    speculative_config = _collect_speculative_runtime_config(config.args)
    speculative_mode = result.speculative_mode or speculative_config["speculative_mode"]
    draft_tokens_rejected: int | None = None
    draft_accepted_vs_output: float | None = None
    if result.draft_tokens is not None and result.draft_tokens_accepted is not None:
        draft_tokens_rejected = max(0, result.draft_tokens - result.draft_tokens_accepted)
    if result.output_tokens and result.output_tokens > 0 and result.draft_tokens_accepted is not None:
        draft_accepted_vs_output = (result.draft_tokens_accepted / result.output_tokens) * 100.0

    request_without_prompt = {
        key: value
        for key, value in request_body.items()
        if key not in {"prompt", "messages"}
    }
    prompt_fence = _markdown_fence(prompt_text)
    output_fence = _markdown_fence(output_text)
    payload_text = json.dumps(final_payload, indent=2, ensure_ascii=False) if final_payload else "{}"
    payload_fence = _markdown_fence(payload_text)
    settings_text = json.dumps(_settings_summary(settings), indent=2, ensure_ascii=False)
    request_text = json.dumps(request_without_prompt, indent=2, ensure_ascii=False)
    core_metric_rows = [
        ("Output tokens", _format_optional_metric(result.output_tokens)),
        ("Generation TPS", _format_optional_metric(result.tokens_per_second)),
        ("End-to-end TPS", _format_optional_metric(result.end_to_end_tokens_per_second)),
        ("TTFT (first token) ms", _format_optional_metric(result.latency_ms)),
        ("Time per output token ms", _format_optional_metric(result.time_per_output_token_ms)),
        ("Elapsed ms", _format_optional_metric(result.elapsed_ms)),
        ("Prompt tokens", _format_optional_metric(result.prompt_tokens)),
        ("Prompt eval ms", _format_optional_metric(result.prompt_ms)),
        ("Prompt eval tokens/sec", _format_optional_metric(result.prompt_tokens_per_second)),
        ("Cached prompt tokens", _format_optional_metric(result.tokens_cached)),
        ("Cache hit rate", _format_optional_percent(result.cache_hit_rate)),
        ("Error", result.error or "-"),
    ]
    memory_metric_rows = [
        ("Dedicated VRAM MB", _format_optional_metric(result.dedicated_vram_mb)),
        ("Shared RAM MB", _format_optional_metric(result.shared_ram_mb)),
        ("Total sampled GPU memory MB", _format_optional_metric(result.total_gpu_memory_mb)),
        ("Memory source", _format_optional_metric(result.memory_source)),
        ("Memory scope", _format_optional_metric(result.memory_scope)),
        ("Memory confidence", _format_optional_metric(result.memory_confidence)),
        ("Sampled PID", _format_optional_metric(result.sampled_pid)),
        ("Sampled device", _format_optional_metric(result.sampled_device_label)),
        ("Memory sample note", _format_optional_metric(result.memory_sample_error)),
    ]
    speculative_metric_rows = [
        ("Speculative mode", _format_optional_metric(speculative_mode)),
        ("Draft model / MTP head", _format_optional_metric(speculative_config["draft_model"])),
        ("Draft max tokens", _format_optional_metric(speculative_config["draft_max_tokens"])),
        ("Draft min tokens", _format_optional_metric(speculative_config["draft_min_tokens"])),
        ("Draft block size", _format_optional_metric(speculative_config["draft_block_size"])),
        ("Draft tokens attempted", _format_optional_metric(result.draft_tokens)),
        ("Draft tokens accepted", _format_optional_metric(result.draft_tokens_accepted)),
        ("Draft tokens rejected", _format_optional_metric(draft_tokens_rejected)),
        ("Draft acceptance rate", _format_optional_percent(result.draft_acceptance_rate)),
        ("Accepted draft vs output tokens", _format_optional_percent(draft_accepted_vs_output)),
    ]
    has_speculative_metrics = any(value != "-" for _, value in speculative_metric_rows)

    artifact_path.write_text(
        "\n".join(
            [
                f"# Quick Benchmark - {result.instance_name}",
                "",
                "## Summary",
                "",
                f"- Timestamp: `{result.timestamp}`",
                f"- Status: `{result.status}`",
                f"- Config hash: `{result.config_hash}`",
                f"- Prompt file: `{result.prompt_file}`",
                f"- Prompt SHA256: `{result.prompt_sha256 or '-'}`",
                f"- Model path: `{config.model.path}`",
                f"- Runtime args: `{shlex.join(config.args) if config.args else '-'}`",
                f"- Backend: `{config.gpu.backend}`",
                f"- Device ID: `{config.gpu.device_id}`",
                f"- Server: `{config.server.host}:{config.server.port}`",
                "",
                "## Core Metrics",
                "",
                *_markdown_table_lines(core_metric_rows),
                "",
                "## Memory Metrics",
                "",
                *_markdown_table_lines(memory_metric_rows),
                "",
                "## Speculative / Draft Metrics",
                "",
                *(
                    _markdown_table_lines(speculative_metric_rows)
                    if has_speculative_metrics
                    else ["No speculative or draft runtime telemetry detected."]
                ),
                "",
                "## Benchmark Settings",
                "",
                "```json",
                settings_text,
                "```",
                "",
                "## Request Parameters",
                "",
                "```json",
                request_text,
                "```",
                "",
                "## Prompt",
                "",
                prompt_fence,
                prompt_text,
                prompt_fence,
                "",
                "## Model Output",
                "",
                output_fence,
                output_text,
                output_fence,
                "",
                "## Final Stream Payload",
                "",
                payload_fence + "json",
                payload_text,
                payload_fence,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return artifact_path


def quick_benchmark_instance(
    config: InstanceConfig,
    settings: BenchmarkSettings | None = None,
) -> BenchmarkResult:
    """Run a standard prompt against a live llama.cpp server and persist metrics."""
    active_settings = settings or load_benchmark_settings()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cfg_hash = config_hash(config)
    prompt_file = _display_project_path(active_settings.prompt_file)
    stderr_log = get_log_files(config.name)[1]
    pid = get_validated_benchmark_pid(config.name)
    prompt = ""
    prompt_digest = ""
    output_text = ""
    chunks: list[str] = []
    final_payload: dict[str, Any] = {}
    request_body: dict[str, Any] = {}

    try:
        prompt, prompt_digest = read_prompt(active_settings)
        started = time.perf_counter()
        first_token_at: float | None = None
        endpoint_path = get_benchmark_endpoint_path(active_settings)
        url = f"http://{config.server.host}:{config.server.port}{endpoint_path}"
        request_body = build_benchmark_request_body(active_settings, prompt)

        with httpx.Client(timeout=config.server.timeout or None) as client, client.stream(
            "POST",
            url,
            json=request_body,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                final_payload = payload
                content = _extract_stream_content(payload)
                if content:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    chunks.append(content)

        ended = time.perf_counter()
        output_text = "".join(chunks)
        output_tokens = _extract_token_count(final_payload, output_text)
        elapsed_ms = (ended - started) * 1000
        latency_ms = ((first_token_at or ended) - started) * 1000
        generation_seconds = max(0.001, (ended - (first_token_at or started)))
        derived_tokens_per_second: float | None = None
        if output_tokens and output_tokens > 0:
            derived_tokens_per_second = output_tokens / generation_seconds
        telemetry = _extract_benchmark_telemetry(
            final_payload,
            args=config.args,
            output_tokens=output_tokens,
            elapsed_ms=elapsed_ms,
            derived_tokens_per_second=derived_tokens_per_second,
        )
        memory_sample = sample_gpu_memory(
            pid=pid,
            device_id=_resolve_benchmark_sampling_device_id(config),
            backend=config.gpu.backend,
            stderr_log=stderr_log,
        )

        result = BenchmarkResult(
            instance_name=config.name,
            timestamp=timestamp,
            config_hash=cfg_hash,
            prompt_file=prompt_file,
            prompt_sha256=prompt_digest,
            prompt_chars=len(prompt),
            output_tokens=output_tokens,
            tokens_per_second=telemetry["tokens_per_second"],
            latency_ms=latency_ms,
            elapsed_ms=elapsed_ms,
            vram_mb=memory_sample.dedicated_vram_mb,
            status="ok",
            dedicated_vram_mb=memory_sample.dedicated_vram_mb,
            shared_ram_mb=memory_sample.shared_ram_mb,
            total_gpu_memory_mb=memory_sample.total_gpu_memory_mb,
            memory_source=memory_sample.memory_source,
            memory_scope=memory_sample.memory_scope,
            memory_confidence=memory_sample.memory_confidence,
            sampled_pid=memory_sample.sampled_pid,
            sampled_device_label=memory_sample.sampled_device_label,
            memory_sample_error=memory_sample.sample_error,
            prompt_tokens=telemetry["prompt_tokens"],
            tokens_cached=telemetry["tokens_cached"],
            cache_hit_rate=telemetry["cache_hit_rate"],
            prompt_ms=telemetry["prompt_ms"],
            prompt_tokens_per_second=telemetry["prompt_tokens_per_second"],
            time_per_output_token_ms=telemetry["time_per_output_token_ms"],
            end_to_end_tokens_per_second=telemetry["end_to_end_tokens_per_second"],
            speculative_mode=telemetry["speculative_mode"],
            draft_tokens=telemetry["draft_tokens"],
            draft_tokens_accepted=telemetry["draft_tokens_accepted"],
            draft_acceptance_rate=telemetry["draft_acceptance_rate"],
        )
    except Exception as exc:
        output_text = "".join(chunks)
        with suppress(Exception):
            prompt, prompt_digest = read_prompt(active_settings)
        telemetry = _extract_benchmark_telemetry(
            final_payload,
            args=config.args,
            output_tokens=None,
            elapsed_ms=None,
            derived_tokens_per_second=None,
        )
        result = BenchmarkResult(
            instance_name=config.name,
            timestamp=timestamp,
            config_hash=cfg_hash,
            prompt_file=prompt_file,
            prompt_sha256=prompt_digest,
            prompt_chars=len(prompt),
            output_tokens=None,
            tokens_per_second=None,
            latency_ms=None,
            elapsed_ms=None,
            vram_mb=None,
            status="failed",
            dedicated_vram_mb=None,
            shared_ram_mb=None,
            total_gpu_memory_mb=None,
            error=str(exc),
            prompt_tokens=telemetry["prompt_tokens"],
            tokens_cached=telemetry["tokens_cached"],
            cache_hit_rate=telemetry["cache_hit_rate"],
            prompt_ms=telemetry["prompt_ms"],
            prompt_tokens_per_second=telemetry["prompt_tokens_per_second"],
            time_per_output_token_ms=telemetry["time_per_output_token_ms"],
            end_to_end_tokens_per_second=telemetry["end_to_end_tokens_per_second"],
            speculative_mode=telemetry["speculative_mode"],
            draft_tokens=telemetry["draft_tokens"],
            draft_tokens_accepted=telemetry["draft_tokens_accepted"],
            draft_acceptance_rate=telemetry["draft_acceptance_rate"],
        )
        memory_sample = sample_gpu_memory(
            pid=pid,
            device_id=_resolve_benchmark_sampling_device_id(config),
            backend=config.gpu.backend,
            stderr_log=stderr_log,
        )
        result = BenchmarkResult(
            **{
                **result.__dict__,
                "vram_mb": memory_sample.dedicated_vram_mb,
                "dedicated_vram_mb": memory_sample.dedicated_vram_mb,
                "shared_ram_mb": memory_sample.shared_ram_mb,
                "total_gpu_memory_mb": memory_sample.total_gpu_memory_mb,
                "memory_source": memory_sample.memory_source,
                "memory_scope": memory_sample.memory_scope,
                "memory_confidence": memory_sample.memory_confidence,
                "sampled_pid": memory_sample.sampled_pid,
                "sampled_device_label": memory_sample.sampled_device_label,
                "memory_sample_error": memory_sample.sample_error,
            }
        )

    artifact_path = write_benchmark_artifact(
        result,
        config=config,
        settings=active_settings,
        prompt_text=prompt,
        output_text=output_text,
        request_body=request_body,
        final_payload=final_payload,
    )
    result = replace(result, artifact_file=_display_artifact_path(artifact_path))
    record_benchmark_result(result)
    return result
