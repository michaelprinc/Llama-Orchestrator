"""Tests for benchmark helper behavior."""

from pathlib import Path

from llama_orchestrator.benchmark import (
    BenchmarkResult,
    BenchmarkSettings,
    _parse_vram_from_text,
    config_hash,
    get_default_prompt_file,
    latest_benchmark_results,
    load_benchmark_settings,
    record_benchmark_result,
    sample_vram_mb_from_log,
    save_benchmark_settings,
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

    save_benchmark_settings(BenchmarkSettings(prompt_file=prompt_file, max_tokens=64), tmp_path)
    loaded = load_benchmark_settings(tmp_path)

    assert loaded.prompt_file == prompt_file
    assert loaded.max_tokens == 64


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
    )

    record_benchmark_result(first, db_path)
    record_benchmark_result(second, db_path)

    latest = latest_benchmark_results(db_path)

    assert latest["bench"].config_hash == "b"
    assert latest["bench"].prompt_file == "renamed.txt"
    assert latest["bench"].tokens_per_second == 10.0


def test_parse_vram_from_text_supports_gib_units() -> None:
    """VRAM parser should normalize GiB values to MB for storage/display."""
    assert _parse_vram_from_text("GPU memory used: 15.5 GiB") == 15872.0


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
