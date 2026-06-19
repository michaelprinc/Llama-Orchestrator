"""llama-orchestrator GUI package.

The package entrypoint re-exports the public API from the extracted modules
and the main ``app.py`` coordinator.  The legacy ``gui.py`` module has been
removed as part of the extraction workstream.
"""

from __future__ import annotations

# --- Main GUI classes ---
# --- Utility functions ---
from llama_orchestrator.gui.app import (  # noqa: F401
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
    RUNNING_BENCHMARK_ROW_TAG,
    VULKAN_BINARY_MISSING_MESSAGE,
    ExistingModelFileDialog,
    LlamaOrchestratorGui,
    apply_managed_runtime_args,
    benchmark_shared_ram_warning,
    derive_display_status_and_health,
    format_benchmark_memory,
    format_benchmark_message,
    format_benchmark_settings_summary,
    format_cpu_indicator,
    format_detected_gpu_summary,
    format_download_bytes,
    format_download_progress,
    format_metric,
    format_model_size_gb,
    format_queue_checkbox,
    format_runtime_gpu_display,
    format_serial_benchmark_progress,
    get_gpu_aliases_path,
    gpu_alias_for_label,
    instance_alias_exists,
    launch_gui,
    load_gpu_aliases,
    normalize_config_token,
    normalize_gpu_alias,
    normalize_model_path_for_config,
    ordered_visible_names,
    parse_tag_string,
    persist_instance_health,
    resolve_instance_config_dir,
    resolve_instance_config_path,
    resolve_models_directory_input,
    run_serial_benchmark_queue,
    save_gpu_aliases,
    suggest_add_model_port,
    suggest_next_add_model_port,
    unique_instance_name,
    update_instance_display_name,
)

# --- Data classes ---
from llama_orchestrator.gui.dataclasses import (  # noqa: F401
    GuiRefreshSnapshot,
    ImportDialogEvent,
    TableRow,
)

# --- Dialog classes ---
from llama_orchestrator.gui.dialogs import (  # noqa: F401
    ExistingModelFileDialog as _ExistingModelFileDialog,
)
from llama_orchestrator.gui.grid_benchmark_dialog import GridBenchmarkDialog  # noqa: F401
from llama_orchestrator.gui.grid_dialogs import (  # noqa: F401
    GridDialogParameterVars,
    format_kv_cache_profile_summary,
    parse_grid_number,
    parse_grid_values,
)
from llama_orchestrator.gui.hf_import_dialog import HuggingFaceImportDialog  # noqa: F401
from llama_orchestrator.gui.install_dialog import InstallBinaryDialog  # noqa: F401
from llama_orchestrator.gui.kv_cache_dialogs import KvCacheProfileDialog  # noqa: F401
from llama_orchestrator.gui.model_dialogs import AddModelDialog  # noqa: F401

# --- Refresh controller ---
from llama_orchestrator.gui.refresh import (  # noqa: F401
    RefreshController,
    RenderDiffMixin,
)

# --- Usability helpers ---
from llama_orchestrator.gui.usability import (  # noqa: F401
    SHORTCUT_REGISTRY,
    configure_status_tags,
    create_progress_bar,
    register_shortcuts,
)

__all__ = sorted(
    [
        # Main GUI
        "BENCHMARK_PARAMS_MENU_LABEL",
        "COLUMN_HEADINGS",
        "COLUMN_WIDTHS",
        "CPU_ACTIVE_GLYPH",
        "DEFAULT_RUNTIME_ARGS",
        "EDIT_BENCHMARK_PROMPT_LABEL",
        "GRID_BENCHMARK_LABEL",
        "INSTALL_LLAMA_SERVER_LABEL",
        "QUEUE_CHECKED_GLYPH",
        "QUEUE_UNCHECKED_GLYPH",
        "RUNNING_BENCHMARK_ROW_TAG",
        "VULKAN_BINARY_MISSING_MESSAGE",
        "ExistingModelFileDialog",
        "LlamaOrchestratorGui",
        "launch_gui",
        # Data classes
        "GuiRefreshSnapshot",
        "ImportDialogEvent",
        "TableRow",
        # Refresh
        "RefreshController",
        "RenderDiffMixin",
        # Usability
        "SHORTCUT_REGISTRY",
        "configure_status_tags",
        "create_progress_bar",
        "register_shortcuts",
        # Dialogs
        "AddModelDialog",
        "GridBenchmarkDialog",
        "GridDialogParameterVars",
        "HuggingFaceImportDialog",
        "InstallBinaryDialog",
        "KvCacheProfileDialog",
        "format_kv_cache_profile_summary",
        "parse_grid_number",
        "parse_grid_values",
        # Utilities
        "apply_managed_runtime_args",
        "benchmark_shared_ram_warning",
        "derive_display_status_and_health",
        "format_benchmark_memory",
        "format_benchmark_message",
        "format_benchmark_settings_summary",
        "format_cpu_indicator",
        "format_detected_gpu_summary",
        "format_download_bytes",
        "format_download_progress",
        "format_metric",
        "format_model_size_gb",
        "format_queue_checkbox",
        "format_runtime_gpu_display",
        "format_serial_benchmark_progress",
        "get_gpu_aliases_path",
        "gpu_alias_for_label",
        "instance_alias_exists",
        "load_gpu_aliases",
        "normalize_config_token",
        "normalize_gpu_alias",
        "normalize_model_path_for_config",
        "ordered_visible_names",
        "parse_tag_string",
        "persist_instance_health",
        "resolve_instance_config_dir",
        "resolve_instance_config_path",
        "resolve_models_directory_input",
        "run_serial_benchmark_queue",
        "save_gpu_aliases",
        "suggest_add_model_port",
        "suggest_next_add_model_port",
        "unique_instance_name",
        "update_instance_display_name",
    ]
)
