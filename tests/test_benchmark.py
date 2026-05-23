"""Tests for benchmark helper behavior."""

import json
import sqlite3
import subprocess
from pathlib import Path

from llama_orchestrator.benchmark import (
    BenchmarkResult,
    BenchmarkSettings,
    _parse_vram_from_text,
    build_benchmark_request_body,
    config_hash,
    get_benchmark_endpoint_path,
    get_default_prompt_file,
    get_validated_benchmark_pid,
    init_benchmark_db,
    latest_benchmark_results,
    load_benchmark_settings,
    record_benchmark_result,
    sample_gpu_memory,
    sample_vram_mb_from_log,
    save_benchmark_settings,
    write_benchmark_artifact,
)
from llama_orchestrator.config import InstanceConfig, ModelConfig


def test_default_prompt_file_is_created(tmp_path: Path) -> None:
    """The default benchmark prompt is a normal editable text file."""
    prompt_file = get_default_prompt_file(tmp_path)

    assert prompt_file == tmp_path / "benchmarks" / "prompts" / "default.txt"
    assert prompt_file.exists()
    assert "benchmarking" in prompt_file.read_text(encoding="utf-8").lower()


def test_benchmark_settings_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Prompt file settings survive reload and keep relative paths portable."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.benchmark.get_state_dir", lambda: state_dir)

    prompt_file = tmp_path / "benchmarks" / "prompts" / "renamed.md"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text("Prompt v2", encoding="utf-8")

    save_benchmark_settings(
        BenchmarkSettings(
            prompt_file=prompt_file,
            max_tokens=64,
            temperature=0.2,
            top_p=0.9,
            top_k=50,
            repeat_penalty=1.15,
            seed=42,
            endpoint="completion",
            ignore_eos=True,
        ),
        tmp_path,
    )
    loaded = load_benchmark_settings(tmp_path)

    assert loaded.prompt_file == prompt_file
    assert loaded.max_tokens == 64
    assert loaded.temperature == 0.2
    assert loaded.top_p == 0.9
    assert loaded.top_k == 50
    assert loaded.repeat_penalty == 1.15
    assert loaded.seed == 42
    assert loaded.endpoint == "completion"
    assert loaded.ignore_eos is True


def test_benchmark_settings_clamp_invalid_values(tmp_path: Path, monkeypatch) -> None:
    """Loaded benchmark settings should stay inside GUI-supported ranges."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.benchmark.get_state_dir", lambda: state_dir)

    prompt_file = get_default_prompt_file(tmp_path)
    (state_dir / "benchmark_settings.json").write_text(
        json.dumps(
            {
                "prompt_file": str(prompt_file),
                "max_tokens": -10,
                "temperature": -1,
                "top_p": 2,
                "top_k": -4,
                "repeat_penalty": -0.5,
                "seed": -4,
                "endpoint": "unknown",
                "ignore_eos": "yes",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_benchmark_settings(tmp_path)

    assert loaded.max_tokens == 1
    assert loaded.temperature == 0.0
    assert loaded.top_p == 1.0
    assert loaded.top_k == 0
    assert loaded.repeat_penalty == 0.0
    assert loaded.seed == -1
    assert loaded.endpoint == "chat_completions"
    assert loaded.ignore_eos is True


def test_build_benchmark_request_body_includes_custom_completion_params(tmp_path: Path) -> None:
    """Quick benchmark should pass persisted sampling controls to /completion."""
    settings = BenchmarkSettings(
        prompt_file=tmp_path / "prompt.txt",
        max_tokens=32,
        temperature=0.1,
        top_p=0.95,
        top_k=40,
        repeat_penalty=1.05,
        seed=123,
        endpoint="completion",
        ignore_eos=True,
    )

    assert get_benchmark_endpoint_path(settings) == "/completion"
    assert build_benchmark_request_body(settings, "Prompt") == {
        "prompt": "Prompt",
        "n_predict": 32,
        "temperature": 0.1,
        "stream": True,
        "cache_prompt": False,
        "top_p": 0.95,
        "top_k": 40,
        "repeat_penalty": 1.05,
        "seed": 123,
        "ignore_eos": True,
    }


def test_build_benchmark_request_body_uses_chat_endpoint_by_default(tmp_path: Path) -> None:
    """The default benchmark endpoint should apply the model chat template."""
    settings = BenchmarkSettings(prompt_file=tmp_path / "prompt.txt")

    body = build_benchmark_request_body(settings, "Prompt")

    assert get_benchmark_endpoint_path(settings) == "/v1/chat/completions"
    assert body["messages"] == [{"role": "user", "content": "Prompt"}]
    assert body["max_tokens"] == 200
    assert body["temperature"] == 0.0
    assert body["stream"] is True
    assert "n_predict" not in body
    assert "top_p" not in body
    assert "top_k" not in body
    assert "repeat_penalty" not in body
    assert "seed" not in body
    assert "ignore_eos" not in body


def test_config_hash_changes_when_runtime_args_change() -> None:
    """Benchmark history can distinguish runtime config variants."""
    config = InstanceConfig(name="bench", model=ModelConfig(path=Path("model.gguf")))
    baseline = config_hash(config)

    config.args.append("--flash-attn")

    assert config_hash(config) != baseline


def test_benchmark_history_latest_per_instance(tmp_path: Path) -> None:
    """SQLite history returns the newest row for each model instance."""
    db_path = tmp_path / "benchmarks.sqlite"
    first = BenchmarkResult(
        instance_name="bench",
        timestamp="2026-05-16T10:00:00+0000",
        config_hash="a",
        prompt_file="default.txt",
        prompt_sha256="sha",
        prompt_chars=10,
        output_tokens=10,
        tokens_per_second=5.0,
        latency_ms=100.0,
        elapsed_ms=2000.0,
        vram_mb=1024.0,
        status="ok",
    )
    second = BenchmarkResult(
        instance_name="bench",
        timestamp="2026-05-16T10:05:00+0000",
        config_hash="b",
        prompt_file="renamed.txt",
        prompt_sha256="sha2",
        prompt_chars=20,
        output_tokens=20,
        tokens_per_second=10.0,
        latency_ms=80.0,
        elapsed_ms=2000.0,
        vram_mb=2048.0,
        status="ok",
        artifact_file="logs/bench/benchmarks/second.md",
    )

    record_benchmark_result(first, db_path)
    record_benchmark_result(second, db_path)

    latest = latest_benchmark_results(db_path)

    assert latest["bench"].config_hash == "b"
    assert latest["bench"].prompt_file == "renamed.txt"
    assert latest["bench"].tokens_per_second == 10.0
    assert latest["bench"].dedicated_vram_mb == 2048.0
    assert latest["bench"].shared_ram_mb is None
    assert latest["bench"].total_gpu_memory_mb == 2048.0
    assert latest["bench"].artifact_file == "logs/bench/benchmarks/second.md"


def test_benchmark_history_additive_schema_keeps_legacy_rows(tmp_path: Path) -> None:
    """Older benchmark rows should load even after memory-split columns are added."""
    db_path = tmp_path / "benchmarks.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE benchmarks (
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
            """
            INSERT INTO benchmarks (
                timestamp, instance_name, config_hash, prompt_file, prompt_sha256,
                prompt_chars, output_tokens, tokens_per_second, latency_ms,
                elapsed_ms, vram_mb, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-19T12:00:00+0000",
                "legacy",
                "cfg",
                "default.txt",
                "sha",
                10,
                20,
                12.0,
                80.0,
                1500.0,
                4096.0,
                "ok",
                None,
            ),
        )

    init_benchmark_db(db_path)
    latest = latest_benchmark_results(db_path)

    assert latest["legacy"].dedicated_vram_mb == 4096.0
    assert latest["legacy"].shared_ram_mb is None
    assert latest["legacy"].total_gpu_memory_mb == 4096.0
    assert latest["legacy"].artifact_file is None


def test_write_benchmark_artifact_records_prompt_output_and_stats(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Each benchmark run should have a readable artifact with prompt and output."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr("llama_orchestrator.benchmark.get_state_dir", lambda: state_dir)
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("llama_orchestrator.benchmark.get_logs_dir", lambda: logs_dir)
    config = InstanceConfig(name="bench-demo", model=ModelConfig(path=Path("model.gguf")))
    settings = BenchmarkSettings(
        prompt_file=tmp_path / "prompt.txt",
        max_tokens=32,
        temperature=0.1,
        top_p=0.9,
    )
    result = BenchmarkResult(
        instance_name="bench-demo",
        timestamp="2026-05-23T15:45:00+0200",
        config_hash="cfg123",
        prompt_file="prompt.txt",
        prompt_sha256="sha",
        prompt_chars=18,
        output_tokens=12,
        tokens_per_second=24.5,
        latency_ms=100.0,
        elapsed_ms=750.0,
        vram_mb=1024.0,
        status="ok",
        dedicated_vram_mb=1024.0,
        shared_ram_mb=0.0,
        total_gpu_memory_mb=1024.0,
    )

    artifact = write_benchmark_artifact(
        result,
        config=config,
        settings=settings,
        prompt_text="Prompt body",
        output_text="Model output",
        request_body=build_benchmark_request_body(settings, "Prompt body"),
        final_payload={"timings": {"predicted_n": 12}},
    )

    content = artifact.read_text(encoding="utf-8")

    assert artifact.parent == logs_dir / "bench-demo" / "benchmarks"
    assert "# Quick Benchmark - bench-demo" in content
    assert "Prompt body" in content
    assert "Model output" in content
    assert "| Tokens/sec | 24.500 |" in content
    assert '"max_tokens": 32' in content
    assert '"temperature": 0.1' in content
    assert '"endpoint": "chat_completions"' in content


def test_parse_vram_from_text_supports_gib_units() -> None:
    """VRAM parser should normalize GiB values to MB for storage/display."""
    assert _parse_vram_from_text("GPU memory used: 15.5 GiB") == 15872.0


def test_sample_gpu_memory_prefers_windows_process_counters(monkeypatch) -> None:
    """Process-scoped Windows counters should provide dedicated/shared split when available."""

    def fake_run(command, **kwargs):
        assert command[0] in {"pwsh", "powershell"}
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"dedicated_vram_mb": 16261.3, "shared_ram_mb": 175.2}),
            stderr="",
        )

    monkeypatch.setattr("llama_orchestrator.benchmark.subprocess.run", fake_run)

    sample = sample_gpu_memory(pid=12212)

    assert sample.dedicated_vram_mb == 16261.3
    assert sample.shared_ram_mb == 175.2
    assert sample.total_gpu_memory_mb == 16436.5


def test_get_validated_benchmark_pid_returns_none_for_invalid_process(monkeypatch) -> None:
    """Process-scoped sampling should be skipped for stale or mismatched runtime state."""

    class FakeValidation:
        actual_pid = None
        expected_pid = 12212

        def is_valid(self) -> bool:
            return False

    monkeypatch.setattr("llama_orchestrator.benchmark.validate_process", lambda name: FakeValidation())

    assert get_validated_benchmark_pid("demo") is None


def test_sample_gpu_memory_falls_back_to_dedicated_only_when_process_counters_are_unavailable(
    monkeypatch,
) -> None:
    """Cross-machine fallback should keep shared RAM unknown when Windows counters fail."""
    monkeypatch.setattr(
        "llama_orchestrator.benchmark._sample_windows_gpu_process_memory",
        lambda pid: None,
    )
    monkeypatch.setattr(
        "llama_orchestrator.benchmark.sample_vram_mb",
        lambda *args, **kwargs: 4861.28,
    )

    sample = sample_gpu_memory(pid=12212, device_id=1, backend="vulkan")

    assert sample.dedicated_vram_mb == 4861.28
    assert sample.shared_ram_mb is None
    assert sample.total_gpu_memory_mb == 4861.28


def test_sample_vram_mb_from_log_uses_matching_vulkan_buffer_size(tmp_path: Path) -> None:
    """When vendor tools are unavailable, benchmarking can fall back to stderr logs."""
    stderr_log = tmp_path / "stderr.log"
    stderr_log.write_text(
        "\n".join(
            [
                "0.00 I   - Vulkan0 : AMD Radeon(TM) Graphics (49047 MiB, 46594 MiB free)",
                "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
                "load_tensors:      Vulkan1 model buffer size =  4861.28 MiB",
                "llama_model_load_from_file_impl: using device Vulkan1 (AMD Radeon RX 6800) (unknown id) - 10698 MiB free",
            ]
        ),
        encoding="utf-8",
    )

    assert sample_vram_mb_from_log(stderr_log, backend="vulkan", device_id=1) == 4861.28


def test_sample_vram_mb_from_log_falls_back_to_total_minus_free(tmp_path: Path) -> None:
    """If no model buffer line is present, use the latest device free memory delta."""
    stderr_log = tmp_path / "stderr.log"
    stderr_log.write_text(
        "\n".join(
            [
                "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
                "llama_model_load_from_file_impl: using device Vulkan1 (AMD Radeon RX 6800) (unknown id) - 10698 MiB free",
            ]
        ),
        encoding="utf-8",
    )

    assert sample_vram_mb_from_log(stderr_log, backend="vulkan", device_id=1) == 5670.0
