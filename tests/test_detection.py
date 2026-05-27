"""Tests for runtime CPU/GPU detection helpers."""

from pathlib import Path

from llama_orchestrator.config.schema import GpuConfig, InstanceConfig, ModelConfig, ServerConfig
from llama_orchestrator.engine.detection import (
    DetectedGpu,
    collect_detected_gpu_inventory,
    describe_effective_runtime,
    parse_detected_gpus,
    parse_vulkaninfo_summary,
)


def _make_config(**overrides) -> InstanceConfig:
    config = InstanceConfig(
        name="demo",
        model=ModelConfig(
            path=Path("models/demo.gguf"),
            context_size=4096,
            batch_size=512,
            threads=16,
        ),
        server=ServerConfig(port=8001, host="127.0.0.1"),
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=30),
        args=[],
        env={},
    )
    return config.model_copy(update=overrides)


def test_describe_effective_runtime_uses_last_flag_values() -> None:
    """Display metadata should follow the final effective command line flags."""
    config = _make_config(args=["--threads", "8", "--n-gpu-layers", "0"])

    selection = describe_effective_runtime(config)

    assert selection.threads == 8
    assert selection.gpu_layers == 0
    assert selection.cpu_active is True
    assert selection.gpu_active is False
    assert selection.gpu_display == "-"


def test_describe_effective_runtime_prefers_explicit_device_args_over_env() -> None:
    """Explicit runtime device args should win over environment defaults for display."""
    config = _make_config(
        env={"GGML_VULKAN_DEVICE": "2"},
        args=["--device", "Vulkan2,Vulkan0", "--main-gpu", "0"],
    )

    selection = describe_effective_runtime(config)

    assert selection.gpu_active is True
    assert selection.gpu_labels == ("Vulkan2", "Vulkan0")
    assert selection.gpu_display == "Vulkan2, Vulkan0"
    assert selection.primary_gpu_label == "Vulkan0"
    assert selection.primary_device_id == 0


def test_describe_effective_runtime_uses_env_override_when_no_explicit_device_flag_exists() -> None:
    """Merged config env overrides should drive the displayed GPU label when no --device is set."""
    config = _make_config(env={"GGML_VULKAN_DEVICE": "2"})

    selection = describe_effective_runtime(config)

    assert selection.gpu_active is True
    assert selection.gpu_labels == ("Vulkan2",)
    assert selection.gpu_display == "Vulkan2"
    assert selection.primary_device_id == 2


def test_describe_effective_runtime_keeps_draft_gpu_visible_without_replacing_primary_gpu() -> None:
    """Draft-device routing should extend the display without overriding the main GPU selection."""
    config = _make_config(
        env={"GGML_VULKAN_DEVICE": "2"},
        args=["--device-draft", "Vulkan0"],
    )

    selection = describe_effective_runtime(config)

    assert selection.gpu_labels == ("Vulkan2", "Vulkan0")
    assert selection.gpu_display == "Vulkan2, Vulkan0"
    assert selection.primary_gpu_label == "Vulkan2"
    assert selection.primary_device_id == 2


def test_parse_detected_gpus_reads_inventory_and_active_device_names() -> None:
    """Vulkan startup logs should expose both indexed labels and adapter names."""
    text = "\n".join(
        [
            "0.00 I   - Vulkan0 : AMD Radeon(TM) Graphics (49047 MiB, 46594 MiB free)",
            "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
            "llama_model_load_from_file_impl: using device Vulkan1 (AMD Radeon RX 6800) (unknown id) - 10698 MiB free",
        ]
    )

    detected = parse_detected_gpus(text)

    assert detected[0].label == "Vulkan0"
    assert detected[0].name == "AMD Radeon(TM) Graphics"
    assert detected[1].label == "Vulkan1"
    assert detected[1].name == "AMD Radeon RX 6800"


def test_parse_vulkaninfo_summary_reads_current_vulkan_labels() -> None:
    """The live Vulkan probe should map VulkanN labels to current adapter names."""
    text = "\n".join(
        [
            "Devices:",
            "========",
            "GPU0:",
            "\tdeviceName         = AMD Radeon(TM) Graphics",
            "GPU1:",
            "\tdeviceName         = Radeon (TM) RX 480 Graphics",
            "GPU2:",
            "\tdeviceName         = AMD Radeon RX 6800",
        ]
    )

    detected = parse_vulkaninfo_summary(text)

    assert detected == [
        DetectedGpu(label="Vulkan0", name="AMD Radeon(TM) Graphics"),
        DetectedGpu(label="Vulkan1", name="Radeon (TM) RX 480 Graphics"),
        DetectedGpu(label="Vulkan2", name="AMD Radeon RX 6800"),
    ]


def test_collect_detected_gpu_inventory_falls_back_to_label_without_log_name(tmp_path: Path) -> None:
    """Inventory should still expose the selected label when stderr has no adapter name yet."""
    config = _make_config(name="demo-no-name")
    log_dir = tmp_path / "logs" / config.name
    log_dir.mkdir(parents=True)
    (log_dir / "stderr.log").write_text("", encoding="utf-8")

    from unittest.mock import patch

    with patch(
        "llama_orchestrator.engine.detection.probe_vulkan_gpu_inventory",
        return_value=[],
    ), patch(
        "llama_orchestrator.engine.detection.get_log_files",
        return_value=(log_dir / "stdout.log", log_dir / "stderr.log"),
    ):
        detected = collect_detected_gpu_inventory([config])

    assert detected == [type(detected[0])(label="Vulkan0", name=None)]


def test_collect_detected_gpu_inventory_prefers_live_vulkan_map_over_stale_logs(
    tmp_path: Path,
) -> None:
    """Current Vulkan ordering should win over stale llama.cpp stderr inventories."""
    config = _make_config(name="demo-stale-log", args=["--device", "Vulkan2"])
    log_dir = tmp_path / "logs" / config.name
    log_dir.mkdir(parents=True)
    (log_dir / "stderr.log").write_text(
        "\n".join(
            [
                "0.00 I   - Vulkan1 : AMD Radeon RX 6800 (16368 MiB, 15569 MiB free)",
                "0.00 I   - Vulkan2 : Radeon (TM) RX 480 Graphics (8192 MiB, 7367 MiB free)",
            ]
        ),
        encoding="utf-8",
    )

    from unittest.mock import patch

    with patch(
        "llama_orchestrator.engine.detection.probe_vulkan_gpu_inventory",
        return_value=[
            DetectedGpu(label="Vulkan1", name="Radeon (TM) RX 480 Graphics"),
            DetectedGpu(label="Vulkan2", name="AMD Radeon RX 6800"),
        ],
    ), patch(
        "llama_orchestrator.engine.detection.get_log_files",
        return_value=(log_dir / "stdout.log", log_dir / "stderr.log"),
    ):
        detected = collect_detected_gpu_inventory([config])

    assert DetectedGpu(label="Vulkan2", name="AMD Radeon RX 6800") in detected
    assert DetectedGpu(label="Vulkan2", name="Radeon (TM) RX 480 Graphics") not in detected
