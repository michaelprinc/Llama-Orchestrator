"""GPU inventory panel and alias editing.

Extracted from app.py (Phase 5: Module extraction).
Handles the GPU detection display and adapter-name alias management.

NOTE: This module does NOT import from app.py to avoid circular imports.
All shared constants and format functions are passed as parameters.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import messagebox, ttk

from llama_orchestrator.engine.detection import DetectedGpu

# ─── Public API ───────────────────────────────────────────────────────


def build_gpu_inventory_frame(
    parent: tk.Widget,
) -> tuple[ttk.LabelFrame, tk.Frame]:
    """Build the GPU inventory frame.

    Returns:
        Tuple of (gpu_inventory_frame, gpu_inventory_rows)
    """
    gpu_inventory_frame = ttk.LabelFrame(
        parent, text="Detected GPUs", padding=(8, 6)
    )
    gpu_inventory_frame.columnconfigure(0, weight=1)
    gpu_inventory_frame.grid(
        row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
    )

    gpu_inventory_rows = tk.Frame(gpu_inventory_frame)
    gpu_inventory_rows.columnconfigure(2, weight=1)
    gpu_inventory_rows.grid(row=0, column=0, sticky="ew")

    return gpu_inventory_frame, gpu_inventory_rows


def toggle_gpu_inventory(
    show_var: tk.BooleanVar,
    gpu_inventory_frame: tk.Widget,
) -> None:
    """Toggle the visibility of the GPU inventory panel."""
    if show_var.get():
        gpu_inventory_frame.pack(fill="x")
    else:
        gpu_inventory_frame.pack_forget()


def render_gpu_inventory(
    parent: tk.Widget,
    gpus: Sequence[DetectedGpu],
    aliases: dict[str, str],
    gpu_alias_button_width: int,
    on_edit_alias: Callable[[str, str], None] | None = None,
) -> list[tk.Widget]:
    """Render GPU inventory rows.

    Args:
        parent: The parent frame widget.
        gpus: Sequence of DetectedGpu objects.
        aliases: GPU alias mapping (adapter_name -> normalized_alias).
        gpu_alias_button_width: Width for alias edit buttons.
        on_edit_alias: Callback invoked when user right-clicks and selects
            "Edit" on an alias button. Signature: (adapter_name, alias).

    Returns:
        List of created widgets for later cleanup.
    """
    # Clear existing widgets
    for widget in parent.winfo_children():
        widget.destroy()
    parent.columnconfigure(2, weight=1)

    widgets: list[tk.Widget] = []
    for gpu in gpus:
        gpu_name = gpu.name or ""
        alias = aliases.get(gpu_name, "")
        display = _format_runtime_gpu_display(gpu, alias)
        label = tk.Label(parent, text=display, anchor="w")
        label.grid(
            row=len(widgets),
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        widgets.append(label)

        # Show alias button only if adapter name is known
        if gpu_name:
            # Button text: show the aliased GPU name (the alias itself)
            btn_text = alias if alias else gpu_name

            alias_btn = tk.Button(
                parent,
                text=btn_text,
                width=gpu_alias_button_width,
            )
            # Position button to the left, just behind the GPU label
            alias_btn.grid(
                row=len(widgets) - 1, column=1, padx=(4, 0), sticky="w"
            )
            widgets.append(alias_btn)

            # Add right-click context menu for editing the alias
            if on_edit_alias is not None:
                _bind_alias_button_context_menu(
                    alias_btn, gpu_name, alias, on_edit_alias
                )

    return widgets


def _bind_alias_button_context_menu(
    button: tk.Button,
    adapter_name: str,
    current_alias: str,
    on_edit_alias: Callable[[str, str], None],
) -> None:
    """Bind a right-click context menu to an alias button.

    Args:
        button: The alias button widget.
        adapter_name: The GPU adapter identifier.
        current_alias: The current alias string.
        on_edit_alias: Callback invoked with (adapter_name, alias).
    """
    menu = tk.Menu(button, tearoff=0)
    menu.add_command(
        label="Edit",
        command=lambda: on_edit_alias(adapter_name, current_alias),
    )

    def _show_menu(event: tk.Event) -> None:
        """Show the context menu at the cursor position."""
        menu.tk_popup(event.x_root, event.y_root)

    button.bind("<Button-3>", _show_menu)
    button.bind("<Button-3>-menu", lambda e: None)  # Suppress default menu


def edit_gpu_alias(
    parent: tk.Widget,
    adapter_name: str,
    current_alias: str,
    aliases_store: dict[str, str],
    on_alias_changed: Callable[[str, str], None],
    normalize_fn: Callable[[str], str],
    save_fn: Callable[[dict[str, str]], None],
) -> None:
    """Show a dialog to edit a GPU adapter alias.

    Args:
        parent: Parent Toplevel/Widget for modality.
        adapter_name: The GPU adapter identifier.
        current_alias: The existing alias string.
        aliases_store: Mutable dict mapping adapter_name -> alias (shared state).
        on_alias_changed: Callback invoked with (adapter_name, normalized_alias).
        normalize_fn: Function to normalize the alias input (str -> str).
        save_fn: Function to persist aliases dict (dict[str, str] -> Path).
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Edit alias for {adapter_name}")
    dialog.geometry("300x150")
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text=f"Adapter: {adapter_name}").pack(pady=5)

    alias_var = tk.StringVar(value=current_alias)
    entry = tk.Entry(dialog, textvariable=alias_var, width=30)
    entry.pack(pady=5)

    def save() -> None:
        try:
            normalized = normalize_fn(alias_var.get())
            aliases_store[adapter_name] = normalized
            save_fn(aliases_store)
            on_alias_changed(adapter_name, normalized)
            dialog.destroy()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))

    tk.Button(dialog, text="Save", command=save).pack(pady=5)
    tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=2)


# ─── Internal helpers ─────────────────────────────────────────────────


def _format_runtime_gpu_display(
    gpu: DetectedGpu,
    alias: str,
) -> str:
    """Format a single GPU entry for display in the inventory panel.

    This is a simplified version of the full format_runtime_gpu_display
    from app.py, which takes (labels, gpus, aliases) for the main table.
    Here we just show label + optional alias.
    """
    if alias:
        return f"{gpu.label}  ({alias})"
    return gpu.label
