"""Grid benchmark parameter dialog.

Extracted from app.py to reduce context fill during independent refactoring.
"""

from __future__ import annotations

from tkinter import messagebox, ttk

import tkinter as tk

from llama_orchestrator.benchmark import BenchmarkSettings
from llama_orchestrator.benchmark_grid import (
    DEFAULT_KV_CACHE_PROFILE_IDS,
    GridParameterRange,
    GridPlan,
    KV_CACHE_PARAMETER_NAME,
    format_grid_plan_preview,
    grid_parameter_catalog,
    load_grid_plan,
    save_grid_plan,
)
from llama_orchestrator.config import InstanceConfig
from llama_orchestrator.gui.grid_dialogs import (
    GridDialogParameterVars,
    _default_grid_bound,
    _default_grid_values_for_spec,
    _default_step_or_values,
    _format_grid_values,
    _grid_dialog_status,
    _grid_spec_label,
    format_kv_cache_profile_summary,
    parse_grid_number,
    parse_grid_values,
)
from llama_orchestrator.gui.kv_cache_dialogs import KvCacheProfileDialog

GRID_BENCHMARK_LABEL = "Grid benchmark"


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
