"""Table row rendering and data preparation.

Extracted from app.py (Phase 5: Module extraction).
Handles visibility filtering and table row rendering.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Sequence
from llama_orchestrator.gui.dataclasses import TableRow
from llama_orchestrator.gui_state import stable_sort_rows


def filter_visible_rows(
    rows: Sequence[TableRow],
    sort_order: tuple[str, ...],
) -> tuple[TableRow, ...]:
    """Filter and sort rows according to GUI settings."""
    return tuple(
        stable_sort_rows(
            list(rows),
            sort_order,
            lambda current, column: current.sort_values.get(column),
        )
    )


def render_full_rows(
    tree: Any,
    rows: Sequence[TableRow],
    benchmark_active_name: str | None,
    running_benchmark_row_tag: str,
) -> None:
    """Render TableRow data into Treeview widget."""
    # Clear existing items
    for item in tree.get_children():
        tree.delete(item)

    # Insert new rows
    for row in rows:
        tags = (running_benchmark_row_tag,) if row.name == benchmark_active_name else ()
        tree.insert("", tk.END, iid=row.name, values=row.values, tags=tags)
