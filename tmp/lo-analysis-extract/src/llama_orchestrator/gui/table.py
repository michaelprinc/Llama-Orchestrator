"""Table (Treeview) widget setup and rendering.

Extracted from app.py (Phase 5: Module extraction).
Handles the main instance table (ttk.Treeview), column config,
sorting, and selection.

NOTE: This module does NOT import from app.py to avoid circular imports.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

# ─── Constants ────────────────────────────────────────────────────────

SORT_ASC = "↑"
SORT_DESC = "↓"
UNSORTED = ""


# ─── Public API ───────────────────────────────────────────────────────


def build_tree(
    parent: tk.Widget,
    columns: tuple[str, ...],
    column_headings: dict[str, str],
    visible_columns: tuple[str, ...],
) -> tuple[ttk.Treeview, str]:
    """Build the main instance Treeview widget.

    Args:
        parent: The parent container widget.
        columns: All possible column names (includes hidden columns).
        column_headings: Mapping of column_name -> display label.
        visible_columns: Currently visible columns.

    Returns:
        Tuple of (Treeview, widget_ref_name).
        widget_ref_name should be stored as self.tree.
    """
    tree = ttk.Treeview(parent, columns=list(columns), show="headings")
    tree["selectmode"] = "extended"

    # Configure headings
    for col in columns:
        heading = column_headings.get(col, col)
        tree.column(col, anchor="center", width=100)
        tree.heading(col, text=heading)

    # Enable sorting for name column
    tree.column("name", anchor="w", width=200)
    tree.heading("name", text="Name")

    return tree, "tree"


def apply_visible_columns(
    tree: ttk.Treeview,
    column_headings: dict[str, str],
    all_columns: tuple[str, ...],
    visible_columns: tuple[str, ...],
) -> None:
    """Apply visible column configuration to the Treeview.

    Args:
        tree: The Treeview widget.
        column_headings: Mapping of column_name -> display label.
        all_columns: All possible column names.
        visible_columns: Currently visible column names.
    """
    # Build the column list to pass to tree['columns']
    new_columns = [col for col in all_columns if col in visible_columns]
    tree['columns'] = new_columns

    # Update headings for visible columns
    for col in new_columns:
        heading = column_headings.get(col, col)
        tree.heading(col, text=heading)


def configure_sort_header(
    tree: ttk.Treeview,
    sort_column: str | None,
    sort_reverse: bool,
    column_headings: dict[str, str],
) -> None:
    """Update sort indicator in column headings.

    Args:
        tree: The Treeview widget.
        sort_column: Currently sorted column name (or None).
        sort_reverse: True if descending sort.
        column_headings: Mapping of column_name -> display label.
    """
    for col in tree['columns']:
        if sort_column and col == sort_column:
            indicator = SORT_DESC if sort_reverse else SORT_ASC
            heading = column_headings.get(col, col)
            tree.heading(col, text=f"{heading} {indicator}")
        else:
            heading = column_headings.get(col, col)
            if heading:
                tree.heading(col, text=heading)


# ─── Internal helpers ─────────────────────────────────────────────────


def _make_sort_callback(column: str, tree: ttk.Treeview) -> Callable[[], None]:
    """Create a sort callback for a specific column.

    Args:
        column: Column name to sort by.
        tree: The Treeview widget.

    Returns:
        A callable that toggles sort direction.
    """
    def sort_callback() -> None:
        heading_info = tree.heading(column)
        heading_text = heading_info.get("text", "") if heading_info else ""

        # Determine current sort direction by looking at the indicator in text
        currently_desc = SORT_DESC in str(heading_text)
        currently_asc = SORT_ASC in str(heading_text)

        # Toggle: desc→asc, asc→desc, neither→asc (first click)
        if currently_desc:
            new_indicator = SORT_ASC
        elif currently_asc:
            new_indicator = SORT_DESC
        else:
            new_indicator = SORT_ASC

        # Sort items (toggle: was desc -> now asc, was asc -> now desc)
        items = [(tree.set(child, column), child) for child in tree.get_children()]
        items.sort(reverse=not currently_desc)
        for index, (_, child) in enumerate(items):
            tree.move(child, "", index)

        # Strip any existing indicator and re-apply the toggled one
        base = (
            str(heading_text)
            .replace(f" {SORT_ASC}", "")
            .replace(f" {SORT_DESC}", "")
            .strip()
        )
        tree.heading(column, text=f"{base} {new_indicator}", command=sort_callback)

    return sort_callback


def on_tree_click(
    event: tk.Event,
    tree: ttk.Treeview,
    on_context_menu: Callable[[tk.Event], None],
) -> str | None:
    """Handle tree widget click events.

    Args:
        event: The mouse click event.
        tree: The Treeview widget.
        on_context_menu: Callback to show context menu.

    Returns:
        Selected item name, or None.
    """
    if event.num == 3:  # Right-click
        on_context_menu(event)
        return None

    region = tree.identify_region(event.x, event.y)
    if region == "cell":
        return tree.identify_row(event.y)
    return None


def get_tree_column_from_event(
    event: tk.Event,
    tree: ttk.Treeview,
) -> str | None:
    """Extract the column name from a tree event.

    Args:
        event: The event from the Treeview.
        tree: The Treeview widget.

    Returns:
        Column name string, or None.
    """
    col_id = tree.identify_column(event.x)
    if not col_id:
        return None
    try:
        col_index = int(col_id[1:]) - 1   # "#1" -> 0
        columns = tree["columns"]
        if 0 <= col_index < len(columns):
            return columns[col_index]
    except (ValueError, IndexError):
        pass
    return None


def on_tree_double_click(
    event: tk.Event,
    tree: ttk.Treeview,
    on_double_click: Callable[[str], None],
) -> None:
    """Handle double-click on tree items.

    Args:
        event: The double-click event.
        tree: The Treeview widget.
        on_double_click: Callback with the selected item name.
    """
    row = tree.identify_row(event.y)
    if row:
        on_double_click(row)
