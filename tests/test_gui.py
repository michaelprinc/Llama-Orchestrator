"""Tests for GUI helper behavior."""

from unittest.mock import patch

from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus
from llama_orchestrator.gui import (
    DEFAULT_RUNTIME_ARGS,
    EDIT_BENCHMARK_PROMPT_LABEL,
    INSTALL_LLAMA_SERVER_LABEL,
    VULKAN_BINARY_MISSING_MESSAGE,
    apply_managed_runtime_args,
    derive_display_status_and_health,
    parse_tag_string,
    persist_instance_health,
)


def test_apply_managed_runtime_args_defaults() -> None:
    """Default GUI args include requested llama-server flags."""
    assert apply_managed_runtime_args([]) == DEFAULT_RUNTIME_ARGS


def test_apply_managed_runtime_args_replaces_existing_values() -> None:
    """Managed flags are replaced instead of duplicated."""
    args = [
        "--threads",
        "8",
        "--reasoning",
        "auto",
        "--flash-attn",
        "off",
        "--no-mmproj",
    ]

    assert apply_managed_runtime_args(args) == [
        "--threads",
        "8",
        "--no-mmproj",
        "--reasoning",
        "off",
        "--flash-attn",
        "auto",
    ]


def test_parse_tag_string_normalizes_unique_tags() -> None:
    """Tags can be typed as comma or space separated values."""
    assert parse_tag_string("Qwen35-family, rx480-test qwen35-family") == [
        "qwen35-family",
        "rx480-test",
    ]


def test_display_status_distinguishes_loading_from_ready() -> None:
    """The GUI should not present a ready model as merely running/loading."""
    loading = InstanceState(name="demo", status=InstanceStatus.RUNNING, health=HealthStatus.LOADING)
    ready = InstanceState(name="demo", status=InstanceStatus.RUNNING, health=HealthStatus.HEALTHY)

    assert derive_display_status_and_health(loading) == ("loading", "loading")
    assert derive_display_status_and_health(ready) == ("ready", "healthy")


def test_display_status_leaves_non_running_states_unchanged() -> None:
    """Stopped and error states should preserve their existing semantics."""
    stopped = InstanceState(name="demo", status=InstanceStatus.STOPPED, health=HealthStatus.UNKNOWN)
    failed = InstanceState(name="demo", status=InstanceStatus.ERROR, health=HealthStatus.ERROR)

    assert derive_display_status_and_health(stopped) == ("stopped", "unknown")
    assert derive_display_status_and_health(failed) == ("error", "error")


def test_persist_instance_health_updates_history_and_runtime() -> None:
    """GUI-triggered health updates should mirror the monitor persistence path."""
    state = InstanceState(name="demo", pid=123, status=InstanceStatus.RUNNING, health=HealthStatus.LOADING)

    with patch("llama_orchestrator.gui.save_state") as mock_save_state, \
         patch("llama_orchestrator.gui.load_runtime", return_value=None), \
         patch("llama_orchestrator.gui.save_runtime") as mock_save_runtime, \
         patch("llama_orchestrator.gui.record_health_check") as mock_record_health_check:
        persist_instance_health(
            "demo",
            state,
            port=8080,
            health=HealthStatus.HEALTHY,
            response_time_ms=12.5,
        )

    saved_runtime = mock_save_runtime.call_args.args[0]

    assert mock_save_state.called
    assert saved_runtime.name == "demo"
    assert saved_runtime.health == HealthStatus.HEALTHY
    assert saved_runtime.port == 8080
    mock_record_health_check.assert_called_once_with(
        "demo",
        HealthStatus.HEALTHY,
        response_time_ms=12.5,
        error_message="",
    )


def test_install_binary_gui_copy_uses_llama_server_label() -> None:
    """GUI install copy names the installed runtime, not one backend."""
    assert INSTALL_LLAMA_SERVER_LABEL == "Install llama-server"
    assert "Install Vulkan" not in VULKAN_BINARY_MISSING_MESSAGE
    assert "llama-server variant" in VULKAN_BINARY_MISSING_MESSAGE


def test_gui_uses_explicit_benchmark_prompt_edit_label() -> None:
    """The benchmark prompt control should describe the edit action clearly."""
    assert EDIT_BENCHMARK_PROMPT_LABEL == "Edit Benchmark Prompt"
