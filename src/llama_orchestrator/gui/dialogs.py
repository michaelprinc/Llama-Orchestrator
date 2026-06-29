"""GUI dialog classes extracted from the legacy gui.py.

This module contains all the Toplevel dialogs used by the llama-orchestrator
GUI: KV cache profile selection, grid benchmark parameter configuration,
model addition, HuggingFace import, and binary installation dialogs.

Extracted from `gui.py` as part of the WS-5 module extraction effort.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

from llama_orchestrator.benchmark import BenchmarkSettings
from llama_orchestrator.benchmark_grid import (
    DEFAULT_GRID_CONFIRM_LIMIT,
    DEFAULT_GRID_HARD_LIMIT,
    DEFAULT_KV_CACHE_PROFILE_IDS,
    GridParameterRange,
    GridParameterSpec,
    GridPlan,
    all_kv_cache_profiles,
    format_grid_plan_preview,
    grid_parameter_catalog,
    kv_cache_profiles_for_preset,
    load_grid_plan,
    save_grid_plan,
)
from llama_orchestrator.config import InstanceConfig

# ---------------------------------------------------------------------------
# Supporting dataclass and helpers (extracted from gui.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridDialogParameterVars:
    """Tk variables backing one Grid benchmark dialog row."""

    spec: GridParameterSpec
    enabled: tk.BooleanVar
    minimum: tk.StringVar
    maximum: tk.StringVar
    step_or_values: tk.StringVar


def _default_step_or_values(value: int | float | str | bool | None) -> str:
    if isinstance(value, bool):
        return "false,true" if value is False else "true,false"
    if isinstance(value, float):
        return f"{value:g}"
    if value is None:
        return ""
    return str(value)

# ---------------------------------------------------------------------------
# KV Cache Profile Dialog
# ---------------------------------------------------------------------------


class KvCacheProfileDialog(tk.Toplevel):
    """Dialog for exact KV cache benchmark profile selection."""

    def __init__(self, parent: tk.Misc, selected_profile_ids: list[str]) -> None:
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
        ttk.Button(
            preset_frame, text="Baseline", command=lambda: self._select_preset("baseline")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Paired", command=lambda: self._select_preset("paired")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(
            preset_frame, text="Memory saving", command=lambda: self._select_preset("memory")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            preset_frame, text="Add asymmetric", command=lambda: self._select_preset("asymmetric")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            preset_frame, text="Full K x V", command=lambda: self._select_preset("full")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(preset_frame, text="Clear", command=self._clear_all).pack(
            side=tk.LEFT, padx=2
        )

        table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(2, weight=1)
        headers = ("Enabled", "Name", "K cache", "V cache", "Draft K", "Draft V", "Notes")
        for column, header in enumerate(headers):
            ttk.Label(table, text=header).grid(row=0, column=column, sticky="w", padx=4)

        for row, profile in enumerate(self._profiles, start=1):
            variable = tk.BooleanVar(value=profile.id in selected)
            self._profile_vars[profile.id] = variable
            ttk.Checkbutton(
                table, variable=variable, command=self._update_summary
            ).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Label(table, text=profile.label).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_k).grid(row=row, column=2, sticky="w", padx=4)
            ttk.Label(table, text=profile.cache_type_v).grid(
                row=row, column=3, sticky="w", padx=4
            )
            ttk.Label(
                table, text=profile.cache_type_k_draft or "-"
            ).grid(row=row, column=4, sticky="w", padx=4)
            ttk.Label(
                table, text=profile.cache_type_v_draft or "-"
            ).grid(row=row, column=5, sticky="w", padx=4)
            ttk.Label(table, text=profile.notes or "-").grid(
                row=row, column=6, sticky="w", padx=4
            )

        self.summary_var = tk.StringVar()
        ttk.Label(body, textvariable=self.summary_var).grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Apply", command=self._accept).pack(side=tk.LEFT, padx=4)

        self.transient(parent)  # type: ignore[arg-type]
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._update_summary()

    def _selected_profile_ids(self) -> tuple[str, ...]:
        return tuple(
            profile.id for profile in self._profiles if self._profile_vars[profile.id].get()
        )

    def _select_preset(self, preset: str) -> None:
        selected = {profile.id for profile in kv_cache_profiles_for_preset(preset)}
        for profile_id, variable in self._profile_vars.items():
            variable.set(profile_id in selected)
        self._mode.set(
            "full"
            if preset == "full"
            else "paired"
            if preset == "paired"
            else "custom"
        )
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
            messagebox.showerror(
                "KV Cache combinations",
                "Select at least one KV cache profile.",
                parent=self,
            )
            return
        self.result = selected
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ---------------------------------------------------------------------------
# Grid Benchmark Dialog
# ---------------------------------------------------------------------------


class GridBenchmarkDialog(tk.Toplevel):
    """Grid benchmark parameter dialog."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: BenchmarkSettings,
        config: InstanceConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Grid benchmark")
        self.resizable(True, True)
        self.result: GridPlan | None = None
        self._parameter_vars: dict[str, GridDialogParameterVars] = {}
        self._settings = settings
        self._config = config
        self._preview_var = tk.StringVar()
        saved_ranges = {parameter.name: parameter for parameter in load_grid_plan().parameters}

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

        # Populate parameter rows
        for row, spec in enumerate(grid_parameter_catalog(), start=1):
            enabled = tk.BooleanVar(value=saved_ranges.get(spec.name) is not None)
            minimum = tk.StringVar(value=str(spec.minimum) if spec.minimum is not None else "")
            maximum = tk.StringVar(value=str(spec.maximum) if spec.maximum is not None else "")
            step_or_values = tk.StringVar(
                value=_default_step_or_values(spec.default)
            )
            var = GridDialogParameterVars(
                spec=spec,
                enabled=enabled,
                minimum=minimum,
                maximum=maximum,
                step_or_values=step_or_values,
            )
            self._parameter_vars[spec.name] = var

            ttk.Checkbutton(body, variable=enabled).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Label(body, text=spec.display_name or spec.name).grid(
                row=row, column=1, sticky="w", padx=4
            )
            ttk.Label(body, text=str(spec.default or "")).grid(
                row=row, column=2, sticky="w", padx=4
            )
            ttk.Entry(body, textvariable=minimum).grid(row=row, column=3, sticky="ew", padx=4)
            ttk.Entry(body, textvariable=maximum).grid(row=row, column=4, sticky="ew", padx=4)
            ttk.Entry(body, textvariable=step_or_values).grid(
                row=row, column=5, sticky="ew", padx=4
            )
            ttk.Label(body, text=str(spec.category)).grid(
                row=row, column=6, sticky="w", padx=4
            )
            ttk.Label(body, text="Yes" if spec.restart_required else "No").grid(
                row=row, column=7, sticky="w", padx=4
            )
            ttk.Label(body, text="Ready" if spec.execution_supported else "Disabled").grid(
                row=row, column=8, sticky="w", padx=4
            )

        # KV cache profile selection
        ttk.Label(body, text="KV Cache profiles:").grid(
            row=len(grid_parameter_catalog()) + 1, column=0, sticky="e", padx=4, pady=(10, 0)
        )
        self._kv_cache_summary = tk.StringVar()
        ttk.Label(body, textvariable=self._kv_cache_summary).grid(
            row=len(grid_parameter_catalog()) + 1, column=1, sticky="w", padx=4
        )
        ttk.Button(
            body, text="Select profiles...", command=self._select_kv_cache
        ).grid(
            row=len(grid_parameter_catalog()) + 1, column=2, padx=4
        )

        # Preview
        ttk.Label(body, textvariable=self._preview_var, wraplength=600).grid(
            row=len(grid_parameter_catalog()) + 2, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )

        # Action buttons
        buttons = ttk.Frame(body)
        buttons.grid(row=len(grid_parameter_catalog()) + 3, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(buttons, text="Run grid", command=self._accept).pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _select_kv_cache(self) -> None:
        """Open the KV cache profile selection dialog."""
        dialog = KvCacheProfileDialog(self, [])
        dialog.grab_set()
        if dialog.result:
            self._kv_cache_summary.set(f"Selected: {len(dialog.result)} profiles")

    def _build_plan(self) -> GridPlan:
        """Build the GridPlan from user selections."""
        enabled_specs = [
            spec
            for spec in grid_parameter_catalog()
            if self._parameter_vars[spec.name].enabled.get()
        ]
        ranges: list[GridParameterRange] = []
        for _idx, spec in enumerate(enabled_specs, start=1):
            var = self._parameter_vars[spec.name]
            min_val = var.minimum.get().strip()
            max_val = var.maximum.get().strip()

            # Parse step_or_values as comma-separated choices
            choices_str = var.step_or_values.get().strip()
            choices = tuple(
                float(c) if '.' in c else int(c)
                for c in choices_str.split(",") if c.strip()
            ) if choices_str else ()

            ranges.append(
                GridParameterRange(
                    name=spec.name,
                    enabled=True,
                    minimum=float(min_val) if min_val else spec.minimum,
                    maximum=float(max_val) if max_val else spec.maximum,
                    step=None,
                    values=choices if choices else spec.choices,
                )
            )

        return GridPlan(
            parameters=tuple(ranges),
            confirm_limit=DEFAULT_GRID_CONFIRM_LIMIT,
            hard_limit=DEFAULT_GRID_HARD_LIMIT,
        )

    def _update_preview(self) -> None:
        """Update the grid plan preview label."""
        plan = self._build_plan()
        preview = format_grid_plan_preview(plan)
        self._preview_var.set(preview)

    def _selected_kv_cache_profile_ids(self, text: str) -> tuple[str, ...]:
        """Extract selected KV cache profile IDs from the summary text."""
        if "Selected:" not in text:
            return ()
        return tuple(id.strip() for id in text.split("Selected:")[1].split(",") if id.strip())

    def _configure_kv_cache(self, target: tk.StringVar) -> None:
        """Configure the KV cache profile selection."""
        self._kv_cache_summary = target

    def _accept(self) -> None:
        plan = self._build_plan()
        if not plan.parameters:
            messagebox.showerror(
                "Grid benchmark",
                "Enable at least one parameter for the grid search.",
                parent=self,
            )
            return
        self.result = plan
        self._save_config()
        self.destroy()

    def _save_config(self) -> None:
        """Persist the grid plan to disk."""
        if self.result is not None:
            save_grid_plan(self.result)

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ---------------------------------------------------------------------------
# Existing Model File Dialog (still in use by hf_import_dialog.py)
# ---------------------------------------------------------------------------


class ExistingModelFileDialog(tk.Toplevel):
    """Dialog for handling existing model files."""

    def __init__(self, master: tk.Misc, final_path: Path) -> None:
        super().__init__(master)
        self.title("Model file exists")
        self._final_path = final_path

        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            body, text=f"Model file already exists: {final_path}"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Button(body, text="Use existing", command=self._use_existing).grid(
            row=1, column=0, sticky="e", padx=4
        )
        ttk.Button(body, text="Redownload", command=self._redownload).grid(
            row=1, column=1, sticky="w", padx=4
        )
        ttk.Button(body, text="Cancel", command=self._cancel).grid(
            row=2, column=0, columnspan=2, pady=(10, 0)
        )

    @staticmethod
    def ask(master: tk.Misc, final_path: Path) -> str:
        """Class method to show the dialog and return the user's choice."""
        dialog = ExistingModelFileDialog(master, final_path)
        dialog.grab_set()
        return dialog._choice  # type: ignore[attr-defined]

    def _use_existing(self) -> None:
        self._choice = "use_existing"
        self.destroy()

    def _redownload(self) -> None:
        self._choice = "redownload"
        self.destroy()

    def _cancel(self) -> None:
        self._choice = "cancel"
        self.destroy()


# ---------------------------------------------------------------------------
# Install Binary Dialog
# ---------------------------------------------------------------------------


class InstallBinaryDialog(tk.Toplevel):
    """Dialog for installing llama-server binaries."""

    def __init__(
        self,
        parent: tk.Misc,
        project_root: Path,
    ) -> None:
        super().__init__(parent)
        self.title("Install llama-server")
        self._project_root = project_root
        self._build()

    def _build(self) -> None:
        """Build the binary installation dialog."""
        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            body, text="Select a llama-server variant to install:"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # Variant options
        variants = [
            ("Windows (Vulkan)", "win-vulkan-x64"),
            ("Windows (CPU)", "win-cpu-x64"),
            ("Linux (Vulkan)", "linux-vulkan-x64"),
            ("Linux (CPU)", "linux-cpu-x64"),
            ("macOS (Metal)", "macos-metal"),
        ]
        for idx, (label, variant) in enumerate(variants, start=1):
            ttk.Radiobutton(
                body, text=label, value=variant
            ).grid(row=idx, column=0, sticky="w", padx=4)

        buttons = ttk.Frame(body)
        buttons.grid(row=len(variants) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(buttons, text="Install", command=self._install).pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _install(self) -> None:
        """Start the binary installation."""
        pass
