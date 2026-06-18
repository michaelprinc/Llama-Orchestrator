"""KV cache profile selection dialog.

Extracted from app.py to reduce context fill during independent refactoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from tkinter import messagebox, ttk

import tkinter as tk

from llama_orchestrator.benchmark_grid import (
    DEFAULT_KV_CACHE_PROFILE_IDS,
    all_kv_cache_profiles,
    kv_cache_profiles_for_preset,
)


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
