"""Instance action handlers.

Extracted from app.py (Phase 5: Module extraction).
Handles clone, diff, copy CLI command, and rename operations.

NOTE: This module does NOT import from app.py to avoid circular imports.
All instance operations are delegated via callbacks.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox

# ─── Public API ───────────────────────────────────────────────────────


def clone_instance(
    source_name: str,
    next_clone_name_fn: Callable[[str], str],
    clone_fn: Callable[[str, str, Path, str], None],
    on_clone_complete: Callable[[str], None],
) -> None:
    """Clone the selected instance.

    Args:
        source_name: Name of the source instance to clone.
        next_clone_name_fn: Function to generate next clone name.
        clone_fn: Function to perform the actual clone.
        on_clone_complete: Callback with new instance name.
    """
    new_name = next_clone_name_fn(source_name)
    if not new_name:
        return

    try:
        # The clone_fn should handle the actual cloning
        clone_fn(source_name, new_name, Path(), "")
        on_clone_complete(new_name)
        messagebox.showinfo("Clone", f"Instance '{source_name}' cloned as '{new_name}'")
    except Exception as exc:
        messagebox.showerror("Clone failed", str(exc))


def next_clone_name(
    source: str,
    existing_names: set[str],
) -> str:
    """Generate a unique clone name based on source.

    Args:
        source: The source instance name.
        existing_names: Set of all existing instance names.

    Returns:
        A unique name for the clone.
    """
    base = source
    counter = 1
    candidate = f"{base}-clone-{counter}"
    while candidate in existing_names:
        counter += 1
        candidate = f"{base}-clone-{counter}"
    return candidate


def diff_instances(
    source: str,
    target: str,
    diff_fn: Callable[[str, str], str],
) -> None:
    """Compare two instances and show diff.

    Args:
        source: First instance name.
        target: Second instance name.
        diff_fn: Function to generate diff string.
    """
    try:
        diff_text = diff_fn(source, target)
        _show_diff_window(source, target, diff_text)
    except Exception as exc:
        messagebox.showerror("Diff failed", str(exc))


def _show_diff_window(
    name1: str,
    name2: str,
    diff_text: str,
) -> None:
    """Show diff in a new Toplevel window."""
    dialog = tk.Toplevel()
    dialog.title(f"Diff: {name1} vs {name2}")
    dialog.geometry("600x400")

    text = tk.Text(dialog, wrap="none")
    text.pack(fill="both", expand=True, padx=10, pady=10)
    text.insert("1.0", diff_text)
    text.config(state="disabled")


def copy_cli_command(
    instance_name: str,
    get_cli_command_fn: Callable[[str], str],
) -> None:
    """Copy CLI command to clipboard.

    Args:
        instance_name: Name of the instance.
        get_cli_command_fn: Function to get the CLI command string.
    """
    try:
        command = get_cli_command_fn(instance_name)
        root = tk.Tk()
        root.withdraw()  # Hide main window
        root.clipboard_clear()
        root.clipboard_append(command)
        root.destroy()
        messagebox.showinfo("Copied", "CLI command copied to clipboard")
    except Exception as exc:
        messagebox.showerror("Copy failed", str(exc))


def rename_instance(
    instance_name: str,
    current_display_name: str,
    confirm_fn: Callable[[str, str], bool],
) -> bool:
    """Rename an instance's display name.

    Args:
        instance_name: The instance identifier.
        current_display_name: Current display name.
        confirm_fn: Confirmation callback (returns True to proceed).

    Returns:
        True if rename was confirmed, False otherwise.
    """
    dialog = tk.Tk()
    dialog.title(f"Rename: {instance_name}")
    dialog.geometry("300x150")
    dialog.transient()
    dialog.grab_set()

    tk.Label(dialog, text=f"Current name: {current_display_name}").pack(pady=10)

    name_var = tk.StringVar(value=current_display_name)
    entry = tk.Entry(dialog, textvariable=name_var, width=30)
    entry.pack(pady=5)

    result: list[bool] = [False]

    def on_ok() -> None:
        new_name = name_var.get().strip()
        if new_name and new_name != current_display_name and confirm_fn(instance_name, new_name):
            result[0] = True
        dialog.destroy()

    tk.Button(dialog, text="OK", command=on_ok).pack(pady=5)
    tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=2)

    dialog.mainloop()
    return result[0]
