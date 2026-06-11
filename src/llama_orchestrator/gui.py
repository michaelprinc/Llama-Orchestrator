"""
Tkinter GUI for llama-orchestrator model instance management.

The GUI intentionally uses only the Python standard library so the desktop
management surface works on Windows without adding runtime dependencies.
"""

from __future__ import annotations

import difflib
import json
import os
import queue
import shlex
import threading
import time
import tkinter as tk
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from llama_orchestrator.benchmark import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    BenchmarkResult,
    BenchmarkSettings,
    latest_benchmark_results,
    load_benchmark_settings,
    quick_benchmark_instance,
    save_benchmark_settings,
)
from llama_orchestrator.benchmark_grid import (
    DEFAULT_KV_CACHE_PROFILE_IDS,
    KV_CACHE_PARAMETER_NAME,
    GridParameterRange,
    GridParameterSpec,
    GridPlan,
    all_kv_cache_profiles,
    format_grid_plan_preview,
    grid_parameter_catalog,
    kv_cache_profiles_for_preset,
    latest_grid_runs,
    load_grid_plan,
    plan_requires_restart,
    run_grid_for_instance,
    save_grid_plan,
    write_grid_summary_artifact,
)
from llama_orchestrator.config import (
    BinaryConfig,
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
    discover_instances,
    get_instance_config,
    get_project_root,
    get_state_dir,
    load_all_instances,
    save_config,
)
from llama_orchestrator.daemon import get_daemon_status, start_daemon, stop_daemon
from llama_orchestrator.engine import (
    InstanceStatus,
    build_command,
    format_command,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from llama_orchestrator.engine.detection import (
    DetectedGpu,
    collect_detected_gpu_inventory,
    describe_effective_runtime,
    resolve_model_size_gb,
)
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    RuntimeState,
    load_runtime,
    record_health_check,
    save_runtime,
    save_state,
)
from llama_orchestrator.gui_state import (
    GuiSettings,
    cycle_sort_order,
    format_sort_heading,
    load_gui_settings,
    save_gui_settings,
    stable_sort_rows,
)
from llama_orchestrator.health import check_instance_health
from llama_orchestrator.health.ports import suggest_port_for_instance
from llama_orchestrator.hf_import import (
    DownloadCancelledError,
    DownloadProgress,
    GGUFVariant,
    HuggingFaceImportError,
    HuggingFaceRepoRef,
    HuggingFaceTokenStore,
    ImportedModelSelection,
    ImportSettings,
    build_add_model_prefill,
    download_gguf_variant,
    list_gguf_variants,
    load_import_settings,
    normalize_hf_model_reference,
    parse_gguf_quantization,
    plan_download_target,
    save_import_settings,
    write_import_metadata_sidecar,
)
from llama_orchestrator.model_metadata import build_model_metadata

# Optional timing instrumentation for performance profiling.
# Enable by setting LLAMA_ORCH_DEBUG_GUI_TIMING=1 in the environment.
_GUI_TIMING_ENABLED: bool = os.environ.get("LLAMA_ORCH_DEBUG_GUI_TIMING", "0") == "1"

DEFAULT_RUNTIME_ARGS = ["--no-mmproj", "--reasoning", "off", "--flash-attn", "auto"]
MANAGED_VALUE_ARGS = {"--reasoning", "--flash-attn"}
MANAGED_FLAG_ARGS = {"--no-mmproj"}
VULKAN_VARIANT = "win-vulkan-x64"
INSTALL_LLAMA_SERVER_LABEL = "Install llama-server"
EDIT_BENCHMARK_PROMPT_LABEL = "Edit Benchmark Prompt"
BENCHMARK_PARAMS_MENU_LABEL = "Params"
GRID_BENCHMARK_LABEL = "Grid benchmark"
VULKAN_BINARY_MISSING_MESSAGE = (
    "No win-vulkan-x64 llama-server binary is installed. Use Install llama-server "
    "to download the default variant, or choose another llama-server variant."
)
CPU_ACTIVE_GLYPH = "\u2713"
QUEUE_UNCHECKED_GLYPH = "\u2610"
QUEUE_CHECKED_GLYPH = "\u2611"
RUNNING_BENCHMARK_ROW_TAG = "running_benchmark"
GPU_ALIAS_MAX_LENGTH = 10
GPU_ALIAS_BUTTON_WIDTH = GPU_ALIAS_MAX_LENGTH

ALL_COLUMNS = (
    "queue",
    "name",
    "status",
    "health",
    "pid",
    "port",
    "backend",
    "gpu",
    "cpu",
    "tags",
    "tps",
    "latency",
    "vram",
    "prompt",
    "model_size",
    "quantization",
    "architecture",
    "model",
    "args",
    "uptime",
)
COLUMN_HEADINGS = {
    "queue": "Queue",
    "name": "Name",
    "status": "Status",
    "health": "Health",
    "pid": "PID",
    "port": "Port",
    "backend": "Backend",
    "gpu": "GPU",
    "cpu": "CPU",
    "tags": "Tags",
    "tps": "TPS",
    "latency": "Latency ms",
    "vram": "Memory MB",
    "prompt": "Prompt file",
    "model_size": "Model size",
    "quantization": "Quant",
    "architecture": "Arch",
    "model": "Model",
    "args": "Runtime args",
    "uptime": "Uptime",
}
COLUMN_WIDTHS = {
    "queue": 60,
    "name": 150,
    "status": 90,
    "health": 110,
    "pid": 70,
    "port": 70,
    "backend": 90,
    "gpu": 110,
    "cpu": 60,
    "tags": 150,
    "tps": 80,
    "latency": 90,
    "vram": 260,
    "prompt": 140,
    "model_size": 90,
    "quantization": 100,
    "architecture": 90,
    "model": 280,
    "args": 260,
    "uptime": 90,
}


@dataclass(frozen=True)
class TableRow:
    """One rendered GUI table row plus raw sort values."""

    name: str
    values: tuple[str, ...]
    sort_values: dict[str, object]


@dataclass(frozen=True)
class ImportDialogEvent:
    """One worker event consumed by the Hugging Face import dialog."""

    kind: str
    message: str = ""
    repo_ref: HuggingFaceRepoRef | None = None
    variants: list[GGUFVariant] | None = None
    progress: DownloadProgress | None = None
    selection: ImportedModelSelection | None = None


@dataclass(frozen=True)
class GuiRefreshSnapshot:
    """Immutable snapshot of data collected during a full refresh."""

    rows: tuple[TableRow, ...]
    detected_gpus: tuple[DetectedGpu, ...]
    all_tags: tuple[str, ...]
    collected_at: float


@dataclass(frozen=True)
class GridDialogParameterVars:
    """Tk variables backing one Grid benchmark dialog row."""

    spec: GridParameterSpec
    enabled: tk.BooleanVar
    minimum: tk.StringVar
    maximum: tk.StringVar
    step_or_values: tk.StringVar


def format_kv_cache_profile_summary(profile_ids: Sequence[str]) -> str:
    """Return the compact main-grid label for selected KV cache profiles."""
    count = len(tuple(profile_ids))
    if count == 0:
        return "Custom: 0 selected"
    if tuple(profile_ids) == DEFAULT_KV_CACHE_PROFILE_IDS:
        return f"Paired profiles: {count} selected"
    return f"Custom: {count} selected"


def parse_grid_values(text: str, value_type: str) -> tuple[int | float | str | bool, ...]:
    """Parse comma-separated grid values from the dialog."""
    raw_values = [part.strip() for part in text.split(",") if part.strip()]
    if not raw_values:
        raise ValueError("Enter at least one value.")
    parsed: list[int | float | str | bool] = []
    for value in raw_values:
        if value_type == "int":
            parsed.append(int(value))
        elif value_type == "float":
            parsed.append(float(value))
        elif value_type == "bool":
            normalized = value.lower()
            if normalized in {"1", "true", "yes", "on"}:
                parsed.append(True)
            elif normalized in {"0", "false", "no", "off"}:
                parsed.append(False)
            else:
                raise ValueError(f"Invalid boolean value: {value}")
        else:
            parsed.append(value)
    return tuple(parsed)


def parse_grid_number(text: str, value_type: str) -> int | float | None:
    """Parse an optional numeric grid bound."""
    value = text.strip()
    if not value:
        return None
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    raise ValueError(f"{value_type} is not a numeric grid type.")


def _default_grid_values_for_spec(value: int | float | str | bool | None) -> str:
    if isinstance(value, bool):
        return "false,true" if value is False else "true,false"
    if isinstance(value, float):
        return f"{value:g}"
    if value is None:
        return ""
    return str(value)


def _default_grid_bound(value: int | float | str | bool | None) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return ""


def _default_step_or_values(spec: GridParameterSpec) -> str:
    if spec.value_type == "int":
        return "1"
    if spec.value_type == "float":
        return "0.1"
    if spec.choices:
        return ",".join(str(choice).lower() if isinstance(choice, bool) else str(choice) for choice in spec.choices)
    return _default_grid_values_for_spec(spec.default)


def _format_grid_values(values: Sequence[int | float | str | bool]) -> str:
    return ",".join(str(value).lower() if isinstance(value, bool) else str(value) for value in values)


def _grid_dialog_status(spec: GridParameterSpec) -> str:
    if spec.read_only:
        return "read-only"
    if not spec.execution_supported:
        return "disabled"
    return "ready"


def _grid_spec_label(spec: GridParameterSpec) -> str:
    return spec.display_name or spec.name


class KvCacheProfileDialog(tk.Toplevel):
    """Dialog for exact KV cache benchmark profile selection."""

    def __init__(self, parent: tk.Misc, selected_profile_ids: Sequence[str]) -> None:
        super().__init__(parent)
        self.title("KV Cache combinations")
        self.resizable(True, True)
        self.result: tuple[str, ...] | None = None
        selected = set(selected_profile_ids) or set(DEFAULT_KV_CACHE_PROFILE_IDS)
        self._profile_vars: dict[str, tk.BooleanVar] = {}
        self._profiles = all_kv_cache_profiles()
        self._mode = tk.StringVar(value="paired")

        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        mode_frame = ttk.LabelFrame(body, text="Mode")
        mode_frame.grid(row=0, column=0, sticky="ew")
        modes = (
            ("Paired profiles", "paired"),
            ("Matrix selection", "matrix"),
            ("Exact custom list", "custom"),
            ("Full factorial K x V", "full"),
        )
        for index, (label, value) in enumerate(modes):
            ttk.Radiobutton(
                mode_frame,
                text=label,
                value=value,
                variable=self._mode,
                command=self._apply_mode,
            ).grid(row=0, column=index, sticky="w", padx=4)

        preset_frame = ttk.Frame(body)
        preset_frame.grid(row=1, column=0, sticky="w", pady=(8, 6))
        ttk.Button(preset_frame, text="Baseline", command=lambda: self._select_preset("baseline")).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Paired", command=lambda: self._select_preset("paired")).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Memory saving", command=lambda: self._select_preset("memory")).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Add asymmetric", command=lambda: self._select_preset("asymmetric")).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Full K x V", command=lambda: self._select_preset("full")).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Clear", command=self._clear_all).pack(side=tk.LEFT, padx=2)

        table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(2, weight=1)
        headers = ("Enabled", "Name", "K cache", "V cache", "Draft K", "Draft V", "Notes")
        for column, header in enumerate(headers):
            ttk.Label(table, text=header).grid(row=0, column=column, sticky="w", padx=4)

        for row, profile in enumerate(self._profiles, start=1):
            variable = tk.BooleanVar(value=profile.id in selected)
            self._profile_vars[profile.id] = variable
            ttk.Checkbutton(table, variable=variable, command=self._update_summary).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Label(table, text=profile.label).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_k).grid(row=row, column=2, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_v).grid(row=row, column=3, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_k_draft or "-").grid(row=row, column=4, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_v_draft or "-").grid(row=row, column=5, sticky="w", padx=4)
            ttk.Label(table, text=profile.notes or "-").grid(row=row, column=6, sticky="w", padx=4)

        self.summary_var = tk.StringVar()
        ttk.Label(body, textvariable=self.summary_var).grid(row=3, column=0, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Apply", command=self._accept).pack(side=tk.LEFT, padx=4)

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._update_summary()

    def _selected_profile_ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self._profiles if self._profile_vars[profile.id].get())

    def _select_preset(self, preset: str) -> None:
        selected = {profile.id for profile in kv_cache_profiles_for_preset(preset)}
        for profile_id, variable in self._profile_vars.items():
            variable.set(profile_id in selected)
        self._mode.set("full" if preset == "full" else "paired" if preset == "paired" else "custom")
        self._update_summary()

    def _apply_mode(self) -> None:
        if self._mode.get() == "paired":
            self._select_preset("paired")
        elif self._mode.get() == "full":
            self._select_preset("full")
        else:
            self._update_summary()

    def _clear_all(self) -> None:
        for variable in self._profile_vars.values():
            variable.set(False)
        self._mode.set("custom")
        self._update_summary()

    def _update_summary(self) -> None:
        selected = self._selected_profile_ids()
        self.summary_var.set(
            f"Selected KV profiles: {len(selected)} | Estimated total benchmark runs: {len(selected)} x other enabled dimensions"
        )

    def _accept(self) -> None:
        selected = self._selected_profile_ids()
        if not selected:
            messagebox.showerror("KV Cache combinations", "Select at least one KV cache profile.", parent=self)
            return
        self.result = selected
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class GridBenchmarkDialog(tk.Toplevel):
    """Grid benchmark parameter dialog."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: BenchmarkSettings,
        config: InstanceConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(GRID_BENCHMARK_LABEL)
        self.resizable(True, True)
        self.result: GridPlan | None = None
        self._parameter_vars: dict[str, GridDialogParameterVars] = {}
        saved_ranges = {parameter.name: parameter for parameter in load_grid_plan().parameters}
        has_saved_ranges = bool(saved_ranges)

        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(4, weight=1)

        headers = (
            "Enabled",
            "Parameter",
            "Current/default",
            "Minimum",
            "Maximum",
            "Step / values",
            "Category",
            "Restart",
            "Status",
        )
        for column, header in enumerate(headers):
            ttk.Label(body, text=header).grid(row=0, column=column, sticky="w", padx=4)

        for row, spec in enumerate(grid_parameter_catalog(config, settings), start=1):
            saved = saved_ranges.get(spec.name)
            enabled = tk.BooleanVar(
                value=(
                    bool(saved.enabled)
                    if saved
                    else spec.name == KV_CACHE_PARAMETER_NAME and not has_saved_ranges
                )
            )
            if spec.read_only or not spec.execution_supported:
                enabled.set(False)
            min_value = tk.StringVar(
                value=(
                    str(saved.minimum)
                    if saved is not None and saved.minimum is not None
                    else _default_grid_bound(spec.default)
                )
            )
            max_value = tk.StringVar(
                value=(
                    str(saved.maximum)
                    if saved is not None and saved.maximum is not None
                    else _default_grid_bound(spec.default)
                )
            )
            step_or_values = tk.StringVar(
                value=(
                    _format_grid_values(saved.values)
                    if saved is not None and saved.values
                    else (
                        str(saved.step)
                        if saved is not None and saved.step is not None
                        else _default_step_or_values(spec)
                    )
                )
            )
            if spec.name == KV_CACHE_PARAMETER_NAME and saved is None:
                step_or_values.set(_format_grid_values(DEFAULT_KV_CACHE_PROFILE_IDS))
            self._parameter_vars[spec.name] = GridDialogParameterVars(
                spec=spec,
                enabled=enabled,
                minimum=min_value,
                maximum=max_value,
                step_or_values=step_or_values,
            )
            ttk.Checkbutton(
                body,
                variable=enabled,
                state=tk.DISABLED if spec.read_only or not spec.execution_supported else tk.NORMAL,
            ).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Label(body, text=_grid_spec_label(spec)).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(body, text=_default_grid_values_for_spec(spec.default) or "-").grid(
                row=row,
                column=2,
                sticky="w",
                padx=4,
            )
            numeric_state = tk.NORMAL if spec.value_type in {"int", "float"} and not spec.read_only else tk.DISABLED
            ttk.Entry(body, textvariable=min_value, width=10, state=numeric_state).grid(
                row=row, column=3, sticky="ew", padx=4
            )
            ttk.Entry(body, textvariable=max_value, width=10, state=numeric_state).grid(
                row=row, column=4, sticky="ew", padx=4
            )
            if spec.kind == "composite" and spec.name == KV_CACHE_PARAMETER_NAME:
                selected_profile_ids = tuple(str(value) for value in parse_grid_values(step_or_values.get(), "enum"))
                step_or_values._kv_cache_profile_ids = selected_profile_ids
                composite_frame = ttk.Frame(body)
                composite_frame.grid(row=row, column=5, sticky="ew", padx=4)
                composite_frame.columnconfigure(0, weight=1)
                ttk.Entry(
                    composite_frame,
                    textvariable=step_or_values,
                    width=24,
                    state=tk.DISABLED,
                ).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    composite_frame,
                    text="Configure...",
                    command=lambda target=step_or_values: self._configure_kv_cache(target),
                ).grid(row=0, column=1, sticky="e", padx=(4, 0))
                step_or_values.set(format_kv_cache_profile_summary(selected_profile_ids))
            else:
                ttk.Entry(
                    body,
                    textvariable=step_or_values,
                    width=24,
                    state=tk.NORMAL if not spec.read_only and spec.execution_supported else tk.DISABLED,
                ).grid(row=row, column=5, sticky="ew", padx=4)
            ttk.Label(body, text=spec.category).grid(row=row, column=6, sticky="w", padx=4)
            ttk.Label(body, text="yes" if spec.restart_required else "no").grid(
                row=row,
                column=7,
                sticky="w",
                padx=4,
            )
            ttk.Label(body, text=_grid_dialog_status(spec)).grid(row=row, column=8, sticky="w", padx=4)

        self.preview_var = tk.StringVar(value="Combinations: 1")
        ttk.Label(body, textvariable=self.preview_var).grid(
            row=len(self._parameter_vars) + 1,
            column=0,
            columnspan=9,
            sticky="w",
            pady=(10, 0),
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=len(self._parameter_vars) + 2, column=0, columnspan=9, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Preview", command=self._update_preview).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Save config", command=self._save_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Start", command=self._accept).pack(side=tk.LEFT, padx=4)

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._update_preview()

    def _build_plan(self) -> GridPlan:
        ranges: list[GridParameterRange] = []
        for name, vars_ in self._parameter_vars.items():
            if not vars_.enabled.get():
                continue
            if vars_.spec.read_only:
                raise ValueError(f"{name} is read-only metadata and cannot be benchmarked.")
            if not vars_.spec.execution_supported:
                raise ValueError(f"{name} is disabled for this model/runtime.")
            if vars_.spec.kind == "composite" and name == KV_CACHE_PARAMETER_NAME:
                ranges.append(
                    GridParameterRange(
                        name=name,
                        values=self._selected_kv_cache_profile_ids(vars_.step_or_values.get()),
                    )
                )
                continue
            if vars_.spec.value_type in {"int", "float"}:
                minimum = parse_grid_number(vars_.minimum.get(), vars_.spec.value_type)
                maximum = parse_grid_number(vars_.maximum.get(), vars_.spec.value_type)
                step = parse_grid_number(vars_.step_or_values.get(), vars_.spec.value_type)
                ranges.append(
                    GridParameterRange(
                        name=name,
                        minimum=minimum,
                        maximum=maximum,
                        step=step,
                    )
                )
                continue
            ranges.append(
                GridParameterRange(
                    name=name,
                    values=parse_grid_values(vars_.step_or_values.get(), vars_.spec.value_type),
                )
            )
        return GridPlan(parameters=tuple(ranges))

    def _update_preview(self) -> None:
        try:
            plan = self._build_plan()
            suffix = " confirmation required" if plan.needs_confirmation() else ""
            self.preview_var.set(f"{format_grid_plan_preview(plan)}{suffix}")
        except Exception as exc:
            self.preview_var.set(f"Invalid grid: {exc}")

    def _selected_kv_cache_profile_ids(self, text: str) -> tuple[str, ...]:
        if "selected" in text:
            vars_ = self._parameter_vars[KV_CACHE_PARAMETER_NAME]
            stored = getattr(vars_.step_or_values, "_kv_cache_profile_ids", None)
            if stored:
                return tuple(stored)
        values = parse_grid_values(text, "enum")
        return tuple(str(value) for value in values)

    def _configure_kv_cache(self, target: tk.StringVar) -> None:
        selected = self._selected_kv_cache_profile_ids(target.get())
        dialog = KvCacheProfileDialog(self, selected)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        target._kv_cache_profile_ids = tuple(dialog.result)
        target.set(format_kv_cache_profile_summary(dialog.result))
        self._parameter_vars[KV_CACHE_PARAMETER_NAME].enabled.set(True)
        self._update_preview()

    def _accept(self) -> None:
        try:
            plan = self._build_plan()
            count = plan.combination_count()
        except Exception as exc:
            messagebox.showerror(GRID_BENCHMARK_LABEL, str(exc), parent=self)
            return
        if plan.needs_confirmation() and not messagebox.askyesno(
            GRID_BENCHMARK_LABEL,
            f"Run {count} combinations?",
            parent=self,
        ):
            return
        self.result = plan
        self.destroy()

    def _save_config(self) -> None:
        try:
            path = save_grid_plan(self._build_plan())
        except Exception as exc:
            messagebox.showerror(GRID_BENCHMARK_LABEL, str(exc), parent=self)
            return
        self.preview_var.set(f"Saved: {path.name}")

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def format_queue_checkbox(checked: bool) -> str:
    """Render the queue selection column as a checkbox glyph."""
    return QUEUE_CHECKED_GLYPH if checked else QUEUE_UNCHECKED_GLYPH


def ordered_visible_names(selected_names: set[str] | frozenset[str], visible_names: Sequence[str]) -> tuple[str, ...]:
    """Keep only selected names, preserving the current visible table order."""
    return tuple(name for name in visible_names if name in selected_names)


def resolve_instance_config_path(config: InstanceConfig, project_root: Path) -> Path:
    """Resolve the on-disk config path for an instance regardless of directory layout."""
    if config.source_path is not None:
        return config.source_path
    return project_root / "instances" / config.instance_dir_name / "config.json"


def resolve_instance_config_dir(config: InstanceConfig, project_root: Path) -> Path:
    """Resolve the folder containing an instance config."""
    return resolve_instance_config_path(config, project_root).parent


def instance_alias_exists(name: str) -> bool:
    """Check whether an immutable instance alias is already present."""
    return any(existing_name == name for existing_name, _ in discover_instances())


def update_instance_display_name(name: str, display_name: str) -> InstanceConfig:
    """Persist a new display name without changing the immutable instance alias."""
    normalized = display_name.strip()
    if not normalized:
        raise ValueError("Display name cannot be blank.")

    config = get_instance_config(name)
    updated = config.model_copy(update={"display_name": normalized})
    if config.source_path is not None:
        updated.set_source_path(config.source_path)
    save_config(updated)
    return updated


def format_serial_benchmark_progress(result: BenchmarkResult) -> str:
    """Summarize a serial benchmark result for the activity log."""
    if result.status != "ok":
        return result.error or "unknown error"
    return (
        f"TPS={format_metric(result.tokens_per_second)}, "
        f"latency={format_metric(result.latency_ms, 0)} ms"
    )


def run_serial_benchmark_queue(
    names: Sequence[str],
    *,
    should_stop: Callable[[], bool],
    set_active_name: Callable[[str | None], None],
    start_one: Callable[[str], bool],
    run_one: Callable[[str], BenchmarkResult],
    stop_one: Callable[[str], None],
    handle_exception: Callable[[str, Exception], None],
    post_message: Callable[[str], None],
) -> str:
    """Run benchmarked rows sequentially while reporting queue progress."""
    completed = 0
    total = len(names)
    for index, name in enumerate(names, start=1):
        if should_stop():
            return f"[Serial benchmark] stopped after {completed}/{total} completed."

        set_active_name(name)
        should_stop_after_run = False
        post_message(f"[Serial benchmark] {index}/{total} starting: {name}")
        try:
            should_stop_after_run = start_one(name)
            if should_stop_after_run:
                post_message(f"[Serial benchmark] {index}/{total} started: {name}")
            else:
                post_message(f"[Serial benchmark] {index}/{total} already running: {name}")
            post_message(f"[Serial benchmark] {index}/{total} running: {name}")
            result = run_one(name)
        except Exception as exc:
            handle_exception(name, exc)
            post_message(f"[Serial benchmark] {index}/{total} failed: {name}: {exc}")
        else:
            status = "completed" if result.status == "ok" else "failed"
            post_message(
                f"[Serial benchmark] {index}/{total} {status}: {name}: "
                f"{format_serial_benchmark_progress(result)}"
            )
        finally:
            if should_stop_after_run:
                post_message(f"[Serial benchmark] {index}/{total} stopping: {name}")
                try:
                    stop_one(name)
                except Exception as exc:
                    handle_exception(name, exc)
                    post_message(f"[Serial benchmark] {index}/{total} stop failed: {name}: {exc}")
                else:
                    post_message(f"[Serial benchmark] {index}/{total} stopped: {name}")
            completed = index
            set_active_name(None)

    return f"[Serial benchmark] finished {completed}/{total}."


def apply_managed_runtime_args(
    args: list[str],
    *,
    no_mmproj: bool = True,
    reasoning: str = "off",
    flash_attn: str = "auto",
) -> list[str]:
    """Apply GUI-managed llama-server runtime args without duplicating flags."""
    cleaned: list[str] = []
    skip_next = False

    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in MANAGED_FLAG_ARGS:
            continue
        if arg in MANAGED_VALUE_ARGS:
            skip_next = True
            continue
        cleaned.append(arg)

    if no_mmproj:
        cleaned.append("--no-mmproj")
    if reasoning:
        cleaned.extend(["--reasoning", reasoning])
    if flash_attn:
        cleaned.extend(["--flash-attn", flash_attn])

    return cleaned


def parse_tag_string(value: str) -> list[str]:
    """Parse a comma/space separated tag string for config storage."""
    tags: list[str] = []
    seen: set[str] = set()
    for tag in value.replace(",", " ").split():
        clean = tag.strip().lower()
        if clean and clean not in seen:
            tags.append(clean)
            seen.add(clean)
    return tags


def normalize_model_path_for_config(path: Path) -> Path:
    """Prefer project-relative model paths when the file lives under the repo root."""

    raw_path = path.expanduser()
    project_root = get_project_root().resolve()
    resolved = raw_path if raw_path.is_absolute() else (project_root / raw_path)
    resolved = resolved.resolve()
    try:
        return resolved.relative_to(project_root)
    except ValueError:
        return resolved


def format_download_bytes(value: int | None) -> str:
    """Format byte counts with a compact base-1024 GB display."""

    if value is None:
        return "-"
    return f"{value / (1024**3):.2f} GB"


def format_download_progress(progress: DownloadProgress) -> str:
    """Render one GGUF download progress line for the import dialog."""

    if progress.total_bytes:
        return (
            f"Downloading {Path(progress.filename).name}: "
            f"{format_download_bytes(progress.downloaded_bytes)} / "
            f"{format_download_bytes(progress.total_bytes)}"
        )
    return (
        f"Downloading {Path(progress.filename).name}: "
        f"{format_download_bytes(progress.downloaded_bytes)}"
    )


def resolve_models_directory_input(value: str) -> Path:
    """Resolve the local models directory against the project root when needed."""

    raw_value = value.strip()
    if not raw_value:
        return get_project_root() / "models"
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return path
    return get_project_root() / path


def format_metric(value: float | None, digits: int = 1) -> str:
    """Format optional numeric benchmark values for the table."""
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def format_model_size_gb(value: float | None) -> str:
    """Format optional GGUF file size using the GUI's base-1024 GB convention."""
    if value is None:
        return "-"
    return f"{value:.1f} GB"


def format_cpu_indicator(cpu_active: bool) -> str:
    """Render a compact CPU-in-use marker for the table."""
    return CPU_ACTIVE_GLYPH if cpu_active else ""


def normalize_gpu_alias(value: str) -> str:
    """Normalize an adapter alias and enforce the GUI display limit."""
    normalized = " ".join(value.strip().split())
    if len(normalized) > GPU_ALIAS_MAX_LENGTH:
        raise ValueError(f"GPU alias must be at most {GPU_ALIAS_MAX_LENGTH} characters.")
    return normalized


def get_gpu_aliases_path() -> Path:
    """Return the persisted adapter-name alias mapping path."""
    return get_state_dir() / "gpu_aliases.json"


def load_gpu_aliases() -> dict[str, str]:
    """Load GPU aliases keyed by detected adapter name."""
    path = get_gpu_aliases_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    raw_aliases = data.get("aliases") if isinstance(data, dict) else None
    if not isinstance(raw_aliases, dict):
        raw_aliases = data if isinstance(data, dict) else {}

    aliases: dict[str, str] = {}
    for raw_name, raw_alias in raw_aliases.items():
        name = str(raw_name).strip()
        if not name:
            continue
        try:
            alias = normalize_gpu_alias(str(raw_alias))
        except ValueError:
            alias = str(raw_alias).strip()[:GPU_ALIAS_MAX_LENGTH]
        if alias:
            aliases[name] = alias
    return aliases


def save_gpu_aliases(aliases: dict[str, str]) -> Path:
    """Persist GPU aliases keyed by adapter name."""
    cleaned: dict[str, str] = {}
    for raw_name, raw_alias in aliases.items():
        name = raw_name.strip()
        if not name:
            continue
        alias = normalize_gpu_alias(raw_alias)
        if alias:
            cleaned[name] = alias

    path = get_gpu_aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"aliases": dict(sorted(cleaned.items()))}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def gpu_alias_for_label(
    label: str,
    gpus: Sequence[DetectedGpu],
    aliases: dict[str, str],
) -> str | None:
    """Resolve a display alias for the current GPU label through the adapter name."""
    for gpu in gpus:
        if gpu.label == label and gpu.name:
            return aliases.get(gpu.name)
    return None


def format_runtime_gpu_display(
    labels: Sequence[str],
    gpus: Sequence[DetectedGpu],
    aliases: dict[str, str],
) -> str:
    """Render runtime GPU labels, replacing known adapter names with aliases."""
    if not labels:
        return "-"
    return ", ".join(gpu_alias_for_label(label, gpus, aliases) or label for label in labels)


def format_detected_gpu_summary(gpus: Sequence[DetectedGpu], aliases: dict[str, str] | None = None) -> str:
    """Format a multiline GPU inventory summary for the toolbar panel."""
    if not gpus:
        return (
            "No GPU mapping detected yet. Start or benchmark a GPU-backed instance "
            "to capture VulkanN adapter names."
        )
    aliases = aliases or {}
    return "\n".join(
        (
            f"{gpu.label} [{alias}] - {gpu.name}"
            if gpu.name and (alias := aliases.get(gpu.name))
            else f"{gpu.label} - {gpu.name}"
            if gpu.name
            else f"{gpu.label} - adapter name unavailable"
        )
        for gpu in gpus
    )


def format_benchmark_settings_summary(settings: BenchmarkSettings) -> str:
    """Format quick benchmark settings for compact GUI labels and logs."""
    optional = {
        "top-p": settings.top_p,
        "top-k": settings.top_k,
        "penalty": settings.repeat_penalty,
        "seed": settings.seed,
    }
    parts = [
        "chat" if settings.endpoint == "chat_completions" else "completion",
        f"{settings.max_tokens} tok",
        f"temp {settings.temperature:g}",
    ]
    parts.extend(
        f"{label} {value:g}" if isinstance(value, float) else f"{label} {value}"
        for label, value in optional.items()
        if value is not None
    )
    if settings.ignore_eos:
        parts.append("ignore EOS")
    return ", ".join(parts)


def format_benchmark_message(result: BenchmarkResult) -> str:
    """Format a benchmark result for the activity log."""
    if result.status != "ok":
        memory_text = format_benchmark_memory(result)
        warning = benchmark_shared_ram_warning(result)
        message = (
            f"Benchmark {result.instance_name} failed using {result.prompt_file}: "
            f"{result.error or 'unknown error'}"
        )
        if memory_text != "-":
            message = f"{message} Memory: {memory_text}."
        if warning:
            message = f"{message} {warning}"
        if result.artifact_file:
            message = f"{message} Artifact: {result.artifact_file}."
        return message
    warning = benchmark_shared_ram_warning(result)
    message = (
        f"Benchmark {result.instance_name} using {result.prompt_file}: "
        f"{format_metric(result.tokens_per_second)} TPS, "
        f"TTFT {format_metric(result.latency_ms, 0)} ms, "
        f"{format_benchmark_memory(result)}."
    )
    if warning:
        message = f"{message} {warning}"
    if result.artifact_file:
        message = f"{message} Artifact: {result.artifact_file}."
    return message


def benchmark_shared_ram_warning(result: BenchmarkResult) -> str:
    """Return a user-facing warning when benchmarked inference uses shared RAM."""
    if result.shared_ram_mb is None or result.shared_ram_mb <= 0:
        return ""
    if result.memory_source != "windows_process_counter" or result.memory_scope != "process":
        return ""
    return "Shared RAM in use; inference may be slower."


def format_benchmark_memory(result: BenchmarkResult) -> str:
    """Format benchmark memory for both the table and activity log."""
    dedicated_vram_mb = result.dedicated_vram_mb if result.dedicated_vram_mb is not None else result.vram_mb
    total_gpu_memory_mb = result.total_gpu_memory_mb
    shared_ram_mb = result.shared_ram_mb
    memory_source = result.memory_source or ("legacy_unknown" if result.vram_mb is not None else None)
    memory_scope = result.memory_scope

    if dedicated_vram_mb is None and total_gpu_memory_mb is None:
        return "-"
    if total_gpu_memory_mb is None:
        total_gpu_memory_mb = dedicated_vram_mb
    if shared_ram_mb is None:
        if dedicated_vram_mb is None:
            return f"{format_metric(total_gpu_memory_mb, 0)} total"
        if memory_source == "vendor_device_cli":
            return f"{format_metric(dedicated_vram_mb, 0)} VRAM (device-level; RAM unknown)"
        if memory_source in {"log_model_buffer", "log_device_free_delta"}:
            label = "model buffer" if memory_source == "log_model_buffer" else "device delta"
            return f"{format_metric(dedicated_vram_mb, 0)} {label} (log estimate; RAM unknown)"
        return (
            f"{format_metric(total_gpu_memory_mb, 0)} total "
            f"(VRAM {format_metric(dedicated_vram_mb, 0)}"
            f"{'; RAM unknown' if memory_scope not in {'process', None} else ''})"
        )
    suffix = " slow" if shared_ram_mb > 0 else ""
    return (
        f"{format_metric(total_gpu_memory_mb, 0)} total "
        f"(VRAM {format_metric(dedicated_vram_mb, 0)}, "
        f"RAM {format_metric(shared_ram_mb, 0)}){suffix}"
    )


def derive_display_status_and_health(state: InstanceState | None) -> tuple[str, str]:
    """Present ready vs loading distinctly while preserving engine runtime semantics."""
    if state is None:
        return InstanceStatus.STOPPED.value, HealthStatus.UNKNOWN.value

    if state.status == InstanceStatus.RUNNING:
        if state.health == HealthStatus.HEALTHY:
            return "ready", state.health.value
        if state.health == HealthStatus.LOADING:
            return "loading", state.health.value

    return state.status.value, state.health.value


def persist_instance_health(
    name: str,
    state: InstanceState | None,
    *,
    port: int,
    health: HealthStatus,
    error_message: str = "",
    response_time_ms: float | None = None,
) -> None:
    """Persist a GUI-observed health state so refresh shows current readiness."""
    checked_at = time.time()
    current_state = state or InstanceState(name=name, status=InstanceStatus.STOPPED)
    current_state.health = health
    current_state.last_health_check = checked_at
    if health in {HealthStatus.UNHEALTHY, HealthStatus.ERROR}:
        current_state.error_message = error_message
    elif error_message == "":
        current_state.error_message = ""
    save_state(current_state)

    runtime = load_runtime(name) or RuntimeState(name=name)
    runtime.pid = current_state.pid
    runtime.port = port
    runtime.status = current_state.status
    runtime.health = health
    runtime.started_at = runtime.started_at or current_state.start_time
    runtime.last_seen_at = checked_at
    if health == HealthStatus.HEALTHY:
        runtime.last_health_ok_at = checked_at
        runtime.last_error = ""
    elif error_message:
        runtime.last_error = error_message
    save_runtime(runtime)
    record_health_check(
        name,
        health,
        response_time_ms=response_time_ms,
        error_message=error_message,
    )


class LlamaOrchestratorGui(tk.Tk):
    """Desktop GUI for managing llama.cpp model instances."""

    refresh_interval_ms = 5000

    def __init__(self) -> None:
        super().__init__()
        self.title("llama-orchestrator")
        self.geometry("1260x720")
        self.minsize(960, 560)

        self.project_root = get_project_root()
        self._messages: queue.Queue[str] = queue.Queue()
        self._selected_name: str | None = None
        self._selected_names: tuple[str, ...] = ()
        self._focused_name: str | None = None
        self.benchmark_settings = load_benchmark_settings(self.project_root)
        self.gui_settings: GuiSettings = load_gui_settings(ALL_COLUMNS)
        if "queue" not in self.gui_settings.visible_columns:
            self.gui_settings = replace(
                self.gui_settings,
                visible_columns=("queue", *self.gui_settings.visible_columns),
            )
            save_gui_settings(self.gui_settings)
        self.benchmark_params_menu: tk.Menu | None = None
        self.tag_filter_var = tk.StringVar(value="All tags")
        self.prompt_var = tk.StringVar(value=self.benchmark_settings.prompt_file.name)
        self.show_gpu_inventory_var = tk.BooleanVar(value=True)
        self.gpu_inventory_var = tk.StringVar(value=format_detected_gpu_summary(()))
        self.gpu_aliases = load_gpu_aliases()
        self._detected_gpus: tuple[DetectedGpu, ...] = ()
        self._queued_benchmark_names: set[str] = set()
        self._benchmark_active_name: str | None = None
        self._benchmark_job_lock = threading.Lock()
        self._benchmark_job_active = False
        self._benchmark_job_label: str | None = None
        self._serial_benchmark_stop = threading.Event()
        self._serial_benchmark_active = False
        self._grid_benchmark_stop = threading.Event()
        self._grid_benchmark_active = False
        self.quick_benchmark_button: ttk.Button | None = None
        self.serial_benchmark_button: ttk.Button | None = None
        self.stop_serial_benchmark_button: ttk.Button | None = None
        self.grid_benchmark_button: ttk.Button | None = None
        self.stop_grid_benchmark_button: ttk.Button | None = None

        self._build_widgets()
        self._schedule_message_pump()
        self.refresh()
        self.after(500, self._check_vulkan_binary)
        self.after(self.refresh_interval_ms, self._auto_refresh)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(16, weight=1)

        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(toolbar, text="Add model", command=self._open_add_dialog).grid(row=0, column=1, padx=6)
        ttk.Button(toolbar, text="Apply args", command=self._apply_default_args).grid(row=0, column=2, padx=6)
        ttk.Button(toolbar, text=INSTALL_LLAMA_SERVER_LABEL, command=self._open_binary_dialog).grid(row=0, column=3, padx=6)
        ttk.Button(toolbar, text="Start", command=lambda: self._run_selected("start")).grid(row=0, column=4, padx=6)
        ttk.Button(toolbar, text="Stop", command=lambda: self._run_selected("stop")).grid(row=0, column=5, padx=6)
        ttk.Button(toolbar, text="Restart", command=lambda: self._run_selected("restart")).grid(row=0, column=6, padx=6)
        ttk.Button(toolbar, text="Health", command=lambda: self._run_selected("health")).grid(row=0, column=7, padx=6)

        columns_button = ttk.Menubutton(toolbar, text="Columns")
        columns_menu = tk.Menu(columns_button, tearoff=False)
        columns_button["menu"] = columns_menu
        self._column_vars: dict[str, tk.BooleanVar] = {}
        for column in ALL_COLUMNS:
            var = tk.BooleanVar(value=column in self.gui_settings.visible_columns)
            self._column_vars[column] = var
            columns_menu.add_checkbutton(
                label=COLUMN_HEADINGS[column],
                variable=var,
                command=self._apply_visible_columns,
            )
        columns_button.grid(row=0, column=8, padx=6)

        batch_button = ttk.Menubutton(toolbar, text="Batch")
        batch_menu = tk.Menu(batch_button, tearoff=False)
        batch_button["menu"] = batch_menu
        batch_menu.add_command(label="Start visible", command=lambda: self._run_batch("start"))
        batch_menu.add_command(label="Stop visible", command=lambda: self._run_batch("stop"))
        batch_menu.add_command(label="Restart visible", command=lambda: self._run_batch("restart"))
        batch_button.grid(row=0, column=9, padx=6)

        ttk.Label(toolbar, text="Tag").grid(row=0, column=10, sticky="e", padx=(12, 4))
        self.tag_filter = ttk.Combobox(
            toolbar,
            textvariable=self.tag_filter_var,
            values=("All tags",),
            state="readonly",
            width=16,
        )
        self.tag_filter.grid(row=0, column=11, sticky="w", padx=(0, 6))
        self.tag_filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Button(toolbar, text=EDIT_BENCHMARK_PROMPT_LABEL, command=self._select_prompt_file).grid(row=0, column=12, padx=6)
        ttk.Label(toolbar, textvariable=self.prompt_var).grid(row=0, column=13, sticky="w", padx=(0, 6))
        ttk.Checkbutton(
            toolbar,
            text="GPU map",
            variable=self.show_gpu_inventory_var,
            command=self._toggle_gpu_inventory,
        ).grid(row=0, column=14, padx=(12, 6))

        self.daemon_var = tk.StringVar(value="Daemon: unknown")
        ttk.Label(toolbar, textvariable=self.daemon_var).grid(row=0, column=16, sticky="e", padx=(10, 6))
        ttk.Button(toolbar, text="Start daemon", command=self._start_daemon).grid(row=0, column=17, padx=6)
        ttk.Button(toolbar, text="Stop daemon", command=self._stop_daemon).grid(row=0, column=18, padx=(6, 0))

        main = ttk.PanedWindow(self, orient=tk.VERTICAL)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        table_frame = ttk.Frame(main)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)
        main.add(table_frame, weight=4)

        self.gpu_inventory_frame = ttk.LabelFrame(table_frame, text="Detected GPUs", padding=(8, 6))
        self.gpu_inventory_frame.columnconfigure(0, weight=1)
        self.gpu_inventory_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.gpu_inventory_rows = ttk.Frame(self.gpu_inventory_frame)
        self.gpu_inventory_rows.columnconfigure(2, weight=1)
        self.gpu_inventory_rows.grid(row=0, column=0, sticky="ew")

        self.tree = ttk.Treeview(table_frame, columns=ALL_COLUMNS, show="headings", selectmode="extended")
        for column in ALL_COLUMNS:
            self.tree.column(column, width=COLUMN_WIDTHS[column], anchor=tk.W)
        self.tree.tag_configure(RUNNING_BENCHMARK_ROW_TAG, background="#fff4cc")
        self._refresh_tree_headings()

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        y_scroll.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._on_tree_click, add=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self._apply_visible_columns()
        self._toggle_gpu_inventory()

        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="Quick benchmark", command=self._run_benchmark_selected)
        self.context_menu.add_command(label=GRID_BENCHMARK_LABEL, command=self._run_grid_benchmark)
        self.context_menu.add_command(label="Toggle benchmark queue", command=self._toggle_selected_queue_rows)
        self.context_menu.add_command(label="Clone row", command=self._clone_selected)
        self.context_menu.add_command(label="Rename display name", command=self._rename_display_name)
        self.context_menu.add_command(label="Copy as CLI command", command=self._copy_cli_command)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Export config to VS Code", command=self._export_config_to_vscode)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Open config", command=self._open_config)
        self.context_menu.add_command(label="Open config folder", command=self._open_config_folder)

        detail_bar = ttk.Frame(table_frame, padding=(0, 8, 0, 0))
        detail_bar.grid(row=2, column=0, sticky="ew")
        self.quick_benchmark_button = ttk.Button(detail_bar, text="Quick benchmark", command=self._run_benchmark_selected)
        self.quick_benchmark_button.pack(side=tk.LEFT)
        self.serial_benchmark_button = ttk.Button(
            detail_bar,
            text="Serial benchmark",
            command=self._run_serial_benchmark,
        )
        self.serial_benchmark_button.pack(side=tk.LEFT, padx=(6, 0))
        self.grid_benchmark_button = ttk.Button(
            detail_bar,
            text=GRID_BENCHMARK_LABEL,
            command=self._run_grid_benchmark,
        )
        self.grid_benchmark_button.pack(side=tk.LEFT, padx=(6, 0))
        self.stop_serial_benchmark_button = ttk.Button(
            detail_bar,
            text="Stop queue",
            command=self._stop_serial_benchmark,
        )
        self.stop_serial_benchmark_button.pack(side=tk.LEFT, padx=(6, 0))
        self.stop_grid_benchmark_button = ttk.Button(
            detail_bar,
            text="Stop grid",
            command=self._stop_grid_benchmark,
        )
        self.stop_grid_benchmark_button.pack(side=tk.LEFT, padx=(6, 0))
        params_button = ttk.Menubutton(detail_bar, text=BENCHMARK_PARAMS_MENU_LABEL)
        self.benchmark_params_menu = tk.Menu(params_button, tearoff=False)
        params_button["menu"] = self.benchmark_params_menu
        params_button.pack(side=tk.LEFT, padx=(4, 6))
        self._refresh_benchmark_params_menu()
        ttk.Button(detail_bar, text="Clone row", command=self._clone_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Rename", command=self._rename_display_name).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Diff selected", command=self._diff_selected).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Copy CLI", command=self._copy_cli_command).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open config", command=self._open_config).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Open config folder", command=self._open_config_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open logs", command=self._open_logs).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open project", command=self._open_project).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open prompt", command=self._open_prompt_file).pack(side=tk.LEFT)
        ttk.Frame(detail_bar).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(detail_bar, text="Reset GUI", command=self._reset_gui_state).pack(side=tk.RIGHT)

        log_frame = ttk.Frame(main)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        main.add(log_frame, weight=2)

        ttk.Label(log_frame, text="Activity").grid(row=0, column=0, sticky="w", pady=(8, 2))
        self.activity = scrolledtext.ScrolledText(log_frame, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.activity.grid(row=1, column=0, sticky="nsew")
        self._update_benchmark_controls()

    def _auto_refresh(self) -> None:
        self.refresh()
        self.after(self.refresh_interval_ms, self._auto_refresh)

    def _apply_visible_columns(self, *, persist: bool = True) -> None:
        visible = [column for column in ALL_COLUMNS if self._column_vars[column].get()]
        if not visible:
            self._column_vars["name"].set(True)
            visible = ["name"]
        self.tree["displaycolumns"] = visible
        if persist:
            self.gui_settings = replace(self.gui_settings, visible_columns=tuple(visible))
            save_gui_settings(self.gui_settings)

    def _refresh_tree_headings(self) -> None:
        for column in ALL_COLUMNS:
            self.tree.heading(
                column,
                text=format_sort_heading(COLUMN_HEADINGS[column], column, self.gui_settings.sort_order),
                command=lambda current=column: self._toggle_sort(current),
            )

    def _toggle_sort(self, column: str) -> None:
        self.gui_settings = replace(
            self.gui_settings,
            sort_order=cycle_sort_order(self.gui_settings.sort_order, column),
        )
        save_gui_settings(self.gui_settings)
        self._refresh_tree_headings()
        self.refresh()

    def _reset_gui_state(self) -> None:
        self.gui_settings = replace(self.gui_settings, sort_order=())
        save_gui_settings(self.gui_settings)
        self._refresh_tree_headings()
        self.refresh()
        self._post_message("GUI sorting reset.")

    def _toggle_gpu_inventory(self) -> None:
        if self.show_gpu_inventory_var.get():
            self.gpu_inventory_frame.grid()
        else:
            self.gpu_inventory_frame.grid_remove()

    def _render_gpu_inventory(self, gpus: Sequence[DetectedGpu]) -> None:
        for child in self.gpu_inventory_rows.winfo_children():
            child.destroy()

        if not gpus:
            ttk.Label(
                self.gpu_inventory_rows,
                text=self.gpu_inventory_var.get(),
                justify=tk.LEFT,
            ).grid(row=0, column=0, columnspan=3, sticky="w")
            return

        ttk.Label(self.gpu_inventory_rows, text="Device").grid(row=0, column=0, sticky="w")
        ttk.Label(self.gpu_inventory_rows, text="Alias").grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Label(self.gpu_inventory_rows, text="Adapter").grid(row=0, column=2, sticky="w")

        for row_index, gpu in enumerate(gpus, start=1):
            ttk.Label(self.gpu_inventory_rows, text=gpu.label).grid(row=row_index, column=0, sticky="w")
            alias = self.gpu_aliases.get(gpu.name or "", "")
            alias_button = ttk.Button(
                self.gpu_inventory_rows,
                text=alias or "-",
                width=GPU_ALIAS_BUTTON_WIDTH,
                command=lambda adapter_name=gpu.name: self._edit_gpu_alias(adapter_name),
            )
            if not gpu.name:
                alias_button.configure(state=tk.DISABLED)
            alias_button.grid(row=row_index, column=1, sticky="w", padx=(8, 8), pady=(2, 0))
            ttk.Label(
                self.gpu_inventory_rows,
                text=gpu.name or "adapter name unavailable",
            ).grid(row=row_index, column=2, sticky="w", pady=(2, 0))

    def _edit_gpu_alias(self, adapter_name: str | None) -> None:
        if not adapter_name:
            return
        current = self.gpu_aliases.get(adapter_name, "")
        value = simpledialog.askstring(
            "GPU alias",
            f"Alias for {adapter_name}\nMaximum {GPU_ALIAS_MAX_LENGTH} characters. Blank clears alias.",
            initialvalue=current,
            parent=self,
        )
        if value is None:
            return
        try:
            alias = normalize_gpu_alias(value)
        except ValueError as exc:
            messagebox.showerror("Invalid GPU alias", str(exc), parent=self)
            return

        if alias:
            self.gpu_aliases[adapter_name] = alias
        else:
            self.gpu_aliases.pop(adapter_name, None)

        try:
            save_gpu_aliases(self.gpu_aliases)
        except Exception as exc:
            messagebox.showerror("Save GPU alias failed", str(exc), parent=self)
            return

        self._post_message(f"Saved GPU alias for {adapter_name}: {alias or 'cleared'}.")
        self.refresh()

    def _schedule_message_pump(self) -> None:
        self._pump_messages()
        self.after(250, self._schedule_message_pump)

    def _pump_messages(self) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            if message == "__REFRESH__":
                self.refresh()
                continue
            self._append_activity(message)

    def _append_activity(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.activity.configure(state=tk.NORMAL)
        self.activity.insert(tk.END, f"[{timestamp}] {message}\n")
        self.activity.see(tk.END)
        self.activity.configure(state=tk.DISABLED)

    def _post_message(self, message: str) -> None:
        self._messages.put(message)

    def refresh(self) -> None:
        _t0 = time.perf_counter_ns()
        selection, focused = self._capture_tree_position()
        _t1 = time.perf_counter_ns()
        snapshot = self._collect_refresh_snapshot(
            queued_names=frozenset(self._queued_benchmark_names),
            active_tag=self.tag_filter_var.get(),
        )
        _t4 = time.perf_counter_ns()
        self._render_full_rows(snapshot.rows)
        _t5 = time.perf_counter_ns()
        self._render_refresh_metadata(snapshot, selection, focused)
        _t6 = time.perf_counter_ns()
        self._update_benchmark_controls()
        self._render_daemon_status()
        _t7 = time.perf_counter_ns()
        if _GUI_TIMING_ENABLED:
            ns = 1_000_000
            self._post_message(
                f"[gui-timing] refresh() total={(_t7 - _t0) / ns:.1f}ms "
                f"sel={(_t1 - _t0) / ns:.1f}ms collect={(_t4 - _t1) / ns:.1f}ms "
                f"render={(_t5 - _t4) / ns:.1f}ms metadata={(_t6 - _t5) / ns:.1f}ms "
                f"done={(_t7 - _t6) / ns:.1f}ms"
            )

    def _capture_tree_position(self) -> tuple[tuple[str, ...], str | None]:
        selection = tuple(str(item) for item in self.tree.selection()) or self._selected_names
        if not selection and self._selected_name:
            selection = (self._selected_name,)
        focused = str(self.tree.focus()) if self.tree.focus() else self._focused_name
        if not focused and selection:
            focused = selection[0]
        return selection, focused

    def _collect_refresh_snapshot(
        self,
        *,
        queued_names: frozenset[str],
        active_tag: str,
    ) -> GuiRefreshSnapshot:
        states = list_instances()
        configs = load_all_instances()
        detected_gpus = collect_detected_gpu_inventory(configs.values())
        self._detected_gpus = tuple(detected_gpus)
        latest_results = latest_benchmark_results()
        rows, all_tags = self._build_table_rows(
            states=states,
            configs=configs,
            detected_gpus=tuple(detected_gpus),
            latest_results=latest_results,
            queued_names=queued_names,
            active_tag=active_tag,
        )
        return GuiRefreshSnapshot(
            rows=rows,
            detected_gpus=tuple(detected_gpus),
            all_tags=tuple(sorted(all_tags)),
            collected_at=time.time(),
        )

    def _build_table_rows(
        self,
        *,
        states: dict[str, InstanceState],
        configs: dict[str, InstanceConfig],
        detected_gpus: tuple[DetectedGpu, ...],
        latest_results: dict[str, BenchmarkResult],
        queued_names: frozenset[str],
        active_tag: str,
    ) -> tuple[tuple[TableRow, ...], set[str]]:
        all_names = sorted(set(states) | set(configs))
        all_tags: set[str] = set()
        rows: list[TableRow] = []

        for name in all_names:
            state = states.get(name)
            try:
                config = configs[name]
                display_name = config.display_name
                runtime_selection = describe_effective_runtime(config)
                port = str(config.server.port)
                backend = config.gpu.backend
                gpu = format_runtime_gpu_display(
                    runtime_selection.gpu_labels if runtime_selection.gpu_active else (),
                    detected_gpus,
                    self.gpu_aliases,
                )
                cpu = format_cpu_indicator(runtime_selection.cpu_active)
                cpu_active = runtime_selection.cpu_active
                tags = ", ".join(config.tags) if config.tags else "-"
                all_tags.update(config.tags)
                model = str(config.model.path)
                model_size_value = resolve_model_size_gb(config)
                model_size = format_model_size_gb(model_size_value)
                quantization = "-"
                architecture = "-"
                if config.model_metadata is not None:
                    if config.model_metadata.quantization.name:
                        quantization = config.model_metadata.quantization.name
                    if config.model_metadata.identity.architecture:
                        architecture = config.model_metadata.identity.architecture
                runtime_args = " ".join(config.args) if config.args else "-"
                sort_tags: object = tuple(config.tags)
                sort_port: object = config.server.port
                sort_backend: object = backend
                sort_gpu: object = gpu
                sort_model: object = model
                sort_model_size: object = model_size_value
                sort_quantization: object = quantization if quantization != "-" else None
                sort_architecture: object = architecture if architecture != "-" else None
                sort_args: object = tuple(config.args)
            except Exception:
                display_name = name
                port = "-"
                backend = "-"
                gpu = "-"
                cpu = ""
                cpu_active = False
                tags = "-"
                model = "-"
                model_size = "-"
                quantization = "-"
                architecture = "-"
                runtime_args = "-"
                sort_tags = ()
                sort_port = None
                sort_backend = None
                sort_gpu = None
                sort_model = None
                sort_model_size = None
                sort_quantization = None
                sort_architecture = None
                sort_args = ()

            if active_tag != "All tags" and active_tag not in {tag.strip() for tag in tags.split(",")}:
                continue

            status, health = derive_display_status_and_health(state)
            pid = str(state.pid) if state and state.pid else "-"
            uptime = state.uptime_str if state else "-"
            uptime_value = state.uptime if state else None
            benchmark = latest_results.get(name)
            if benchmark and benchmark.status == "ok":
                total_memory = benchmark.total_gpu_memory_mb or benchmark.vram_mb
                tps = format_metric(benchmark.tokens_per_second)
                latency = format_metric(benchmark.latency_ms, 0)
                vram = format_benchmark_memory(benchmark)
                prompt = benchmark.prompt_file
            elif benchmark:
                total_memory = benchmark.total_gpu_memory_mb or benchmark.vram_mb
                tps = "failed"
                latency = "-"
                vram = format_benchmark_memory(benchmark)
                prompt = benchmark.prompt_file
            else:
                total_memory = None
                tps = "-"
                latency = "-"
                vram = "-"
                prompt = "-"

            rows.append(
                TableRow(
                    name=name,
                    values=(
                        format_queue_checkbox(name in queued_names),
                        display_name,
                        status,
                        health,
                        pid,
                        port,
                        backend,
                        gpu,
                        cpu,
                        tags,
                        tps,
                        latency,
                        vram,
                        prompt,
                        model_size,
                        quantization,
                        architecture,
                        model,
                        runtime_args,
                        uptime,
                    ),
                    sort_values={
                        "queue": name in queued_names,
                        "name": (display_name.casefold(), name.casefold()),
                        "status": status,
                        "health": health,
                        "pid": state.pid if state and state.pid else None,
                        "port": sort_port,
                        "backend": sort_backend,
                        "gpu": sort_gpu,
                        "cpu": cpu_active,
                        "tags": sort_tags,
                        "tps": benchmark.tokens_per_second if benchmark and benchmark.status == "ok" else None,
                        "latency": benchmark.latency_ms if benchmark and benchmark.status == "ok" else None,
                        "vram": total_memory,
                        "prompt": benchmark.prompt_file if benchmark else None,
                        "model_size": sort_model_size,
                        "quantization": sort_quantization,
                        "architecture": sort_architecture,
                        "model": sort_model,
                        "args": sort_args,
                        "uptime": uptime_value,
                    },
                )
            )

        return tuple(rows), all_tags

    def _visible_rows(self, rows: Sequence[TableRow]) -> tuple[TableRow, ...]:
        return tuple(
            stable_sort_rows(
                list(rows),
                self.gui_settings.sort_order,
                lambda current, column: current.sort_values.get(column),
            )
        )

    def _render_full_rows(self, rows: Sequence[TableRow]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self._visible_rows(rows):
            tags = (RUNNING_BENCHMARK_ROW_TAG,) if row.name == self._benchmark_active_name else ()
            self.tree.insert("", tk.END, iid=row.name, values=row.values, tags=tags)

    def _render_refresh_metadata(
        self,
        snapshot: GuiRefreshSnapshot,
        selection: tuple[str, ...],
        focused: str | None,
    ) -> None:
        self.gpu_inventory_var.set(format_detected_gpu_summary(snapshot.detected_gpus, self.gpu_aliases))
        self._render_gpu_inventory(snapshot.detected_gpus)

        filter_values = ("All tags", *snapshot.all_tags)
        self.tag_filter.configure(values=filter_values)
        if self.tag_filter_var.get() not in filter_values:
            self.tag_filter_var.set("All tags")
        self.prompt_var.set(self.benchmark_settings.prompt_file.name)

        visible_names = {row.name for row in snapshot.rows}
        visible_selection = tuple(name for name in selection if name in visible_names)
        if visible_selection:
            self.tree.selection_set(visible_selection)
        focus_target = None
        if focused and focused in visible_names:
            focus_target = focused
        elif visible_selection:
            focus_target = visible_selection[0]
        if focus_target:
            self.tree.focus(focus_target)
            self.tree.see(focus_target)

        self._selected_names = visible_selection
        self._selected_name = visible_selection[0] if visible_selection else None
        self._focused_name = focus_target

    def _render_daemon_status(self) -> None:
        daemon = get_daemon_status()
        if daemon.running:
            self.daemon_var.set(f"Daemon: running (PID {daemon.pid})")
        else:
            self.daemon_var.set("Daemon: stopped")

    def _on_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        self._selected_names = tuple(str(item) for item in selection)
        self._selected_name = self._selected_names[0] if self._selected_names else None
        focused = str(self.tree.focus()) if self.tree.focus() else None
        self._focused_name = focused or self._selected_name

    def _tree_column_from_event(self, event: tk.Event) -> str | None:
        column_id = self.tree.identify_column(event.x)
        if not column_id.startswith("#"):
            return None
        try:
            display_index = int(column_id[1:]) - 1
        except ValueError:
            return None
        display_columns = self.tree["displaycolumns"]
        columns = list(ALL_COLUMNS) if display_columns == "#all" else list(display_columns)
        if display_index < 0 or display_index >= len(columns):
            return None
        return columns[display_index]

    def _on_tree_click(self, event: tk.Event) -> str | None:
        item = self.tree.identify_row(event.y)
        if not item or self._tree_column_from_event(event) != "queue":
            return None
        self._toggle_queue_name(item)
        return "break"

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        self.tree.focus(item)
        self._selected_names = (item,)
        self._selected_name = item
        self._focused_name = item
        if self._tree_column_from_event(event) == "args":
            self._edit_args_inline(item, event)
            return
        self._open_config()

    def _show_context_menu(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
            self.tree.focus(item)
            self._selected_names = (item,)
            self._selected_name = item
            self._focused_name = item
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _selected_instance(self) -> str | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No model selected", "Select a model instance first.")
            return None
        return selection[0]

    def _selected_instances(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.tree.selection())

    def _visible_instance_names(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.tree.get_children())

    def _ordered_queued_instance_names(self) -> tuple[str, ...]:
        return ordered_visible_names(self._queued_benchmark_names, self._visible_instance_names())

    def _update_queue_cells(self, names: Sequence[str]) -> None:
        """Fast-path: update only the queue column for the given visible row names.

        Updates the queue cell glyph for each named row without rebuilding the
        entire Treeview.  Rows that are not currently visible are skipped; the
        next normal refresh will render their correct state.
        """
        for item in self.tree.get_children():
            if item in names:
                checked = item in self._queued_benchmark_names
                current = self.tree.set(item, "queue")
                expected = format_queue_checkbox(checked)
                if current != expected:
                    self.tree.set(item, "queue", expected)

    def _toggle_queue_name(self, name: str) -> None:
        if name in self._queued_benchmark_names:
            self._queued_benchmark_names.remove(name)
        else:
            self._queued_benchmark_names.add(name)
        self._update_queue_cells((name,))
        self._update_benchmark_controls()

    def _set_active_benchmark_name(self, name: str | None) -> None:
        self._benchmark_active_name = name
        self._post_message("__REFRESH__")

    def _toggle_selected_queue_rows(self) -> None:
        names = self._selected_instances()
        if not names:
            messagebox.showinfo("No model selected", "Select one or more model instances first.")
            return
        should_queue = any(name not in self._queued_benchmark_names for name in names)
        for name in names:
            if should_queue:
                self._queued_benchmark_names.add(name)
            else:
                self._queued_benchmark_names.discard(name)
        self._update_queue_cells(names)
        self._update_benchmark_controls()

    def _benchmark_job_running(self) -> bool:
        with self._benchmark_job_lock:
            return self._benchmark_job_active

    def _begin_benchmark_job(self, label: str) -> bool:
        with self._benchmark_job_lock:
            if self._benchmark_job_active:
                return False
            self._benchmark_job_active = True
            self._benchmark_job_label = label
            return True

    def _finish_benchmark_job(self) -> None:
        with self._benchmark_job_lock:
            self._benchmark_job_active = False
            self._benchmark_job_label = None

    def _update_benchmark_controls(self) -> None:
        running = self._benchmark_job_running()
        queued_visible = bool(self._ordered_queued_instance_names())
        if self.quick_benchmark_button is not None:
            self.quick_benchmark_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        if self.serial_benchmark_button is not None:
            self.serial_benchmark_button.configure(
                state=tk.DISABLED if running or not queued_visible else tk.NORMAL
            )
        if self.grid_benchmark_button is not None:
            has_grid_target = bool(self.tree.selection() or queued_visible)
            self.grid_benchmark_button.configure(
                state=tk.DISABLED if running or not has_grid_target else tk.NORMAL
            )
        if self.stop_serial_benchmark_button is not None:
            self.stop_serial_benchmark_button.configure(
                state=(
                    tk.NORMAL
                    if self._serial_benchmark_active and not self._serial_benchmark_stop.is_set()
                    else tk.DISABLED
                )
            )
        if self.stop_grid_benchmark_button is not None:
            self.stop_grid_benchmark_button.configure(
                state=(
                    tk.NORMAL
                    if self._grid_benchmark_active and not self._grid_benchmark_stop.is_set()
                    else tk.DISABLED
                )
            )

    def _run_background(self, label: str, action: Callable[[], str | None]) -> None:
        def worker() -> None:
            try:
                result = action()
                self._post_message(result or f"{label} completed.")
            except Exception as exc:
                self._post_message(f"{label} failed: {exc}")
            finally:
                self._post_message("__REFRESH__")

        self._post_message(f"{label} started.")
        threading.Thread(target=worker, daemon=True).start()

    def _run_selected(self, action_name: str) -> None:
        name = self._selected_instance()
        if not name:
            return

        def action() -> str:
            if action_name == "start":
                state = start_instance(name)
                return f"Started {name} (PID {state.pid})."
            if action_name == "stop":
                stop_instance(name)
                return f"Stopped {name}."
            if action_name == "restart":
                state = restart_instance(name)
                return f"Restarted {name} (PID {state.pid})."
            if action_name == "health":
                config = get_instance_config(name)
                state = list_instances().get(name)
                result = check_instance_health(name)
                persist_instance_health(
                    name,
                    state,
                    port=config.server.port,
                    health=result.to_health_status,
                    error_message=result.error_message or "",
                    response_time_ms=result.response_time_ms,
                )
                detail = result.error_message or f"{result.response_time_ms or 0:.1f} ms"
                return f"Health {name}: {result.status.value} ({detail})."
            raise ValueError(f"Unknown action: {action_name}")

        self._run_background(f"{action_name} {name}", action)

    def _benchmark_instance(self, name: str, settings: BenchmarkSettings) -> BenchmarkResult:
        config = get_instance_config(name)
        result = quick_benchmark_instance(config, settings)
        current_state = list_instances().get(name)
        if result.status == "ok":
            persist_instance_health(
                name,
                current_state,
                port=config.server.port,
                health=HealthStatus.HEALTHY,
                response_time_ms=result.latency_ms,
            )
            return result

        health_result = check_instance_health(name)
        persist_instance_health(
            name,
            current_state,
            port=config.server.port,
            health=health_result.to_health_status,
            error_message=health_result.error_message or result.error or "",
            response_time_ms=health_result.response_time_ms,
        )
        return result

    def _start_serial_benchmark_instance(self, name: str) -> bool:
        state = list_instances().get(name)
        if state is not None and state.status == InstanceStatus.RUNNING:
            return False
        start_instance(name)
        return True

    def _handle_benchmark_exception(self, name: str, exc: Exception) -> None:
        try:
            config = get_instance_config(name)
            port = config.server.port
        except Exception:
            port = 0
        error_message = str(exc)
        try:
            health_result = check_instance_health(name)
        except Exception:
            persist_instance_health(
                name,
                list_instances().get(name),
                port=port,
                health=HealthStatus.ERROR,
                error_message=error_message,
            )
            return

        persist_instance_health(
            name,
            list_instances().get(name),
            port=port,
            health=health_result.to_health_status,
            error_message=health_result.error_message or error_message,
            response_time_ms=health_result.response_time_ms,
        )

    def _run_batch(self, action_name: str) -> None:
        names = tuple(str(item) for item in self.tree.get_children())
        if not names:
            messagebox.showinfo("No visible models", "No visible model instances match the current filter.")
            return

        def action() -> str:
            completed: list[str] = []
            for name in names:
                if action_name == "start":
                    start_instance(name)
                elif action_name == "stop":
                    stop_instance(name)
                elif action_name == "restart":
                    restart_instance(name)
                else:
                    raise ValueError(f"Unknown batch action: {action_name}")
                completed.append(name)
            return f"{action_name.capitalize()}ed {len(completed)} visible instance(s)."

        label = f"{action_name} {len(names)} visible instance(s)"
        self._run_background(label, action)

    def _run_benchmark_selected(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        if not self._begin_benchmark_job(f"benchmark {name}"):
            active_label = self._benchmark_job_label or "Another benchmark"
            messagebox.showinfo("Benchmark already running", f"{active_label} is already in progress.")
            return
        self._reload_benchmark_settings()

        def action() -> str:
            self._set_active_benchmark_name(name)
            try:
                result = self._benchmark_instance(name, self.benchmark_settings)
                return format_benchmark_message(result)
            except Exception as exc:
                self._handle_benchmark_exception(name, exc)
                raise
            finally:
                self._set_active_benchmark_name(None)
                self._finish_benchmark_job()

        self._run_background(f"benchmark {name}", action)

    def _run_serial_benchmark(self) -> None:
        names = self._ordered_queued_instance_names()
        if not names:
            messagebox.showinfo("No queued models", "Check one or more visible model instances first.")
            return
        if not self._begin_benchmark_job("Serial benchmark"):
            active_label = self._benchmark_job_label or "Another benchmark"
            messagebox.showinfo("Benchmark already running", f"{active_label} is already in progress.")
            return
        self._serial_benchmark_active = True
        self._serial_benchmark_stop.clear()
        self._update_benchmark_controls()

        def action() -> str:
            try:
                return run_serial_benchmark_queue(
                    names,
                    should_stop=self._serial_benchmark_stop.is_set,
                    set_active_name=self._set_active_benchmark_name,
                    start_one=self._start_serial_benchmark_instance,
                    run_one=lambda current: self._benchmark_instance(
                        current,
                        load_benchmark_settings(self.project_root),
                    ),
                    stop_one=stop_instance,
                    handle_exception=self._handle_benchmark_exception,
                    post_message=self._post_message,
                )
            finally:
                self._set_active_benchmark_name(None)
                self._serial_benchmark_active = False
                self._serial_benchmark_stop.clear()
                self._finish_benchmark_job()

        self._run_background("Serial benchmark", action)

    def _stop_serial_benchmark(self) -> None:
        if not self._serial_benchmark_active:
            return
        self._serial_benchmark_stop.set()
        self._post_message("[Serial benchmark] stop requested.")
        self._update_benchmark_controls()

    def _grid_benchmark_targets(self) -> tuple[str, ...]:
        queued = self._ordered_queued_instance_names()
        if queued:
            return queued
        selection = self.tree.selection()
        return (str(selection[0]),) if selection else ()

    def _run_grid_benchmark(self) -> None:
        names = self._grid_benchmark_targets()
        if not names:
            return
        self._reload_benchmark_settings()
        dialog_config = get_instance_config(names[0])
        dialog = GridBenchmarkDialog(self, self.benchmark_settings, dialog_config)
        self.wait_window(dialog)
        plan = dialog.result
        if plan is None:
            return
        if not self._begin_benchmark_job(GRID_BENCHMARK_LABEL):
            active_label = self._benchmark_job_label or "Another benchmark"
            messagebox.showinfo("Benchmark already running", f"{active_label} is already in progress.")
            return
        self._grid_benchmark_active = True
        self._grid_benchmark_stop.clear()
        self._update_benchmark_controls()

        def action() -> str:
            completed = 0
            try:
                for name in names:
                    if self._grid_benchmark_stop.is_set():
                        break
                    self._set_active_benchmark_name(name)
                    original_config = get_instance_config(name)
                    state_before = list_instances().get(name)
                    was_running = (
                        state_before is not None
                        and state_before.status == InstanceStatus.RUNNING
                    )
                    started_by_grid = False
                    try:
                        restart_needed = plan_requires_restart(plan)
                        if not restart_needed:
                            started_by_grid = self._start_serial_benchmark_instance(name)
                        def restart_grid_runtime(
                            runtime_config: InstanceConfig,
                            current_name: str = name,
                        ) -> None:
                            restart_instance(current_name, config_override=runtime_config)

                        sweep = run_grid_for_instance(
                            original_config,
                            base_settings=load_benchmark_settings(self.project_root),
                            plan=plan,
                            should_stop=self._grid_benchmark_stop.is_set,
                            post_message=self._post_message,
                            restart_runtime=restart_grid_runtime,
                        )
                        runs = latest_grid_runs(sweep.sweep_id)
                        summary = write_grid_summary_artifact(
                            instance_name=name,
                            sweep_id=sweep.sweep_id,
                            runs=runs,
                        )
                        self._post_message(
                            f"[Grid benchmark] summary: {summary.relative_to(self.project_root)}"
                        )
                        completed += 1
                    finally:
                        self._set_active_benchmark_name(None)
                        if plan_requires_restart(plan):
                            if was_running:
                                restart_instance(name, config_override=original_config)
                            else:
                                stop_instance(name)
                        elif started_by_grid:
                            stop_instance(name)
                if self._grid_benchmark_stop.is_set():
                    return f"[Grid benchmark] stopped after {completed}/{len(names)} sweep(s)."
                return f"[Grid benchmark] finished {completed}/{len(names)} sweep(s)."
            finally:
                self._set_active_benchmark_name(None)
                self._grid_benchmark_active = False
                self._grid_benchmark_stop.clear()
                self._finish_benchmark_job()

        self._run_background(GRID_BENCHMARK_LABEL, action)

    def _stop_grid_benchmark(self) -> None:
        if not self._grid_benchmark_active:
            return
        self._grid_benchmark_stop.set()
        self._post_message("[Grid benchmark] stop requested.")
        self._update_benchmark_controls()

    def _set_benchmark_settings(self, **changes: int | float | str | bool | Path | None) -> None:
        self.benchmark_settings = replace(self.benchmark_settings, **changes)
        save_benchmark_settings(self.benchmark_settings, self.project_root)
        self.prompt_var.set(self.benchmark_settings.prompt_file.name)
        self._refresh_benchmark_params_menu()

    def _reload_benchmark_settings(self) -> None:
        self.benchmark_settings = load_benchmark_settings(self.project_root)
        self.prompt_var.set(self.benchmark_settings.prompt_file.name)
        self._refresh_benchmark_params_menu()

    def _refresh_benchmark_params_menu(self) -> None:
        if self.benchmark_params_menu is None:
            return
        menu = self.benchmark_params_menu
        menu.delete(0, tk.END)
        settings = self.benchmark_settings
        menu.add_command(
            label=f"Max tokens: {settings.max_tokens}",
            command=self._edit_benchmark_max_tokens,
        )
        menu.add_command(
            label=f"Endpoint: {'chat' if settings.endpoint == 'chat_completions' else 'completion'}",
            command=self._toggle_benchmark_endpoint,
        )
        menu.add_command(
            label=f"Temperature: {settings.temperature:g}",
            command=self._edit_benchmark_temperature,
        )
        menu.add_command(
            label=f"Top-p: {settings.top_p:g}" if settings.top_p is not None else "Top-p: default",
            command=lambda: self._edit_optional_float_setting(
                "Top-p",
                "top_p",
                minimum=0.0,
                maximum=1.0,
            ),
        )
        menu.add_command(
            label=f"Top-k: {settings.top_k}" if settings.top_k is not None else "Top-k: default",
            command=lambda: self._edit_optional_int_setting("Top-k", "top_k", minimum=0),
        )
        menu.add_command(
            label=(
                f"Repeat penalty: {settings.repeat_penalty:g}"
                if settings.repeat_penalty is not None
                else "Repeat penalty: default"
            ),
            command=lambda: self._edit_optional_float_setting(
                "Repeat penalty",
                "repeat_penalty",
                minimum=0.0,
            ),
        )
        menu.add_command(
            label=f"Seed: {settings.seed}" if settings.seed is not None else "Seed: default",
            command=lambda: self._edit_optional_int_setting("Seed", "seed", minimum=-1),
        )
        menu.add_command(
            label=f"Ignore EOS: {'on' if settings.ignore_eos else 'off'}",
            command=self._toggle_benchmark_ignore_eos,
        )
        menu.add_separator()
        menu.add_command(label="Reset defaults", command=self._reset_benchmark_params)
        menu.add_separator()
        menu.add_command(label="Open settings file", command=self._open_benchmark_settings_file)

    def _toggle_benchmark_endpoint(self) -> None:
        endpoint = (
            "completion"
            if self.benchmark_settings.endpoint == "chat_completions"
            else "chat_completions"
        )
        self._set_benchmark_settings(endpoint=endpoint)
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _toggle_benchmark_ignore_eos(self) -> None:
        self._set_benchmark_settings(ignore_eos=not self.benchmark_settings.ignore_eos)
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _edit_benchmark_max_tokens(self) -> None:
        value = simpledialog.askinteger(
            "Benchmark max tokens",
            "Maximum generated tokens",
            initialvalue=self.benchmark_settings.max_tokens,
            minvalue=1,
            parent=self,
        )
        if value is None:
            return
        self._set_benchmark_settings(max_tokens=value)
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _edit_benchmark_temperature(self) -> None:
        value = simpledialog.askfloat(
            "Benchmark temperature",
            "Temperature",
            initialvalue=self.benchmark_settings.temperature,
            minvalue=0.0,
            parent=self,
        )
        if value is None:
            return
        self._set_benchmark_settings(temperature=value)
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _edit_optional_int_setting(self, label: str, field: str, *, minimum: int) -> None:
        current = getattr(self.benchmark_settings, field)
        value = simpledialog.askstring(
            f"Benchmark {label}",
            f"{label} (blank = server default)",
            initialvalue="" if current is None else str(current),
            parent=self,
        )
        if value is None:
            return
        clean = value.strip()
        if not clean:
            self._set_benchmark_settings(**{field: None})
        else:
            try:
                parsed = int(clean)
            except ValueError:
                messagebox.showerror("Invalid value", f"{label} must be an integer.")
                return
            if parsed < minimum:
                messagebox.showerror("Invalid value", f"{label} must be at least {minimum}.")
                return
            self._set_benchmark_settings(**{field: parsed})
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _edit_optional_float_setting(
        self,
        label: str,
        field: str,
        *,
        minimum: float,
        maximum: float | None = None,
    ) -> None:
        current = getattr(self.benchmark_settings, field)
        value = simpledialog.askstring(
            f"Benchmark {label}",
            f"{label} (blank = server default)",
            initialvalue="" if current is None else f"{current:g}",
            parent=self,
        )
        if value is None:
            return
        clean = value.strip()
        if not clean:
            self._set_benchmark_settings(**{field: None})
        else:
            try:
                parsed = float(clean)
            except ValueError:
                messagebox.showerror("Invalid value", f"{label} must be a number.")
                return
            if parsed < minimum:
                messagebox.showerror("Invalid value", f"{label} must be at least {minimum:g}.")
                return
            if maximum is not None and parsed > maximum:
                messagebox.showerror("Invalid value", f"{label} must be at most {maximum:g}.")
                return
            self._set_benchmark_settings(**{field: parsed})
        self._post_message(
            f"Benchmark parameters updated: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _reset_benchmark_params(self) -> None:
        self._set_benchmark_settings(
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            top_p=None,
            top_k=None,
            repeat_penalty=None,
            seed=None,
            endpoint=DEFAULT_ENDPOINT,
            ignore_eos=False,
        )
        self._post_message(
            f"Benchmark parameters reset: {format_benchmark_settings_summary(self.benchmark_settings)}."
        )

    def _open_benchmark_settings_file(self) -> None:
        settings_path = save_benchmark_settings(self.benchmark_settings, self.project_root)
        self._open_path(settings_path)

    def _select_prompt_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select benchmark prompt",
            initialdir=str(self.benchmark_settings.prompt_file.parent),
            filetypes=(("Text and Markdown", "*.txt *.md"), ("All files", "*.*")),
        )
        if not path:
            return
        self._set_benchmark_settings(prompt_file=Path(path))
        self._post_message(f"Benchmark prompt set to {self.benchmark_settings.prompt_file.name}.")

    def _open_prompt_file(self) -> None:
        self._open_path(self.benchmark_settings.prompt_file)

    def _edit_args_inline(self, name: str, event: tk.Event) -> None:
        column = "args"
        display_columns = self.tree["displaycolumns"]
        columns = list(ALL_COLUMNS) if display_columns == "#all" else list(display_columns)
        if column not in columns:
            return
        column_id = f"#{columns.index(column) + 1}"
        bbox = self.tree.bbox(name, column_id)
        if not bbox:
            return
        x, y, width, height = bbox
        current = self.tree.set(name, column)
        if current == "-":
            current = ""

        editor = ttk.Entry(self.tree)
        editor.insert(0, current)
        editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        closed = {"value": False}

        def close(save: bool) -> None:
            if closed["value"]:
                return
            closed["value"] = True
            value = editor.get()
            editor.destroy()
            if save:
                self._save_runtime_args(name, value)

        editor.bind("<Return>", lambda _event: close(True))
        editor.bind("<Escape>", lambda _event: close(False))
        editor.bind("<FocusOut>", lambda _event: close(True))

    def _save_runtime_args(self, name: str, value: str) -> None:
        try:
            config = get_instance_config(name)
            config.args = shlex.split(value) if value.strip() else []
            save_config(config)
            state = list_instances().get(name)
        except Exception as exc:
            messagebox.showerror("Save runtime args failed", str(exc))
            self.refresh()
            return

        if state and state.status == InstanceStatus.RUNNING:
            self._run_background(
                f"restart {name}",
                lambda: f"Restarted {name} after runtime args edit (PID {restart_instance(name).pid}).",
            )
        else:
            self._post_message(f"Saved runtime args for {name}.")
            self.refresh()

    def _clone_selected(self) -> None:
        source = self._selected_instance()
        if not source:
            return
        try:
            config = get_instance_config(source)
            new_name = self._next_clone_name(source)
        except Exception as exc:
            messagebox.showerror("Clone failed", str(exc))
            return

        requested = simpledialog.askstring(
            "Clone row",
            "New instance name:",
            initialvalue=new_name,
            parent=self,
        )
        if not requested:
            return
        new_name = requested.strip().lower()
        if instance_alias_exists(new_name):
            messagebox.showerror("Clone failed", f"Instance '{new_name}' already exists.")
            return

        try:
            preferred_port = min(config.server.port + 1, 65535)
            suggested_port = suggest_port_for_instance(
                new_name,
                preferred_port=preferred_port,
                port_range=(preferred_port, 65535),
                host=config.server.host,
            )
            clone = config.model_copy(deep=True, update={"name": new_name})
            clone.server.port = suggested_port or preferred_port
            save_config(clone)
        except Exception as exc:
            messagebox.showerror("Clone failed", str(exc))
            return

        self._post_message(f"Cloned {source} to {new_name} on port {clone.server.port}.")
        self.refresh()

    def _next_clone_name(self, source: str) -> str:
        existing = {name for name, _ in discover_instances()}
        base = f"{source}_clone"
        if base not in existing:
            return base
        index = 2
        while f"{base}{index}" in existing:
            index += 1
        return f"{base}{index}"

    def _diff_selected(self) -> None:
        selected = self._selected_instances()
        if len(selected) != 2:
            messagebox.showinfo("Select two rows", "Select exactly two model rows for config diff.")
            return
        try:
            left = get_instance_config(selected[0])
            right = get_instance_config(selected[1])
        except Exception as exc:
            messagebox.showerror("Diff failed", str(exc))
            return

        left_args = [*left.args]
        right_args = [*right.args]
        diff = "\n".join(
            difflib.unified_diff(
                [arg + "\n" for arg in left_args],
                [arg + "\n" for arg in right_args],
                fromfile=left.name,
                tofile=right.name,
                lineterm="",
            )
        )
        self._open_diff_window(left.name, right.name, left_args, right_args, diff or "Runtime args are identical.")

    def _open_diff_window(
        self,
        left_name: str,
        right_name: str,
        left_args: list[str],
        right_args: list[str],
        diff: str,
    ) -> None:
        window = tk.Toplevel(self)
        window.title(f"Runtime args diff: {left_name} vs {right_name}")
        window.geometry("900x520")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(window, orient=tk.VERTICAL)
        panes.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        side_by_side = ttk.PanedWindow(panes, orient=tk.HORIZONTAL)
        panes.add(side_by_side, weight=2)
        for label, args in ((left_name, left_args), (right_name, right_args)):
            frame = ttk.Frame(side_by_side)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
            text = scrolledtext.ScrolledText(frame, height=10, wrap=tk.NONE)
            text.grid(row=1, column=0, sticky="nsew")
            text.insert(tk.END, "\n".join(args) or "(no runtime args)")
            text.configure(state=tk.DISABLED)
            side_by_side.add(frame, weight=1)

        diff_text = scrolledtext.ScrolledText(panes, height=10, wrap=tk.NONE)
        diff_text.insert(tk.END, diff)
        diff_text.configure(state=tk.DISABLED)
        panes.add(diff_text, weight=1)

    def _copy_cli_command(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        try:
            command = format_command(build_command(get_instance_config(name)))
        except Exception as exc:
            messagebox.showerror("Copy CLI failed", str(exc))
            return
        self.clipboard_clear()
        self.clipboard_append(command)
        self._post_message(f"Copied llama.cpp CLI command for {name}.")

    def _rename_display_name(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        try:
            config = get_instance_config(name)
        except Exception as exc:
            messagebox.showerror("Rename failed", str(exc))
            return

        requested = simpledialog.askstring(
            "Rename display name",
            "Display name:",
            initialvalue=config.display_name,
            parent=self,
        )
        if requested is None:
            return

        try:
            updated = update_instance_display_name(name, requested)
        except Exception as exc:
            messagebox.showerror("Rename failed", str(exc))
            return

        self._post_message(f"Updated display name for {name} to {updated.display_name}.")
        self.refresh()

    def _start_daemon(self) -> None:
        def action() -> str:
            status = get_daemon_status()
            if status.running:
                return f"Daemon already running (PID {status.pid})."
            start_daemon(foreground=False)
            return "Daemon started."

        self._run_background("start daemon", action)

    def _stop_daemon(self) -> None:
        def action() -> str:
            status = get_daemon_status()
            if not status.running:
                return "Daemon already stopped."
            if stop_daemon():
                return "Daemon stopped."
            raise RuntimeError("Daemon did not stop cleanly")

        self._run_background("stop daemon", action)

    def _export_config_to_vscode(self) -> None:
        """Export the selected instance's configuration as a VS Code-compatible JSON file."""
        name = self._selected_instance()
        if not name:
            return

        try:
            config = get_instance_config(name)
        except Exception as exc:
            messagebox.showerror("Export failed", f"Could not load config for {name}: {exc}")
            return

        # Build the export dict matching VS Code config.json schema
        export_data = {
            "name": config.name,
            "schema_version": config.schema_version,
            "instance_uid": config.instance_uid,
            "display_name": config.display_name,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

        # Binary config
        if config.binary is not None:
            export_data["binary"] = {
                "binary_id": str(config.binary.binary_id) if config.binary.binary_id else None,
                "version": config.binary.version,
                "variant": config.binary.variant,
                "source_url": str(config.binary.source_url) if config.binary.source_url else None,
                "sha256": config.binary.sha256,
            }

        # Model config
        export_data["model"] = {
            "path": str(config.model.path),
            "context_size": config.model.context_size,
            "batch_size": config.model.batch_size,
            "threads": config.model.threads,
        }

        # Server config
        export_data["server"] = {
            "host": config.server.host,
            "port": config.server.port,
            "timeout": config.server.timeout,
            "parallel": config.server.parallel,
        }

        # GPU config
        export_data["gpu"] = {
            "backend": config.gpu.backend,
            "device_id": config.gpu.device_id,
            "layers": config.gpu.layers,
        }

        # Environment variables
        export_data["env"] = config.env

        # CLI args
        export_data["args"] = config.args

        # Tags
        export_data["tags"] = config.tags

        # Healthcheck
        export_data["healthcheck"] = {
            "type": config.healthcheck.type,
            "path": config.healthcheck.path,
            "expected_status": config.healthcheck.expected_status,
            "expected_body": config.healthcheck.expected_body,
            "custom_script": config.healthcheck.custom_script,
            "interval": config.healthcheck.interval,
            "timeout": config.healthcheck.timeout,
            "retries": config.healthcheck.retries,
            "retry_delay": config.healthcheck.retry_delay,
            "start_period": config.healthcheck.start_period,
            "backoff_enabled": config.healthcheck.backoff_enabled,
            "backoff_base": config.healthcheck.backoff_base,
            "backoff_max": config.healthcheck.backoff_max,
            "backoff_jitter": config.healthcheck.backoff_jitter,
        }

        # Restart policy
        export_data["restart_policy"] = {
            "enabled": config.restart_policy.enabled,
            "max_retries": config.restart_policy.max_retries,
            "backoff_multiplier": config.restart_policy.backoff_multiplier,
            "initial_delay": config.restart_policy.initial_delay,
            "max_delay": config.restart_policy.max_delay,
        }

        # Logs config
        export_data["logs"] = {
            "stdout": config.logs.stdout,
            "stderr": config.logs.stderr,
            "max_size_mb": config.logs.max_size_mb,
            "rotation": config.logs.rotation,
        }

        # Open file dialog for save location
        default_filename = f"{name}_config.json"
        save_path = filedialog.asksaveasfilename(
            title=f"Export config for '{name}'",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=default_filename,
            initialdir=str(self.project_root),
        )

        if not save_path:
            self._post_message(f"Export cancelled for {name}.")
            return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=4, ensure_ascii=False)
            self._post_message(f"Exported config for {name} to {save_path}.")
        except OSError as exc:
            messagebox.showerror("Export failed", f"Could not save file: {exc}")

    def _open_config(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        try:
            path = resolve_instance_config_path(get_instance_config(name), self.project_root)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self._open_path(path)

    def _open_config_folder(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        try:
            path = resolve_instance_config_dir(get_instance_config(name), self.project_root)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self._open_path(path)

    def _open_logs(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        self._open_path(self.project_root / "logs" / name)

    def _open_project(self) -> None:
        self._open_path(self.project_root)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showwarning("Path not found", str(path))
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Open failed", str(exc))

    def _open_add_dialog(self) -> None:
        AddModelDialog(self, on_saved=self._on_model_saved)

    def _on_model_saved(self, config: InstanceConfig) -> None:
        self._post_message(f"Configured model instance {config.name}.")
        self.refresh()

    def _open_binary_dialog(self) -> None:
        InstallBinaryDialog(self, on_install=self._install_binary)

    def _check_vulkan_binary(self) -> None:
        try:
            from llama_orchestrator.binaries import BinaryManager

            manager = BinaryManager(self.project_root)
            installed = [
                binary for binary in manager.list_installed()
                if binary.variant == VULKAN_VARIANT
            ]
        except Exception as exc:
            self._post_message(f"Could not inspect installed binaries: {exc}")
            return

        if not installed:
            self._post_message(VULKAN_BINARY_MISSING_MESSAGE)

    def _install_binary(self, version: str, variant: str, set_default: bool) -> None:
        def action() -> str:
            from llama_orchestrator.binaries import BinaryManager

            manager = BinaryManager(self.project_root)
            binary = manager.install(
                version=version,
                variant=variant,  # type: ignore[arg-type]
                set_as_default=set_default,
            )
            return (
                f"Installed llama-server {binary.version} ({binary.variant}) "
                f"as {binary.id}."
            )

        self._run_background(f"install {variant} binary", action)

    def _apply_default_args(self) -> None:
        name = self._selected_instance()
        if not name:
            return

        try:
            config = get_instance_config(name)
            config.args = apply_managed_runtime_args(
                list(config.args),
                no_mmproj=True,
                reasoning="off",
                flash_attn="auto",
            )
            if (
                config.gpu.backend == "vulkan"
                and (config.binary is None or config.binary.variant != VULKAN_VARIANT)
            ):
                config.binary = BinaryConfig(version="latest", variant=VULKAN_VARIANT)
            save_config(config)
        except Exception as exc:
            messagebox.showerror("Apply args failed", str(exc))
            return

        self._post_message(
            f"Applied runtime args to {name}: {' '.join(DEFAULT_RUNTIME_ARGS)}"
        )
        self.refresh()


class AddModelDialog(tk.Toplevel):
    """Dialog for creating a model instance config."""

    def __init__(
        self,
        master: LlamaOrchestratorGui,
        on_saved: Callable[[InstanceConfig], None],
    ) -> None:
        super().__init__(master)
        self.title("Add model")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved

        self.name_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.port_var = tk.StringVar(value="8001")
        self.backend_var = tk.StringVar(value="vulkan")
        self.device_var = tk.StringVar(value="0")
        self.layers_var = tk.StringVar(value="0")
        self.context_var = tk.StringVar(value="4096")
        self.threads_var = tk.StringVar(value="8")
        self.tags_var = tk.StringVar()
        self.no_mmproj_var = tk.BooleanVar(value=True)
        self.reasoning_var = tk.StringVar(value="off")
        self.flash_attn_var = tk.StringVar(value="auto")
        self._hf_selection: ImportedModelSelection | None = None

        self._build()
        self.name_entry.focus_set()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        self.name_entry = self._entry(frame, "Name", self.name_var, 0)
        model_entry = self._entry(frame, "GGUF model", self.model_var, 1, width=54)
        ttk.Button(frame, text="Browse", command=self._browse_model).grid(row=1, column=2, padx=(6, 0))
        ttk.Button(
            frame,
            text="Import from Hugging Face...",
            command=self._open_hf_import_dialog,
        ).grid(row=2, column=1, sticky="w", pady=(0, 4))
        model_entry.focus_set()

        self._entry(frame, "Port", self.port_var, 3)
        backend = ttk.Combobox(
            frame,
            textvariable=self.backend_var,
            values=("cpu", "vulkan", "cuda", "hip", "metal"),
            state="readonly",
            width=18,
        )
        ttk.Label(frame, text="Backend").grid(row=4, column=0, sticky="w", pady=4)
        backend.grid(row=4, column=1, sticky="w", pady=4)

        self._entry(frame, "Device ID", self.device_var, 5)
        self._entry(frame, "GPU layers", self.layers_var, 6)
        self._entry(frame, "Context", self.context_var, 7)
        self._entry(frame, "Threads", self.threads_var, 8)
        self._entry(frame, "Tags", self.tags_var, 9)

        ttk.Checkbutton(
            frame,
            text="--no-mmproj",
            variable=self.no_mmproj_var,
        ).grid(row=10, column=1, sticky="w", pady=4)

        reasoning = ttk.Combobox(
            frame,
            textvariable=self.reasoning_var,
            values=("off", "auto"),
            width=18,
        )
        ttk.Label(frame, text="--reasoning").grid(row=11, column=0, sticky="w", pady=4)
        reasoning.grid(row=11, column=1, sticky="w", pady=4)

        flash_attn = ttk.Combobox(
            frame,
            textvariable=self.flash_attn_var,
            values=("auto", "on", "off"),
            width=18,
        )
        ttk.Label(frame, text="--flash-attn").grid(row=12, column=0, sticky="w", pady=4)
        flash_attn.grid(row=12, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=13, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

    def _entry(
        self,
        frame: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        row: int,
        width: int = 24,
    ) -> ttk.Entry:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        return entry

    def _browse_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Select GGUF model",
            filetypes=(("GGUF models", "*.gguf"), ("All files", "*.*")),
        )
        if path:
            self.model_var.set(path)

    def _open_hf_import_dialog(self) -> None:
        HuggingFaceImportDialog(self, on_use=self._apply_hf_import_selection)

    def _apply_hf_import_selection(self, selection: ImportedModelSelection) -> None:
        name, model_path, tags = build_add_model_prefill(selection)
        self.name_var.set(name)
        self.model_var.set(model_path)
        self._hf_selection = selection
        merged_tags = parse_tag_string(
            " ".join([self.tags_var.get().strip(), *tags]).strip()
        )
        self.tags_var.set(", ".join(merged_tags))

    def _save(self) -> None:
        try:
            model_path = normalize_model_path_for_config(Path(self.model_var.get().strip()))
            config = InstanceConfig(
                name=self.name_var.get().strip(),
                binary=BinaryConfig(version="latest", variant=VULKAN_VARIANT)
                if self.backend_var.get() == "vulkan"
                else None,
                model=ModelConfig(
                    path=model_path,
                    context_size=int(self.context_var.get()),
                    threads=int(self.threads_var.get()),
                ),
                server=ServerConfig(port=int(self.port_var.get())),
                gpu=GpuConfig(
                    backend=self.backend_var.get(),  # type: ignore[arg-type]
                    device_id=int(self.device_var.get()),
                    layers=int(self.layers_var.get()),
                ),
                args=apply_managed_runtime_args(
                    [],
                    no_mmproj=self.no_mmproj_var.get(),
                    reasoning=self.reasoning_var.get().strip(),
                    flash_attn=self.flash_attn_var.get().strip(),
                ),
                tags=parse_tag_string(self.tags_var.get()),
            )
            try:
                config.model_metadata = build_model_metadata(config, imported_selection=self._hf_selection)
            except Exception:
                # Metadata must stay optional and never block profile creation.
                config.model_metadata = None
            if instance_alias_exists(config.name):
                raise ValueError(f"Instance '{config.name}' already exists")
            save_config(config)
        except Exception as exc:
            messagebox.showerror("Invalid model config", str(exc))
            return

        self.on_saved(config)
        self.destroy()


class ExistingModelFileDialog(tk.Toplevel):
    """Prompt for how to handle an already-downloaded GGUF file."""

    def __init__(self, master: tk.Misc, final_path: Path) -> None:
        super().__init__(master)
        self.title("Model already exists")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result = "cancel"

        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame,
            text=(
                "The selected GGUF file already exists locally:\n"
                f"{final_path}\n\nChoose how to continue."
            ),
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Button(frame, text="Use existing", command=self._use_existing).grid(
            row=1, column=0, padx=(0, 8), pady=(12, 0)
        )
        ttk.Button(frame, text="Re-download", command=self._redownload).grid(
            row=1, column=1, padx=(0, 8), pady=(12, 0)
        )
        ttk.Button(frame, text="Cancel", command=self._cancel).grid(row=1, column=2, pady=(12, 0))
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    @classmethod
    def ask(cls, master: tk.Misc, final_path: Path) -> str:
        dialog = cls(master, final_path)
        dialog.wait_window(dialog)
        return dialog.result

    def _use_existing(self) -> None:
        self.result = "use_existing"
        self.destroy()

    def _redownload(self) -> None:
        self.result = "redownload"
        self.destroy()

    def _cancel(self) -> None:
        self.result = "cancel"
        self.destroy()


class HuggingFaceImportDialog(tk.Toplevel):
    """Import a GGUF model from Hugging Face and prefill Add Model fields."""

    def __init__(
        self,
        master: AddModelDialog,
        on_use: Callable[[ImportedModelSelection], None],
    ) -> None:
        super().__init__(master)
        self.title("Import model from Hugging Face")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.on_use = on_use
        self.token_store = HuggingFaceTokenStore()
        self.repo_ref: HuggingFaceRepoRef | None = None
        self.variants: list[GGUFVariant] = []
        self.selected_model: ImportedModelSelection | None = None
        self._events: queue.Queue[ImportDialogEvent] = queue.Queue()
        self._cancel_event: threading.Event | None = None
        self._event_pump_id: str | None = None
        self._busy = False

        settings = load_import_settings()
        self.model_ref_var = tk.StringVar()
        self.local_models_dir_var = tk.StringVar(value=settings.local_models_directory)
        self.token_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Paste a Hugging Face model URL or owner/repo ID.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.local_models_dir_var.trace_add("write", self._persist_local_models_dir)

        self._build()
        self._refresh_token_status()
        self.protocol("WM_DELETE_WINDOW", self._cancel_or_close)
        self._schedule_pump()

    def destroy(self) -> None:
        if self._event_pump_id is not None:
            with suppress(tk.TclError):
                self.after_cancel(self._event_pump_id)
            self._event_pump_id = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().destroy()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(frame, text="Model URL or ID").grid(row=0, column=0, sticky="w", pady=4)
        ref_entry = ttk.Entry(frame, textvariable=self.model_ref_var, width=64)
        ref_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.load_button = ttk.Button(frame, text="Load variants", command=self._load_variants)
        self.load_button.grid(row=0, column=2, padx=(8, 0), pady=4)

        token_row = ttk.Frame(frame)
        token_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(token_row, textvariable=self.token_status_var).pack(side=tk.LEFT)
        self.configure_token_button = ttk.Button(token_row, text="Set token...", command=self._configure_token)
        self.configure_token_button.pack(side=tk.LEFT, padx=(10, 6))
        self.remove_token_button = ttk.Button(token_row, text="Remove token", command=self._remove_token)
        self.remove_token_button.pack(side=tk.LEFT)

        columns = ("filename", "size", "quant", "status", "note")
        self.variant_tree = ttk.Treeview(frame, columns=columns, show="headings", height=9)
        headings = {
            "filename": "Filename",
            "size": "Size",
            "quant": "Quantization",
            "status": "Local status",
            "note": "Note",
        }
        widths = {"filename": 320, "size": 90, "quant": 110, "status": 110, "note": 180}
        for column in columns:
            self.variant_tree.heading(column, text=headings[column])
            self.variant_tree.column(column, width=widths[column], anchor="w")
        self.variant_tree.grid(row=2, column=0, columnspan=3, sticky="nsew")
        self.variant_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_actions())
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.variant_tree.yview)
        scrollbar.grid(row=2, column=3, sticky="ns")
        self.variant_tree.configure(yscrollcommand=scrollbar.set)

        ttk.Label(frame, text="Local models directory").grid(row=3, column=0, sticky="w", pady=4)
        self.models_dir_entry = ttk.Entry(frame, textvariable=self.local_models_dir_var, width=64)
        self.models_dir_entry.grid(row=3, column=1, sticky="ew", pady=4)
        self.browse_button = ttk.Button(frame, text="Browse", command=self._browse_models_dir)
        self.browse_button.grid(row=3, column=2, padx=(8, 0), pady=4)

        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(frame, textvariable=self.status_var, wraplength=720, justify=tk.LEFT).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self.download_button = ttk.Button(buttons, text="Download selected", command=self._download_selected)
        self.download_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.use_button = ttk.Button(buttons, text="Use downloaded model", command=self._use_downloaded_model)
        self.use_button.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel_or_close).pack(side=tk.RIGHT)

        self._update_actions()
        ref_entry.focus_set()

    def _persist_local_models_dir(self, *_args: object) -> None:
        directory = self.local_models_dir_var.get().strip()
        if not directory:
            return
        save_import_settings(ImportSettings(local_models_directory=directory))

    def _schedule_pump(self) -> None:
        self._event_pump_id = self.after(100, self._pump_events)

    def _resolve_models_dir(self) -> Path:
        directory = self.local_models_dir_var.get().strip()
        if directory:
            return resolve_models_directory_input(directory)
        settings = load_import_settings()
        if settings.local_models_directory:
            return resolve_models_directory_input(settings.local_models_directory)
        return resolve_models_directory_input("")

    def _refresh_token_status(self) -> None:
        configured = self.token_store.is_configured()
        self.token_status_var.set(
            "Hugging Face token: configured" if configured else "Hugging Face token: not configured"
        )
        self.remove_token_button.configure(state=tk.NORMAL if configured else tk.DISABLED)

    def _browse_models_dir(self) -> None:
        initial_dir = self.local_models_dir_var.get().strip() or str(get_project_root() / "models")
        selected = filedialog.askdirectory(title="Select models directory", initialdir=initial_dir)
        if selected:
            self.local_models_dir_var.set(selected)

    def _configure_token(self) -> None:
        token = simpledialog.askstring(
            "Hugging Face token",
            "Enter a Hugging Face read token for gated or private models.",
            parent=self,
            show="*",
        )
        if token is None:
            return
        self.status_var.set("Validating Hugging Face token...")
        self._set_busy(True)

        def worker() -> None:
            try:
                self.token_store.save_token(token)
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(ImportDialogEvent(kind="token_saved", message="Hugging Face token saved."))
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _remove_token(self) -> None:
        self.token_store.remove_token()
        self._refresh_token_status()
        self.status_var.set("Hugging Face token removed.")

    def _load_variants(self) -> None:
        if self._busy:
            return
        model_ref = self.model_ref_var.get().strip()
        try:
            normalize_hf_model_reference(model_ref)
        except HuggingFaceImportError as exc:
            messagebox.showerror("Hugging Face import", str(exc), parent=self)
            return
        models_dir = self._resolve_models_dir()
        token = self.token_store.get_token()

        self.repo_ref = None
        self.variants = []
        self.selected_model = None
        self._render_variants()
        self.progress_var.set(0.0)
        self.status_var.set("Loading GGUF variants from Hugging Face...")
        self._set_busy(True)

        def worker() -> None:
            try:
                repo_ref, variants = list_gguf_variants(
                    model_ref,
                    local_models_dir=models_dir,
                    token=token,
                )
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(
                    ImportDialogEvent(
                        kind="variants_loaded",
                        repo_ref=repo_ref,
                        variants=variants,
                        message=f"Loaded {len(variants)} GGUF variant(s).",
                    )
                )
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _download_selected(self) -> None:
        if self._busy:
            return
        variant = self._selected_variant()
        if variant is None or self.repo_ref is None:
            messagebox.showerror("Hugging Face import", "Select one GGUF variant first.", parent=self)
            return
        models_dir = self._resolve_models_dir()
        token = self.token_store.get_token()

        existing_choice: str | None = None
        try:
            plan_download_target(variant.local_path)
        except HuggingFaceImportError:
            existing_choice = ExistingModelFileDialog.ask(self, variant.local_path)
        if existing_choice == "cancel":
            self.status_var.set("Download cancelled before it started.")
            return
        if existing_choice == "use_existing":
            selection = ImportedModelSelection(
                repo_id=self.repo_ref.repo_id,
                filename=variant.filename,
                local_path=variant.local_path,
                quantization=variant.quantization or parse_gguf_quantization(variant.filename),
                size_bytes=variant.size_bytes,
            )
            try:
                self.selected_model = write_import_metadata_sidecar(
                    selection,
                    token=token,
                    model_card_text="",
                )
            except HuggingFaceImportError as exc:
                messagebox.showerror("Hugging Face import", str(exc), parent=self)
                return
            self._set_variant_status(variant.filename, "downloaded")
            self.status_var.set(f"Using existing local model: {variant.local_path}")
            self._update_actions()
            return

        self.selected_model = None
        self.progress_var.set(0.0)
        self.status_var.set(f"Starting download for {Path(variant.filename).name}...")
        self._set_variant_status(variant.filename, "downloading")
        self._set_busy(True)
        self._cancel_event = threading.Event()

        def worker() -> None:
            try:
                selection = download_gguf_variant(
                    self.repo_ref.repo_id,
                    variant.filename,
                    models_dir,
                    token=token,
                    size_bytes=variant.size_bytes,
                    existing_choice=existing_choice,  # type: ignore[arg-type]
                    progress_callback=lambda progress: self._events.put(
                        ImportDialogEvent(kind="progress", progress=progress)
                    ),
                    cancel_check=lambda: self._cancel_event.is_set() if self._cancel_event else False,
                )
            except DownloadCancelledError as exc:
                self._events.put(ImportDialogEvent(kind="cancelled", message=str(exc)))
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(
                    ImportDialogEvent(
                        kind="download_complete",
                        selection=selection,
                        message=(
                            f"Downloaded {Path(selection.filename).name} to {selection.local_path}. "
                            "Click Use downloaded model to fill Add model."
                        ),
                    )
                )
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _use_downloaded_model(self) -> None:
        if self.selected_model is None:
            variant = self._selected_variant()
            if variant is None or self.repo_ref is None or variant.local_status != "downloaded":
                messagebox.showerror(
                    "Hugging Face import",
                    "Download or reuse one GGUF variant first.",
                    parent=self,
                )
                return
            selection = ImportedModelSelection(
                repo_id=self.repo_ref.repo_id,
                filename=variant.filename,
                local_path=variant.local_path,
                quantization=variant.quantization or parse_gguf_quantization(variant.filename),
                size_bytes=variant.size_bytes,
            )
            try:
                self.selected_model = write_import_metadata_sidecar(
                    selection,
                    token=self.token_store.get_token(),
                    model_card_text="",
                )
            except HuggingFaceImportError as exc:
                messagebox.showerror("Hugging Face import", str(exc), parent=self)
                return
        self.on_use(self.selected_model)
        self.destroy()

    def _cancel_or_close(self) -> None:
        if self._busy and self._cancel_event is not None:
            self._cancel_event.set()
            self.status_var.set("Cancelling download...")
            return
        self.destroy()

    def _pump_events(self) -> None:
        self._event_pump_id = None
        if not self.winfo_exists():
            return
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "idle":
                self._cancel_event = None
                self._set_busy(False)
            elif event.kind == "variants_loaded":
                self.repo_ref = event.repo_ref
                self.variants = event.variants or []
                self._render_variants(preferred_filename=self.repo_ref.filename if self.repo_ref else None)
                self.status_var.set(event.message)
            elif event.kind == "progress" and event.progress is not None:
                progress = event.progress
                if progress.total_bytes:
                    self.progress_var.set((progress.downloaded_bytes / progress.total_bytes) * 100)
                else:
                    self.progress_var.set(0.0)
                self.status_var.set(format_download_progress(progress))
            elif event.kind == "download_complete" and event.selection is not None:
                self.selected_model = event.selection
                self.progress_var.set(100.0)
                self._set_variant_status(event.selection.filename, "downloaded")
                self.status_var.set(event.message)
            elif event.kind == "token_saved":
                self._refresh_token_status()
                self.status_var.set(event.message)
            elif event.kind == "cancelled":
                self.selected_model = None
                variant = self._selected_variant()
                if variant is not None:
                    self._set_variant_status(variant.filename, "not downloaded")
                self.progress_var.set(0.0)
                self.status_var.set(event.message)
            elif event.kind == "error":
                self.selected_model = None
                variant = self._selected_variant()
                if variant is not None and variant.local_status == "downloading":
                    self._set_variant_status(variant.filename, "not downloaded")
                self.progress_var.set(0.0)
                self.status_var.set(event.message)
                messagebox.showerror("Hugging Face import", event.message, parent=self)
            self._update_actions()
        if self.winfo_exists():
            self._schedule_pump()

    def _render_variants(self, preferred_filename: str | None = None) -> None:
        selected = self.variant_tree.selection()
        selected_name = selected[0] if selected else preferred_filename
        for item in self.variant_tree.get_children():
            self.variant_tree.delete(item)
        for variant in self.variants:
            self.variant_tree.insert(
                "",
                tk.END,
                iid=variant.filename,
                values=(
                    variant.filename,
                    format_model_size_gb(variant.size_gb),
                    variant.quantization or "-",
                    variant.local_status,
                    variant.note or "-",
                ),
            )
        if selected_name and self.variant_tree.exists(selected_name):
            self.variant_tree.selection_set(selected_name)
            self.variant_tree.focus(selected_name)
        self._update_actions()

    def _selected_variant(self) -> GGUFVariant | None:
        selection = self.variant_tree.selection()
        if not selection:
            return None
        filename = selection[0]
        for variant in self.variants:
            if variant.filename == filename:
                return variant
        return None

    def _set_variant_status(self, filename: str, status: str) -> None:
        updated: list[GGUFVariant] = []
        for variant in self.variants:
            if variant.filename == filename:
                updated.append(
                    GGUFVariant(
                        filename=variant.filename,
                        size_bytes=variant.size_bytes,
                        quantization=variant.quantization,
                        local_path=variant.local_path,
                        local_status=status,  # type: ignore[arg-type]
                        note=variant.note,
                    )
                )
            else:
                updated.append(variant)
        self.variants = updated
        self._render_variants(preferred_filename=filename)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.load_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.configure_token_button.configure(state=state)
        self.remove_token_button.configure(
            state=(tk.DISABLED if busy else (tk.NORMAL if self.token_store.is_configured() else tk.DISABLED))
        )
        self._update_actions()

    def _update_actions(self) -> None:
        if self._busy:
            self.download_button.configure(state=tk.DISABLED)
            self.use_button.configure(state=tk.DISABLED)
            return
        variant = self._selected_variant()
        self.download_button.configure(state=tk.NORMAL if variant is not None else tk.DISABLED)
        can_use = self.selected_model is not None or (
            variant is not None and variant.local_status == "downloaded"
        )
        self.use_button.configure(state=tk.NORMAL if can_use else tk.DISABLED)


class InstallBinaryDialog(tk.Toplevel):
    """Dialog for installing llama.cpp binaries from GitHub releases."""

    def __init__(
        self,
        master: LlamaOrchestratorGui,
        on_install: Callable[[str, str, bool], None],
    ) -> None:
        super().__init__(master)
        self.title("Install llama-server")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_install = on_install

        self.version_var = tk.StringVar(value="latest")
        self.variant_var = tk.StringVar(value=VULKAN_VARIANT)
        self.default_var = tk.BooleanVar(value=True)

        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Version").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.version_var, width=24).grid(
            row=0,
            column=1,
            sticky="w",
            pady=4,
        )

        ttk.Label(frame, text="Variant").grid(row=1, column=0, sticky="w", pady=4)
        variant = ttk.Combobox(
            frame,
            textvariable=self.variant_var,
            values=(
                "win-vulkan-x64",
                "win-cpu-x64",
                "win-cuda-12.4-x64",
                "win-cuda-13.1-x64",
                "win-hip-radeon-x64",
                "win-sycl-x64",
            ),
            state="readonly",
            width=24,
        )
        variant.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Checkbutton(
            frame,
            text="Set as default binary",
            variable=self.default_var,
        ).grid(row=2, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Install", command=self._install).pack(side=tk.RIGHT, padx=(0, 8))

    def _install(self) -> None:
        version = self.version_var.get().strip() or "latest"
        variant = self.variant_var.get().strip() or VULKAN_VARIANT
        self.on_install(version, variant, self.default_var.get())
        self.destroy()


def launch_gui() -> None:
    """Launch the desktop GUI."""
    app = LlamaOrchestratorGui()
    app.mainloop()
