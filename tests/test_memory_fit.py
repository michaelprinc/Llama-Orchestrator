"""Tests for config-driven memory fit estimation."""

from __future__ import annotations

import struct
from pathlib import Path

from llama_orchestrator.config import GpuConfig, InstanceConfig, ModelConfig, ServerConfig
from llama_orchestrator.memory_fit import (
    estimate_instance_memory,
    load_gguf_metadata,
    parse_dedicated_vram_budget_from_log,
)

_GGUF_TYPE_STRING = 8
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_UINT64 = 10


def _write_gguf_string(handle, value: str) -> None:
    encoded = value.encode("utf-8")
    handle.write(struct.pack("<Q", len(encoded)))
    handle.write(encoded)


def _write_gguf_value(handle, value) -> None:
    if isinstance(value, str):
        handle.write(struct.pack("<I", _GGUF_TYPE_STRING))
        _write_gguf_string(handle, value)
        return
    if isinstance(value, int):
        gguf_type = _GGUF_TYPE_UINT32 if value <= 0xFFFFFFFF else _GGUF_TYPE_UINT64
        handle.write(struct.pack("<I", gguf_type))
        handle.write(struct.pack("<I" if gguf_type == _GGUF_TYPE_UINT32 else "<Q", value))
        return
    raise TypeError(f"Unsupported test GGUF metadata value: {value!r}")


def _write_test_gguf(path: Path, metadata: dict[str, object]) -> None:
    with path.open("wb") as handle:
        handle.write(b"GGUF")
        handle.write(struct.pack("<I", 3))
        handle.write(struct.pack("<Q", 0))
        handle.write(struct.pack("<Q", len(metadata)))
        for key, value in metadata.items():
            _write_gguf_string(handle, key)
            _write_gguf_value(handle, value)


def _make_config(
    model_path: Path,
    *,
    args: list[str] | None = None,
    layers: int = 999,
    context_size: int = 65536,
) -> InstanceConfig:
    return InstanceConfig(
        name="demo",
        model=ModelConfig(path=model_path, context_size=context_size, batch_size=512, threads=8),
        server=ServerConfig(port=8080, parallel=1),
        gpu=GpuConfig(backend="vulkan", device_id=1, layers=layers),
        args=args or [],
    )


def test_load_gguf_metadata_reads_common_transformer_keys(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 131072,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )

    metadata = load_gguf_metadata(model_path)

    assert metadata is not None
    assert metadata.architecture == "llama"
    assert metadata.context_length == 131072
    assert metadata.block_count == 32
    assert metadata.embedding_length == 4096
    assert metadata.attention_head_count == 32
    assert metadata.attention_head_count_kv == 8


def test_estimate_instance_memory_reports_unknown_without_budget(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 131072,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path)

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=8 * 1024 * 1024 * 1024,
    )

    assert estimate.classification == "unknown"
    assert estimate.confidence == "low"
    assert estimate.estimated_model_resident_mb == 8192.0
    assert estimate.estimated_kv_cache_mb == 8192.0
    assert estimate.estimated_total_required_mb == 16640.0
    assert estimate.budget_mb_used is None
    assert any("budget is unavailable" in reason.lower() for reason in estimate.reasons)


def test_estimate_instance_memory_detects_fit_and_shared_ram_cases(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path, context_size=4096)

    fits = estimate_instance_memory(
        config,
        model_size_bytes=7 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=9000.0,
    )
    spills = estimate_instance_memory(
        config,
        model_size_bytes=9 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=9000.0,
    )

    assert fits.classification == "fits_dedicated_vram"
    assert fits.confidence == "high"
    assert fits.estimated_shared_ram_mb_lower_bound == 0.0
    assert spills.classification == "likely_shared_ram"
    assert spills.estimated_shared_ram_mb_lower_bound is not None
    assert spills.estimated_shared_ram_mb_lower_bound > 0.0


def test_estimate_instance_memory_respects_no_kv_offload(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path, args=["--no-kv-offload"])

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=6 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=7000.0,
    )

    assert estimate.kv_offload_enabled is False
    assert estimate.estimated_kv_cache_mb is None
    assert estimate.classification == "fits_dedicated_vram"


def test_estimate_instance_memory_reports_cpu_only_when_gpu_is_disabled(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(model_path, {"general.architecture": "llama"})
    config = InstanceConfig(
        name="cpu-only",
        model=ModelConfig(path=model_path, context_size=4096, batch_size=512, threads=8),
        server=ServerConfig(port=8080, parallel=1),
        gpu=GpuConfig(backend="cpu", device_id=0, layers=0),
    )

    estimate = estimate_instance_memory(config, model_size_bytes=4 * 1024 * 1024 * 1024)

    assert estimate.classification == "cpu_only"
    assert estimate.confidence == "high"


def test_estimate_instance_memory_keeps_parallel_slot_fit_unknown(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = InstanceConfig(
        name="parallel-demo",
        model=ModelConfig(path=model_path, context_size=4096, batch_size=512, threads=8),
        server=ServerConfig(port=8080, parallel=4),
        gpu=GpuConfig(backend="vulkan", device_id=1, layers=999),
    )

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=7 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=12000.0,
    )

    assert estimate.parallel_slots == 4
    assert estimate.classification == "unknown"
    assert estimate.confidence == "low"
    assert "parallel_slots" in estimate.unsupported_inputs


def test_estimate_instance_memory_preserves_parallel_slot_overflow_signal(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = InstanceConfig(
        name="parallel-overflow",
        model=ModelConfig(path=model_path, context_size=4096, batch_size=512, threads=8),
        server=ServerConfig(port=8080, parallel=4),
        gpu=GpuConfig(backend="vulkan", device_id=1, layers=999),
    )

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=9 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=9000.0,
    )

    assert estimate.classification == "likely_shared_ram"
    assert estimate.confidence == "low"
    assert estimate.estimated_shared_ram_mb_lower_bound is not None
    assert estimate.estimated_shared_ram_mb_lower_bound > 0


def test_estimate_instance_memory_uses_effective_command_batch_override(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path, context_size=4096)

    estimate = estimate_instance_memory(
        config,
        effective_command=["llama-server", "--ctx-size=2048", "--batch-size=2048"],
        model_size_bytes=6 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=12000.0,
    )

    assert estimate.batch_size == 2048
    assert estimate.context_size == 2048
    assert estimate.estimated_runtime_overhead_mb == 1024.0


def test_estimate_instance_memory_uses_larger_ubatch_size_for_overhead(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path, context_size=4096)

    estimate = estimate_instance_memory(
        config,
        effective_command=[
            "llama-server",
            "--ctx-size",
            "4096",
            "--batch-size",
            "1024",
            "--ubatch-size",
            "3072",
        ],
        model_size_bytes=6 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=12000.0,
    )

    assert estimate.batch_size == 1024
    assert estimate.ubatch_size == 3072
    assert estimate.estimated_runtime_overhead_mb == 1536.0


def test_estimate_instance_memory_marks_complex_runtime_inputs_low_confidence(tmp_path: Path) -> None:
    """Estimator should stay advisory for split, draft, and CPU-MoE runtimes."""
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(
        model_path,
        args=[
            "--device",
            "Vulkan1,Vulkan2",
            "--split-mode",
            "layer",
            "--tensor-split",
            "70,30",
            "--model-draft",
            "draft.gguf",
            "--n-cpu-moe",
            "12",
        ],
        context_size=4096,
    )

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=6 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=16000.0,
    )

    assert estimate.confidence == "low"
    assert "multi_gpu_device_list" in estimate.unsupported_inputs
    assert "split_mode:layer" in estimate.unsupported_inputs
    assert "tensor_split" in estimate.unsupported_inputs
    assert "speculative_draft_runtime" in estimate.unsupported_inputs
    assert "n_cpu_moe" in estimate.unsupported_inputs


def test_estimate_instance_memory_uses_effective_command_device_for_budget_log(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    stderr_log = tmp_path / "stderr.log"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 65536,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
        },
    )
    stderr_log.write_text(
        "\n".join(
            [
                "0.00 I   - Vulkan0 : AMD Radeon(TM) Graphics (4096 MiB, 2048 MiB free)",
                "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
            ]
        ),
        encoding="utf-8",
    )
    config = _make_config(model_path)

    estimate = estimate_instance_memory(
        config,
        effective_command=["llama-server", "--device=1"],
        model_size_bytes=6 * 1024 * 1024 * 1024,
        stderr_log=stderr_log,
    )

    assert estimate.budget_mb_used == 16368.0


def test_estimate_instance_memory_uses_block_count_for_partial_offload(tmp_path: Path) -> None:
    model_path = tmp_path / "demo.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 32768,
            "llama.block_count": 40,
            "llama.embedding_length": 5120,
            "llama.attention.head_count": 40,
            "llama.attention.head_count_kv": 8,
        },
    )
    config = _make_config(model_path, layers=20, context_size=4096)

    estimate = estimate_instance_memory(
        config,
        model_size_bytes=10 * 1024 * 1024 * 1024,
        dedicated_vram_budget_mb=7000.0,
    )

    assert estimate.estimated_model_resident_mb == 5120.0
    assert estimate.classification == "fits_dedicated_vram"


def test_parse_dedicated_vram_budget_from_log_uses_matching_inventory_line(tmp_path: Path) -> None:
    stderr_log = tmp_path / "stderr.log"
    stderr_log.write_text(
        "\n".join(
            [
                "0.00 I   - Vulkan0 : AMD Radeon(TM) Graphics (49047 MiB, 46594 MiB free)",
                "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_dedicated_vram_budget_from_log(stderr_log, backend="vulkan", device_id=1) == 16368.0
