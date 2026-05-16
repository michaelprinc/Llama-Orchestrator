"""Quick benchmark support for llama-orchestrator.

The module is intentionally synchronous so it can be called from the Tkinter
GUI inside its existing background worker thread.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from llama_orchestrator.config import InstanceConfig, get_project_root, get_state_dir
from llama_orchestrator.engine.process import get_log_files

DEFAULT_PROMPT_TEXT = (
    "You are benchmarking a local llama.cpp server. Summarize the practical "
    "tradeoffs of GPU inference in exactly five concise bullet points."
)
DEFAULT_MAX_TOKENS = 200


@dataclass(frozen=True)
class BenchmarkSettings:
    """User-editable quick benchmark settings."""

    prompt_file: Path
    max_tokens: int = DEFAULT_MAX_TOKENS


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
    error: str | None = None


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
    max_tokens = int(data.get("max_tokens") or DEFAULT_MAX_TOKENS)
    return BenchmarkSettings(
        prompt_file=_normalize_prompt_file(Path(prompt_value), root),
        max_tokens=max(1, max_tokens),
    )


def save_benchmark_settings(
    settings: BenchmarkSettings,
    project_root: Path | None = None,
) -> Path:
    """Persist benchmark settings with prompt paths relative to project root."""
    root = project_root or get_project_root()
    settings_path = get_settings_path()
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


def get_benchmark_db_path() -> Path:
    """Return the SQLite benchmark history path."""
    return get_state_dir() / "benchmark_history.sqlite"


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
                status TEXT NOT NULL,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_benchmarks_instance_time "
            "ON benchmarks(instance_name, timestamp)"
        )
    return path


def record_benchmark_result(
    result: BenchmarkResult,
    db_path: Path | None = None,
) -> None:
    """Append a benchmark result to SQLite history."""
    path = init_benchmark_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO benchmarks (
                timestamp, instance_name, config_hash, prompt_file, prompt_sha256,
                prompt_chars, output_tokens, tokens_per_second, latency_ms,
                elapsed_ms, vram_mb, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.timestamp,
                result.instance_name,
                result.config_hash,
                result.prompt_file,
                result.prompt_sha256,
                result.prompt_chars,
                result.output_tokens,
                result.tokens_per_second,
                result.latency_ms,
                result.elapsed_ms,
                result.vram_mb,
                result.status,
                result.error,
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

    return {
        row["instance_name"]: BenchmarkResult(
            instance_name=row["instance_name"],
            timestamp=row["timestamp"],
            config_hash=row["config_hash"],
            prompt_file=row["prompt_file"],
            prompt_sha256=row["prompt_sha256"],
            prompt_chars=row["prompt_chars"],
            output_tokens=row["output_tokens"],
            tokens_per_second=row["tokens_per_second"],
            latency_ms=row["latency_ms"],
            elapsed_ms=row["elapsed_ms"],
            vram_mb=row["vram_mb"],
            status=row["status"],
            error=row["error"],
        )
        for row in rows
    }


def _parse_vram_from_text(text: str) -> float | None:
    numbers = re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)?", text, re.IGNORECASE)
    if not numbers:
        return None
    value, unit = numbers[0]
    return _normalize_vram_value(float(value), unit)


def _normalize_vram_value(value: float, unit: str | None) -> float:
    if unit and unit.lower() in {"gib", "gb"}:
        return value * 1024
    return value


def _device_log_label(backend: str, device_id: int) -> str | None:
    prefixes = {
        "vulkan": "Vulkan",
        "cuda": "CUDA",
        "hip": "HIP",
        "metal": "Metal",
    }
    prefix = prefixes.get(backend.lower())
    if prefix is None:
        return None
    return f"{prefix}{device_id}"


def sample_vram_mb_from_log(
    stderr_log: Path,
    *,
    backend: str,
    device_id: int,
) -> float | None:
    """Best-effort VRAM estimate from llama.cpp stderr when vendor CLIs are unavailable."""
    if not stderr_log.exists():
        return None

    device_label = _device_log_label(backend, device_id)
    if device_label is None:
        return None

    try:
        lines = stderr_log.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

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
            return _normalize_vram_value(float(match.group(1)), match.group(2))

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
            return max(0.0, total_mb - latest_free_mb)

    return None


def sample_vram_mb(
    device_id: int = 0,
    *,
    backend: str | None = None,
    stderr_log: Path | None = None,
) -> float | None:
    """Best-effort current GPU memory sampling for common Windows GPU tools."""
    commands = [
        [
            "nvidia-smi",
            f"--id={device_id}",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        ["amd-smi", "metric", "-g", str(device_id), "-m", "--json"],
        ["rocm-smi", "--showmemuse", "--json"],
    ]

    for command in commands:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        parsed = _parse_vram_from_text(proc.stdout)
        if parsed is not None:
            return parsed

    if backend and stderr_log is not None:
        return sample_vram_mb_from_log(stderr_log, backend=backend, device_id=device_id)

    return None


def _extract_token_count(payload: dict[str, Any], fallback_text: str) -> int:
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

    return max(1, len(fallback_text.split()))


def quick_benchmark_instance(
    config: InstanceConfig,
    settings: BenchmarkSettings | None = None,
) -> BenchmarkResult:
    """Run a standard prompt against a live llama.cpp server and persist metrics."""
    active_settings = settings or load_benchmark_settings()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cfg_hash = config_hash(config)
    prompt_file = active_settings.prompt_file.name
    stderr_log = get_log_files(config.name)[1]

    try:
        prompt, prompt_digest = read_prompt(active_settings)
        started = time.perf_counter()
        first_token_at: float | None = None
        chunks: list[str] = []
        final_payload: dict[str, Any] = {}
        url = f"http://{config.server.host}:{config.server.port}/completion"
        request_body = {
            "prompt": prompt,
            "n_predict": active_settings.max_tokens,
            "temperature": 0,
            "stream": True,
            "cache_prompt": False,
        }

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
                content = payload.get("content")
                if isinstance(content, str) and content:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    chunks.append(content)

        ended = time.perf_counter()
        output_text = "".join(chunks)
        output_tokens = _extract_token_count(final_payload, output_text)
        elapsed_ms = (ended - started) * 1000
        latency_ms = ((first_token_at or ended) - started) * 1000
        generation_seconds = max(0.001, (ended - (first_token_at or started)))
        tokens_per_second = output_tokens / generation_seconds
        vram_mb = sample_vram_mb(
            config.gpu.device_id,
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
            tokens_per_second=tokens_per_second,
            latency_ms=latency_ms,
            elapsed_ms=elapsed_ms,
            vram_mb=vram_mb,
            status="ok",
        )
    except Exception as exc:
        prompt = ""
        prompt_digest = ""
        with suppress(Exception):
            prompt, prompt_digest = read_prompt(active_settings)
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
            vram_mb=sample_vram_mb(
                config.gpu.device_id,
                backend=config.gpu.backend,
                stderr_log=stderr_log,
            ),
            status="failed",
            error=str(exc),
        )

    record_benchmark_result(result)
    return result
