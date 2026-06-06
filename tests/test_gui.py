"""Tests for GUI helper behavior."""

import threading
from pathlib import Path
from unittest.mock import patch

from llama_orchestrator.benchmark import BenchmarkResult, BenchmarkSettings
from llama_orchestrator.config import InstanceConfig, ModelConfig
from llama_orchestrator.engine.detection import DetectedGpu
from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus
from llama_orchestrator.gui import (
    BENCHMARK_PARAMS_MENU_LABEL,
    COLUMN_HEADINGS,
    COLUMN_WIDTHS,
    CPU_ACTIVE_GLYPH,
    DEFAULT_RUNTIME_ARGS,
    EDIT_BENCHMARK_PROMPT_LABEL,
    GRID_BENCHMARK_LABEL,
    INSTALL_LLAMA_SERVER_LABEL,
    QUEUE_CHECKED_GLYPH,
    QUEUE_UNCHECKED_GLYPH,
    VULKAN_BINARY_MISSING_MESSAGE,
    LlamaOrchestratorGui,
    apply_managed_runtime_args,
    benchmark_shared_ram_warning,
    derive_display_status_and_health,
    format_benchmark_memory,
    format_benchmark_message,
    format_benchmark_settings_summary,
    format_cpu_indicator,
    format_detected_gpu_summary,
    format_download_progress,
    format_model_size_gb,
    format_queue_checkbox,
    format_runtime_gpu_display,
    format_serial_benchmark_progress,
    instance_alias_exists,
    load_gpu_aliases,
    normalize_gpu_alias,
    normalize_model_path_for_config,
    ordered_visible_names,
    parse_grid_number,
    parse_grid_values,
    parse_tag_string,
    persist_instance_health,
    resolve_instance_config_dir,
    resolve_instance_config_path,
    resolve_models_directory_input,
    run_serial_benchmark_queue,
    save_gpu_aliases,
    update_instance_display_name,
)
from llama_orchestrator.hf_import import DownloadProgress


class _FakeQueueTree:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names
        self._selection: tuple[str, ...] = ()
        self.values = dict.fromkeys(names, QUEUE_UNCHECKED_GLYPH)

    def get_children(self) -> tuple[str, ...]:
        return self._names

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def set(self, item: str, column: str, value: str | None = None) -> str:
        assert column == "queue"
        if value is None:
            return self.values[item]
        self.values[item] = value
        return value


def _queue_only_gui(names: tuple[str, ...]) -> LlamaOrchestratorGui:
    gui = object.__new__(LlamaOrchestratorGui)
    gui.tree = _FakeQueueTree(names)
    gui._queued_benchmark_names = set()
    gui._benchmark_job_lock = threading.Lock()
    gui._benchmark_job_active = False
    gui._serial_benchmark_active = False
    gui._serial_benchmark_stop = threading.Event()
    gui.quick_benchmark_button = None
    gui.serial_benchmark_button = None
    gui.stop_serial_benchmark_button = None
    gui.grid_benchmark_button = None
    gui.stop_grid_benchmark_button = None
    gui._grid_benchmark_active = False
    gui._grid_benchmark_stop = threading.Event()
    return gui


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


def test_gui_has_compact_benchmark_params_menu_label() -> None:
    """The benchmark params menu should stay compact next to Quick benchmark."""
    assert BENCHMARK_PARAMS_MENU_LABEL == "Params"


def test_gui_grid_benchmark_label_is_explicit() -> None:
    """The grid benchmark action should be clearly named."""
    assert GRID_BENCHMARK_LABEL == "Grid benchmark"


def test_parse_grid_values_supports_request_value_types() -> None:
    """Grid dialog values should parse into typed request parameters."""
    assert parse_grid_values("32, 64", "int") == (32, 64)
    assert parse_grid_values("0, 0.2", "float") == (0.0, 0.2)
    assert parse_grid_values("false, true", "bool") == (False, True)
    assert parse_grid_values("chat_completions, completion", "enum") == (
        "chat_completions",
        "completion",
    )


def test_parse_grid_number_supports_numeric_ranges() -> None:
    """Grid dialog numeric rows should support min/max/step inputs."""
    assert parse_grid_number("4", "int") == 4
    assert parse_grid_number("0.25", "float") == 0.25
    assert parse_grid_number("", "int") is None


def test_grid_benchmark_targets_are_silent_without_selection_or_queue() -> None:
    """Grid target lookup should not call the noisy selected-instance helper."""
    gui = _queue_only_gui(("alpha", "beta"))

    with patch.object(gui, "_selected_instance") as mock_selected:
        assert gui._grid_benchmark_targets() == ()

    mock_selected.assert_not_called()


def test_grid_benchmark_targets_prefer_queue_then_selection() -> None:
    """Grid benchmark can run from queue or selected row."""
    gui = _queue_only_gui(("alpha", "beta"))
    gui.tree._selection = ("beta",)

    assert gui._grid_benchmark_targets() == ("beta",)

    gui._queued_benchmark_names = {"alpha"}

    assert gui._grid_benchmark_targets() == ("alpha",)


def test_format_benchmark_settings_summary_lists_custom_values() -> None:
    """The activity log should summarize non-default benchmark parameters compactly."""
    settings = BenchmarkSettings(
        prompt_file=Path("default.txt"),
        max_tokens=64,
        temperature=0.2,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.1,
        seed=7,
        endpoint="completion",
        ignore_eos=True,
    )

    assert format_benchmark_settings_summary(settings) == (
        "completion, 64 tok, temp 0.2, top-p 0.9, top-k 40, penalty 1.1, seed 7, ignore EOS"
    )


def test_format_benchmark_memory_includes_total_and_shared_ram_warning() -> None:
    """GUI memory display should surface total usage and shared RAM slowdown context."""
    result = BenchmarkResult(
        instance_name="demo",
        timestamp="2026-05-19T12:00:00+0000",
        config_hash="cfg",
        prompt_file="default.txt",
        prompt_sha256="sha",
        prompt_chars=10,
        output_tokens=20,
        tokens_per_second=12.0,
        latency_ms=80.0,
        elapsed_ms=1400.0,
        vram_mb=16261.3,
        status="ok",
        dedicated_vram_mb=16261.3,
        shared_ram_mb=175.2,
        total_gpu_memory_mb=16436.5,
        artifact_file="logs/demo/benchmarks/run.md",
    )

    assert format_benchmark_memory(result) == "16436 total (VRAM 16261, RAM 175) slow"
    assert benchmark_shared_ram_warning(result) == "Shared RAM in use; inference may be slower."
    assert "Shared RAM in use; inference may be slower." in format_benchmark_message(result)
    assert "Artifact: logs/demo/benchmarks/run.md." in format_benchmark_message(result)


def test_format_benchmark_memory_keeps_legacy_vram_rows_readable() -> None:
    """Historical rows with only vram_mb should still render without split values."""
    result = BenchmarkResult(
        instance_name="legacy",
        timestamp="2026-05-19T12:00:00+0000",
        config_hash="cfg",
        prompt_file="default.txt",
        prompt_sha256="sha",
        prompt_chars=10,
        output_tokens=None,
        tokens_per_second=None,
        latency_ms=None,
        elapsed_ms=None,
        vram_mb=4861.28,
        status="failed",
        error="benchmark failed",
    )

    assert format_benchmark_memory(result) == "4861 total (VRAM 4861)"
    assert benchmark_shared_ram_warning(result) == ""
    assert "Memory: 4861 total (VRAM 4861)." in format_benchmark_message(result)


def test_gui_columns_include_gpu_cpu_and_model_size() -> None:
    """The main table should expose the new runtime hardware summary columns."""
    assert COLUMN_HEADINGS["queue"] == "Queue"
    assert COLUMN_HEADINGS["gpu"] == "GPU"
    assert COLUMN_HEADINGS["cpu"] == "CPU"
    assert COLUMN_HEADINGS["model_size"] == "Model size"
    assert COLUMN_HEADINGS["quantization"] == "Quant"
    assert COLUMN_HEADINGS["architecture"] == "Arch"


def test_queue_checkbox_glyphs_render_checked_and_unchecked_states() -> None:
    """The queue column should look like a checkbox without extra widgets."""
    assert format_queue_checkbox(False) == QUEUE_UNCHECKED_GLYPH
    assert format_queue_checkbox(True) == QUEUE_CHECKED_GLYPH


def test_toggle_queue_name_updates_only_queue_cell_without_refresh() -> None:
    """Single queue clicks should not rebuild the whole Treeview."""
    gui = _queue_only_gui(("alpha", "beta"))

    with patch.object(gui, "refresh") as mock_refresh:
        gui._toggle_queue_name("alpha")

    mock_refresh.assert_not_called()
    assert gui._queued_benchmark_names == {"alpha"}
    assert gui.tree.values == {
        "alpha": QUEUE_CHECKED_GLYPH,
        "beta": QUEUE_UNCHECKED_GLYPH,
    }


def test_update_queue_cells_skips_hidden_rows() -> None:
    """Filtered rows can stay untouched until the next full refresh."""
    gui = _queue_only_gui(("alpha",))
    gui._queued_benchmark_names = {"alpha", "hidden"}

    gui._update_queue_cells(("alpha", "hidden"))

    assert gui.tree.values == {"alpha": QUEUE_CHECKED_GLYPH}


def test_ordered_visible_names_preserves_current_table_order() -> None:
    """Serial benchmark should follow the visible sorted row order."""
    assert ordered_visible_names({"gamma", "alpha"}, ["beta", "alpha", "gamma"]) == (
        "alpha",
        "gamma",
    )


def test_format_serial_benchmark_progress_reports_tps_and_latency() -> None:
    """Queue progress messages should stay compact and readable."""
    result = BenchmarkResult(
        instance_name="demo",
        timestamp="2026-05-19T12:00:00+0000",
        config_hash="cfg",
        prompt_file="default.txt",
        prompt_sha256="sha",
        prompt_chars=10,
        output_tokens=20,
        tokens_per_second=72.1,
        latency_ms=364.0,
        elapsed_ms=1400.0,
        vram_mb=1024.0,
        status="ok",
    )

    assert format_serial_benchmark_progress(result) == "TPS=72.1, latency=364 ms"


def test_format_serial_benchmark_progress_uses_error_for_failed_rows() -> None:
    """Failed queue rows should log the underlying benchmark error."""
    result = BenchmarkResult(
        instance_name="demo",
        timestamp="2026-05-19T12:00:00+0000",
        config_hash="cfg",
        prompt_file="default.txt",
        prompt_sha256="sha",
        prompt_chars=10,
        output_tokens=None,
        tokens_per_second=None,
        latency_ms=None,
        elapsed_ms=None,
        vram_mb=None,
        status="failed",
        error="connection refused",
    )

    assert format_serial_benchmark_progress(result) == "connection refused"


def test_run_serial_benchmark_queue_continues_after_exception() -> None:
    """One failing row should not stop the remaining queued benchmarks."""
    messages: list[str] = []
    active_names: list[str | None] = []
    handled: list[tuple[str, str]] = []
    lifecycle: list[tuple[str, str]] = []

    def run_one(name: str) -> BenchmarkResult:
        lifecycle.append(("benchmark", name))
        if name == "beta":
            raise RuntimeError("connection refused")
        return BenchmarkResult(
            instance_name=name,
            timestamp="2026-05-19T12:00:00+0000",
            config_hash="cfg",
            prompt_file="default.txt",
            prompt_sha256="sha",
            prompt_chars=10,
            output_tokens=20,
            tokens_per_second=72.1,
            latency_ms=364.0,
            elapsed_ms=1400.0,
            vram_mb=1024.0,
            status="ok",
        )

    result = run_serial_benchmark_queue(
        ("alpha", "beta", "gamma"),
        should_stop=lambda: False,
        set_active_name=active_names.append,
        start_one=lambda name: lifecycle.append(("start", name)) or True,
        run_one=run_one,
        stop_one=lambda name: lifecycle.append(("stop", name)),
        handle_exception=lambda name, exc: handled.append((name, str(exc))),
        post_message=messages.append,
    )

    assert result == "[Serial benchmark] finished 3/3."
    assert handled == [("beta", "connection refused")]
    assert messages == [
        "[Serial benchmark] 1/3 starting: alpha",
        "[Serial benchmark] 1/3 started: alpha",
        "[Serial benchmark] 1/3 running: alpha",
        "[Serial benchmark] 1/3 completed: alpha: TPS=72.1, latency=364 ms",
        "[Serial benchmark] 1/3 stopping: alpha",
        "[Serial benchmark] 1/3 stopped: alpha",
        "[Serial benchmark] 2/3 starting: beta",
        "[Serial benchmark] 2/3 started: beta",
        "[Serial benchmark] 2/3 running: beta",
        "[Serial benchmark] 2/3 failed: beta: connection refused",
        "[Serial benchmark] 2/3 stopping: beta",
        "[Serial benchmark] 2/3 stopped: beta",
        "[Serial benchmark] 3/3 starting: gamma",
        "[Serial benchmark] 3/3 started: gamma",
        "[Serial benchmark] 3/3 running: gamma",
        "[Serial benchmark] 3/3 completed: gamma: TPS=72.1, latency=364 ms",
        "[Serial benchmark] 3/3 stopping: gamma",
        "[Serial benchmark] 3/3 stopped: gamma",
    ]
    assert lifecycle == [
        ("start", "alpha"),
        ("benchmark", "alpha"),
        ("stop", "alpha"),
        ("start", "beta"),
        ("benchmark", "beta"),
        ("stop", "beta"),
        ("start", "gamma"),
        ("benchmark", "gamma"),
        ("stop", "gamma"),
    ]
    assert active_names == ["alpha", None, "beta", None, "gamma", None]


def test_run_serial_benchmark_queue_stops_before_next_item() -> None:
    """Stop requests should finish the current item and skip the remaining queue."""
    messages: list[str] = []
    active_names: list[str | None] = []
    completed_names: list[str] = []
    stop_requested = False

    def run_one(name: str) -> BenchmarkResult:
        nonlocal stop_requested
        completed_names.append(name)
        stop_requested = True
        return BenchmarkResult(
            instance_name=name,
            timestamp="2026-05-19T12:00:00+0000",
            config_hash="cfg",
            prompt_file="default.txt",
            prompt_sha256="sha",
            prompt_chars=10,
            output_tokens=20,
            tokens_per_second=72.1,
            latency_ms=364.0,
            elapsed_ms=1400.0,
            vram_mb=1024.0,
            status="ok",
        )

    result = run_serial_benchmark_queue(
        ("alpha", "beta"),
        should_stop=lambda: stop_requested,
        set_active_name=active_names.append,
        start_one=lambda _name: True,
        run_one=run_one,
        stop_one=lambda _name: None,
        handle_exception=lambda _name, _exc: None,
        post_message=messages.append,
    )

    assert result == "[Serial benchmark] stopped after 1/2 completed."
    assert completed_names == ["alpha"]
    assert messages == [
        "[Serial benchmark] 1/2 starting: alpha",
        "[Serial benchmark] 1/2 started: alpha",
        "[Serial benchmark] 1/2 running: alpha",
        "[Serial benchmark] 1/2 completed: alpha: TPS=72.1, latency=364 ms",
        "[Serial benchmark] 1/2 stopping: alpha",
        "[Serial benchmark] 1/2 stopped: alpha",
    ]
    assert active_names == ["alpha", None]


def test_run_serial_benchmark_queue_keeps_preexisting_running_instance_up() -> None:
    """Rows already running before the queue should not be stopped by cleanup."""
    messages: list[str] = []
    stopped: list[str] = []

    result = run_serial_benchmark_queue(
        ("alpha",),
        should_stop=lambda: False,
        set_active_name=lambda _name: None,
        start_one=lambda _name: False,
        run_one=lambda name: BenchmarkResult(
            instance_name=name,
            timestamp="2026-05-19T12:00:00+0000",
            config_hash="cfg",
            prompt_file="default.txt",
            prompt_sha256="sha",
            prompt_chars=10,
            output_tokens=20,
            tokens_per_second=72.1,
            latency_ms=364.0,
            elapsed_ms=1400.0,
            vram_mb=1024.0,
            status="ok",
        ),
        stop_one=stopped.append,
        handle_exception=lambda _name, _exc: None,
        post_message=messages.append,
    )

    assert result == "[Serial benchmark] finished 1/1."
    assert stopped == []
    assert messages == [
        "[Serial benchmark] 1/1 starting: alpha",
        "[Serial benchmark] 1/1 already running: alpha",
        "[Serial benchmark] 1/1 running: alpha",
        "[Serial benchmark] 1/1 completed: alpha: TPS=72.1, latency=364 ms",
    ]


def test_format_model_size_gb_uses_base_1024_display_units() -> None:
    """Model sizes should render in compact GB values for the table."""
    assert format_model_size_gb(7.625) == "7.6 GB"
    assert format_model_size_gb(None) == "-"


def test_format_cpu_indicator_uses_checkmark_only_when_cpu_is_active() -> None:
    """CPU-only rows should show a compact checkmark indicator."""
    assert format_cpu_indicator(True) == CPU_ACTIVE_GLYPH
    assert format_cpu_indicator(False) == ""


def test_format_detected_gpu_summary_lists_each_device_on_its_own_line() -> None:
    """The GPU summary panel should remain readable and tolerate missing names."""
    summary = format_detected_gpu_summary(
        [
            DetectedGpu(label="Vulkan0", name="AMD Radeon(TM) Graphics"),
            DetectedGpu(label="Vulkan1", name=None),
        ]
    )

    assert summary == (
        "Vulkan0 - AMD Radeon(TM) Graphics\n"
        "Vulkan1 - adapter name unavailable"
    )


def test_format_detected_gpu_summary_shows_alias_next_to_adapter_name() -> None:
    """Detected GPU rows should expose aliases without replacing adapter names."""
    summary = format_detected_gpu_summary(
        [
            DetectedGpu(label="Vulkan0", name="AMD Radeon(TM) Graphics"),
            DetectedGpu(label="Vulkan1", name="AMD Radeon RX 6800"),
        ],
        {"AMD Radeon RX 6800": "RX6800"},
    )

    assert summary == (
        "Vulkan0 - AMD Radeon(TM) Graphics\n"
        "Vulkan1 [RX6800] - AMD Radeon RX 6800"
    )


def test_format_runtime_gpu_display_uses_alias_by_adapter_name_not_vulkan_label() -> None:
    """Aliases should follow adapter names even when Vulkan labels are reordered."""
    aliases = {
        "AMD Radeon(TM) Graphics": "iGPU",
        "AMD Radeon RX 6800": "RX6800",
    }
    before_reboot = [
        DetectedGpu(label="Vulkan0", name="AMD Radeon(TM) Graphics"),
        DetectedGpu(label="Vulkan1", name="AMD Radeon RX 6800"),
    ]
    after_reboot = [
        DetectedGpu(label="Vulkan0", name="AMD Radeon RX 6800"),
        DetectedGpu(label="Vulkan1", name="AMD Radeon(TM) Graphics"),
    ]

    assert format_runtime_gpu_display(("Vulkan1",), before_reboot, aliases) == "RX6800"
    assert format_runtime_gpu_display(("Vulkan0",), after_reboot, aliases) == "RX6800"
    assert format_runtime_gpu_display(("Vulkan1",), after_reboot, aliases) == "iGPU"


def test_format_runtime_gpu_display_falls_back_to_vulkan_label_without_alias() -> None:
    """Rows should remain readable when the adapter name or alias is unavailable."""
    assert format_runtime_gpu_display(
        ("Vulkan0", "Vulkan1"),
        [DetectedGpu(label="Vulkan0", name="AMD Radeon RX 6800")],
        {},
    ) == "Vulkan0, Vulkan1"


def test_gpu_alias_persistence_clamps_to_valid_entries(monkeypatch, tmp_path: Path) -> None:
    """Persisted aliases are keyed by adapter names and ignore blank values."""
    monkeypatch.setattr("llama_orchestrator.gui.get_state_dir", lambda: tmp_path)

    saved_path = save_gpu_aliases(
        {
            "AMD Radeon RX 6800": "RX6800",
            "AMD Radeon(TM) Graphics": "",
        }
    )

    assert saved_path == tmp_path / "gpu_aliases.json"
    assert load_gpu_aliases() == {"AMD Radeon RX 6800": "RX6800"}


def test_normalize_gpu_alias_enforces_ten_character_limit() -> None:
    """Alias values should fit the fixed-width GUI alias button."""
    assert normalize_gpu_alias("  RX 6800  ") == "RX 6800"

    try:
        normalize_gpu_alias("12345678901")
    except ValueError as exc:
        assert "10 characters" in str(exc)
    else:
        raise AssertionError("Expected ValueError for a too-long GPU alias")


def test_memory_column_width_matches_expanded_display_text() -> None:
    """The memory column should be wide enough for total/shared benchmark strings."""
    assert COLUMN_WIDTHS["vram"] >= 220


def test_normalize_model_path_for_config_prefers_project_relative_paths(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "llama-orchestrator"
    model_path = project_root / "models" / "demo.gguf"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"gguf")
    monkeypatch.setattr("llama_orchestrator.gui.get_project_root", lambda: project_root)

    assert normalize_model_path_for_config(model_path) == Path("models/demo.gguf")


def test_resolve_instance_config_path_prefers_loaded_source_path(tmp_path: Path) -> None:
    project_root = tmp_path / "llama-orchestrator"
    source_path = project_root / "instances" / "legacy-demo" / "config.json"
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("models/demo.gguf")))
    config.set_source_path(source_path)

    assert resolve_instance_config_path(config, project_root) == source_path
    assert resolve_instance_config_dir(config, project_root) == source_path.parent


def test_resolve_instance_config_path_falls_back_to_immutable_directory_name(tmp_path: Path) -> None:
    project_root = tmp_path / "llama-orchestrator"
    config = InstanceConfig(
        name="demo",
        instance_uid="123e4567-e89b-42d3-a456-426614174000",
        instance_no="00000042",
        model=ModelConfig(path=Path("models/demo.gguf")),
    )

    assert resolve_instance_config_path(config, project_root) == (
        project_root / "instances" / "00000042_123e4567-e89b-42d3-a456-426614174000" / "config.json"
    )


def test_instance_alias_exists_uses_discovered_aliases() -> None:
    with patch(
        "llama_orchestrator.gui.discover_instances",
        return_value=(("alpha", Path("instances/a/config.json")), ("beta", Path("instances/b/config.json"))),
    ):
        assert instance_alias_exists("beta") is True
        assert instance_alias_exists("gamma") is False


def test_update_instance_display_name_preserves_source_path() -> None:
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("models/demo.gguf")))
    config.set_source_path(Path("instances/legacy-demo/config.json"))

    with patch("llama_orchestrator.gui.get_instance_config", return_value=config), \
         patch("llama_orchestrator.gui.save_config") as mock_save_config:
        updated = update_instance_display_name("demo", "Demo Label")

    saved = mock_save_config.call_args.args[0]
    assert updated.display_name == "Demo Label"
    assert saved.display_name == "Demo Label"
    assert saved.source_path == Path("instances/legacy-demo/config.json")


def test_format_download_progress_reports_downloaded_and_total_bytes() -> None:
    progress = DownloadProgress(
        filename="Qwen3-8B-Q4_K_M.gguf",
        downloaded_bytes=2 * 1024**3,
        total_bytes=8 * 1024**3,
    )

    assert format_download_progress(progress) == (
        "Downloading Qwen3-8B-Q4_K_M.gguf: 2.00 GB / 8.00 GB"
    )


def test_resolve_models_directory_input_anchors_relative_paths_to_project_root(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "llama-orchestrator"
    monkeypatch.setattr("llama_orchestrator.gui.get_project_root", lambda: project_root)

    assert resolve_models_directory_input("models-alt") == project_root / "models-alt"
    assert resolve_models_directory_input("") == project_root / "models"
