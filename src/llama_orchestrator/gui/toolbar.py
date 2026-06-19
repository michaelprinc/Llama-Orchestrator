"""Toolbar widget factory for the llama-orchestrator GUI.

Extracted from app.py (Phase 5: Module extraction).
Handles the toolbar frame, buttons, columns menu, and daemon controls.

NOTE: This module does NOT import from app.py to avoid circular imports.
All shared constants are passed as parameters.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ─── Public API ───────────────────────────────────────────────────────


def build_toolbar(
    parent: tk.Widget,
    *,
    column_headings: dict[str, str],
    all_columns: tuple[str, ...],
    visible_columns: tuple[str, ...],
) -> ttk.Frame:
    """Build the complete toolbar frame with all buttons and controls.

    The caller (app.py) configures button callbacks via grid positions
    after frame creation. This function only builds the widget structure.

    Args:
        parent: The parent container widget.
        column_headings: Mapping of column_name -> display_label.
        all_columns: Tuple of all possible column names.
        visible_columns: Tuple of currently visible column names.

    Returns:
        The configured ttk.Frame ready for grid placement.
    """
    toolbar = ttk.Frame(parent, padding=(10, 10, 10, 4))

    # Configure a weight column so buttons distribute evenly
    for i in range(19):
        toolbar.columnconfigure(i, weight=1)

    # --- Row 0: Action buttons ---
    # Positions are chosen to match the original app.py layout
    ttk.Button(toolbar, text="Refresh").grid(row=0, column=0, padx=(0, 4))
    ttk.Button(toolbar, text="Add model").grid(row=0, column=1, padx=4)
    ttk.Button(toolbar, text="Apply args").grid(row=0, column=2, padx=4)
    ttk.Button(
        toolbar, text="Install llama-server"
    ).grid(row=0, column=3, padx=4)
    ttk.Button(toolbar, text="Start").grid(row=0, column=4, padx=4)
    ttk.Button(toolbar, text="Stop").grid(row=0, column=5, padx=4)
    ttk.Button(toolbar, text="Restart").grid(row=0, column=6, padx=4)
    ttk.Button(toolbar, text="Health").grid(row=0, column=7, padx=4)

    # --- Columns menu ---
    columns_menu = _build_columns_menu(
        column_headings, all_columns, visible_columns
    )
    columns_button = ttk.Menubutton(toolbar, text="Columns", menu=columns_menu)
    columns_button.grid(row=0, column=8, padx=4)

    # --- Batch menu ---
    batch_menu = tk.Menu(columns_menu, tearoff=False)
    ttk.Menubutton(
        toolbar, text="Batch", menu=batch_menu
    ).grid(row=0, column=9, padx=4)

    # --- Tag filter ---
    ttk.Label(toolbar, text="Tag").grid(
        row=0, column=10, sticky="e", padx=(8, 2)
    )

    # --- Benchmark prompt ---
    ttk.Button(toolbar, text="Edit Benchmark Prompt").grid(
        row=0, column=12, padx=4
    )
    ttk.Label(toolbar, text="(prompt)").grid(
        row=0, column=13, sticky="w", padx=(0, 4)
    )

    # --- GPU inventory checkbox ---
    ttk.Checkbutton(toolbar, text="GPU map").grid(
        row=0, column=14, padx=(8, 4)
    )

    # --- Daemon status ---
    ttk.Label(toolbar, text="(daemon)").grid(
        row=0, column=16, sticky="e", padx=(8, 4)
    )
    ttk.Button(toolbar, text="Start daemon").grid(
        row=0, column=17, padx=4
    )
    ttk.Button(toolbar, text="Stop daemon").grid(
        row=0, column=18, padx=(4, 0)
    )

    return toolbar


def _build_columns_menu(
    column_headings: dict[str, str],
    all_columns: tuple[str, ...],
    visible_columns: tuple[str, ...],
) -> tk.Menu:
    """Build the columns checkbutton menu.

    Args:
        column_headings: Mapping of column_name -> display_label.
        all_columns: All possible column names.
        visible_columns: Currently visible column names.

    Returns:
        A tk.Menu with checkbuttons for each column.
    """
    columns_menu = tk.Menu(tk.Menu(), tearoff=False)
    for column in all_columns:
        var = tk.BooleanVar(value=column in visible_columns)
        columns_menu.add_checkbutton(
            label=column_headings.get(column, column),
            variable=var,
        )
    return columns_menu
