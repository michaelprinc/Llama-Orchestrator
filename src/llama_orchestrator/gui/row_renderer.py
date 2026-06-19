"""Table row rendering and data preparation.

Extracted from app.py (Phase 5: Module extraction).
Handles TableRow dataclass, row building, and visibility filtering.

NOTE: This module does NOT import from app.py to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TableRow:
    """A single row of data for the instance table."""
    name: str
    data: dict[str, Any] = field(default_factory=dict)


# ─── Public API ───────────────────────────────────────────────────────


def build_table_rows(
    names: list[str],
    instance_names: list[str],
    render_data: dict[str, dict[str, Any]],
) -> list[TableRow]:
    """Build TableRow objects for all visible instances.

    Args:
        names: List of instance names to render.
        instance_names: All known instance names (for status lookup).
        render_data: Dict mapping instance_name -> row data dict.

    Returns:
        List of TableRow objects.
    """
    rows: list[TableRow] = []
    for name in names:
        data = render_data.get(name, {})
        rows.append(TableRow(name=name, data=data))
    return rows


def filter_visible_rows(
    rows: list[TableRow],
    start_idx: int,
    end_idx: int,
) -> list[TableRow]:
    """Filter rows to only those in the visible window.

    Args:
        rows: All rows to potentially render.
        start_idx: Starting index (inclusive).
        end_idx: Ending index (exclusive).

    Returns:
        Sublist of rows for the visible window.
    """
    if start_idx is None or end_idx is None:
        return rows[start_idx:end_idx] if start_idx is not None and end_idx is not None else rows
    return rows[start_idx:end_idx]


def render_full_rows(
    tree: Any,
    rows: list[TableRow],
    visible_columns: tuple[str, ...],
) -> None:
    """Render TableRow data into Treeview widget.

    Args:
        tree: The Treeview widget.
        rows: List of TableRow objects to render.
        visible_columns: Columns to display.
    """
    # Clear existing items
    for item in tree.get_children():
        tree.delete(item)

    # Insert new rows
    for row in rows:
        values = [row.data.get(col, "") for col in visible_columns]
        tree.insert("", "end", iid=row.name, values=values)


def render_refresh_metadata(
    tree: Any,
    row_count: int,
    elapsed_ms: float,
) -> None:
    """Update tree metadata after refresh.

    Args:
        tree: The Treeview widget.
        row_count: Number of rows currently displayed.
        elapsed_ms: Time taken for refresh in milliseconds.
    """
    # Store metadata as Treeview tag data
    meta_key = f"refresh_meta_{row_count}_{elapsed_ms:.0f}"
    tree.tag_configure(meta_key)
