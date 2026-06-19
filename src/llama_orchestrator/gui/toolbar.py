"""Toolbar widget factory for the llama-orchestrator GUI.

Extracted from app.py (Phase 5: Module extraction).
Handles the toolbar frame, buttons, columns menu, and daemon controls.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ToolbarCallbacks:
    """Callbacks for toolbar button actions."""
    on_refresh: Callable[[], None]
    on_add_model: Callable[[], None]
    on_apply_args: Callable[[], None]
    on_install_llama_server: Callable[[], None]
    on_start: Callable[[], None]
    on_stop: Callable[[], None]
    on_restart: Callable[[], None]
    on_health: Callable[[], None]
    on_start_daemon: Callable[[], None]
    on_stop_daemon: Callable[[], None]
    on_column_toggle: Callable[[], None]
    on_tag_filter: Callable[[tk.Event], None]
    on_edit_prompt: Callable[[], None]
    on_toggle_gpu_inventory: Callable[[], None]
    on_start_visible: Callable[[], None]
    on_stop_visible: Callable[[], None]
    on_restart_visible: Callable[[], None]


@dataclass
class ToolbarWidgets:
    """Widget references returned by build_toolbar."""
    toolbar: ttk.Frame
    start_daemon_btn: ttk.Button
    stop_daemon_btn: ttk.Button
    tag_combo: ttk.Combobox
    columns_vars: dict[str, tk.BooleanVar]


def build_toolbar(
    parent: tk.Widget,
    *,
    callbacks: ToolbarCallbacks,
    column_headings: dict[str, str],
    all_columns: tuple[str, ...],
    visible_columns: tuple[str, ...],
    tag_filter_var: tk.StringVar,
    show_gpu_inventory_var: tk.BooleanVar,
    prompt_var: tk.StringVar,
    daemon_var: tk.StringVar,
) -> ToolbarWidgets:
    """Build the complete toolbar frame with all buttons and controls.

    Args:
        parent: The parent container widget.
        callbacks: Callbacks for button clicks.
        column_headings: Mapping of column_name -> display_label.
        all_columns: Tuple of all possible column names.
        visible_columns: Tuple of currently visible column names.
        tag_filter_var: StringVar for the tag Combobox.
        show_gpu_inventory_var: BooleanVar for the GPU map Checkbutton.
        prompt_var: StringVar for the prompt file path display.
        daemon_var: StringVar for the daemon status text display.

    Returns:
        The configured ToolbarWidgets object.
    """
    toolbar = ttk.Frame(parent, padding=(10, 10, 10, 4))
    toolbar.columnconfigure(16, weight=1)

    # --- Row 0: Action buttons ---
    ttk.Button(toolbar, text="Refresh", command=callbacks.on_refresh).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(toolbar, text="Add model", command=callbacks.on_add_model).grid(
        row=0, column=1, padx=6
    )
    ttk.Button(toolbar, text="Apply args", command=callbacks.on_apply_args).grid(
        row=0, column=2, padx=6
    )
    ttk.Button(
        toolbar,
        text="Install llama-server",
        command=callbacks.on_install_llama_server,
    ).grid(row=0, column=3, padx=6)
    ttk.Button(toolbar, text="Start", command=callbacks.on_start).grid(
        row=0, column=4, padx=6
    )
    ttk.Button(toolbar, text="Stop", command=callbacks.on_stop).grid(
        row=0, column=5, padx=6
    )
    ttk.Button(toolbar, text="Restart", command=callbacks.on_restart).grid(
        row=0, column=6, padx=6
    )
    ttk.Button(toolbar, text="Health", command=callbacks.on_health).grid(
        row=0, column=7, padx=6
    )

    # --- Columns menu ---
    columns_button = ttk.Menubutton(toolbar, text="Columns")
    columns_menu = tk.Menu(columns_button, tearoff=False)
    columns_button["menu"] = columns_menu

    columns_vars: dict[str, tk.BooleanVar] = {}
    for column in all_columns:
        var = tk.BooleanVar(master=toolbar, value=column in visible_columns)
        columns_vars[column] = var
        columns_menu.add_checkbutton(
            label=column_headings.get(column, column),
            variable=var,
            command=callbacks.on_column_toggle,
        )
    columns_button.grid(row=0, column=8, padx=6)

    # --- Batch menu ---
    batch_button = ttk.Menubutton(toolbar, text="Batch")
    batch_menu = tk.Menu(batch_button, tearoff=False)
    batch_button["menu"] = batch_menu
    batch_menu.add_command(label="Start visible", command=callbacks.on_start_visible)
    batch_menu.add_command(label="Stop visible", command=callbacks.on_stop_visible)
    batch_menu.add_command(label="Restart visible", command=callbacks.on_restart_visible)
    batch_button.grid(row=0, column=9, padx=6)

    # --- Tag filter ---
    ttk.Label(toolbar, text="Tag").grid(
        row=0, column=10, sticky="e", padx=(12, 4)
    )
    tag_combo = ttk.Combobox(
        toolbar,
        textvariable=tag_filter_var,
        values=("All tags",),
        state="readonly",
        width=16,
    )
    tag_combo.grid(row=0, column=11, sticky="w", padx=(0, 6))
    tag_combo.bind("<<ComboboxSelected>>", callbacks.on_tag_filter)

    # --- Benchmark prompt ---
    ttk.Button(
        toolbar,
        text="Edit Benchmark Prompt",
        command=callbacks.on_edit_prompt,
    ).grid(row=0, column=12, padx=6)
    ttk.Label(toolbar, textvariable=prompt_var).grid(
        row=0, column=13, sticky="w", padx=(0, 6)
    )

    # --- GPU inventory checkbox ---
    ttk.Checkbutton(
        toolbar,
        text="GPU map",
        variable=show_gpu_inventory_var,
        command=callbacks.on_toggle_gpu_inventory,
    ).grid(row=0, column=14, padx=(12, 6))

    # --- Daemon status ---
    ttk.Label(toolbar, textvariable=daemon_var).grid(
        row=0, column=16, sticky="e", padx=(10, 6)
    )
    start_daemon_btn = ttk.Button(
        toolbar, text="Start daemon", command=callbacks.on_start_daemon
    )
    start_daemon_btn.grid(row=0, column=17, padx=6)
    stop_daemon_btn = ttk.Button(
        toolbar, text="Stop daemon", command=callbacks.on_stop_daemon
    )
    stop_daemon_btn.grid(row=0, column=18, padx=(6, 0))

    return ToolbarWidgets(
        toolbar=toolbar,
        start_daemon_btn=start_daemon_btn,
        stop_daemon_btn=stop_daemon_btn,
        tag_combo=tag_combo,
        columns_vars=columns_vars,
    )
