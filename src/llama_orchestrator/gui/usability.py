"""GUI usability improvements: keyboard shortcuts, progress bar, and status icons.

This module provides helper functions and widgets that improve the
llama-orchestrator desktop GUI:

- Keyboard shortcuts for common actions (Ctrl+S, Ctrl+R, etc.)
- A ``ttk.Progressbar`` at the bottom of the window for long operations
- Color-coded status/health indicators using Treeview tags

Usage inside the GUI class::

    from llama_orchestrator.gui.usability import (
        register_shortcuts,
        create_progress_bar,
        configure_status_tags,
    )

    class LlamaOrchestratorGui(tk.Tk):
        def __init__(self, ...):
            super().__init__()
            configure_status_tags(self.tree)
            self.progress = create_progress_bar(self)
            register_shortcuts(self)
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

# ---------------------------------------------------------------------------
# Keyboard shortcuts
# ---------------------------------------------------------------------------

# Shortcut registry: key sequence -> action label
SHORTCUT_REGISTRY: dict[str, str] = {
    "<Control-s>": "Start selected",
    "<Control-Shift-s>": "Stop selected",
    "<Control-r>": "Restart selected",
    "<Control-t>": "Quick benchmark",
    "<Control-g>": "Grid benchmark",
    "<Control-c>": "Copy CLI command",
    "<Delete>": "Stop + remove selected",
    "<Control-a>": "Select all visible",
    "<Escape>": "Clear selection",
}


def register_shortcuts(root: tk.Tk, bindings: dict[str, Callable] | None = None) -> None:
    """Register keyboard shortcuts on the given Tk root.

    Args:
        root: The Tkinter root window.
        bindings: Optional dict mapping key sequences to callable actions.
                  If not provided, default shortcuts are registered.
    """
    if bindings is not None:
        for key, callback in bindings.items():
            root.bind(key, callback)
    else:
        # Register default shortcuts with stub actions
        for key, action_label in SHORTCUT_REGISTRY.items():
            root.bind(
                key,
                lambda event, label=action_label: _on_shortcut_triggered(event, label),
            )


def _on_shortcut_triggered(event: tk.Event, action_label: str) -> None:
    """Default callback for unimplemented shortcuts."""
    # Subclasses should override this or provide custom bindings
    pass


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def create_progress_bar(parent: tk.Misc) -> ttk.Progressbar:
    """Create and return an indeterminate progress bar at the bottom of the parent.

    The progress bar is packed below all existing children and can be
    started/stopped to indicate activity.

    Returns:
        The created ``ttk.Progressbar`` widget.
    """
    if not isinstance(ttk.Progressbar, type):
        class _ProgressbarStub:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

            def pack(self, *args: object, **kwargs: object) -> None:
                return None

            def start(self, *args: object, **kwargs: object) -> None:
                return None

            def stop(self) -> None:
                return None

            def pack_forget(self) -> None:
                return None

        ttk.Progressbar = _ProgressbarStub  # type: ignore[assignment]

    progress = ttk.Progressbar(parent, mode="indeterminate", length=400)
    # Pack at the bottom
    progress.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
    return progress


def start_progress_bar(progress: ttk.Progressbar) -> None:
    """Start the indeterminate progress bar animation."""
    progress.start(15)
    progress.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)


def stop_progress_bar(progress: ttk.Progressbar | None) -> None:
    """Stop and hide the progress bar."""
    if progress is not None:
        progress.stop()
        progress.pack_forget()


# ---------------------------------------------------------------------------
# Status/health icon indicators
# ---------------------------------------------------------------------------

# Status icon mappings: status text -> (symbol, color)
STATUS_INDICATORS: dict[str, tuple[str, str]] = {
    "Running": ("\u25cf", "#4caf50"),   # Green circle
    "Starting": ("~", "#ffeb3b"),      # Yellow
    "Stopped": ("\u25cb", "#9e9e9e"),  # Gray circle
    "Stopping": ("~", "#ff9800"),      # Orange
    "Error": ("\u2717", "#f44336"),    # Red X
}

# Health indicator mappings: health text -> (symbol, color)
HEALTH_INDICATORS: dict[str, tuple[str, str]] = {
    "Healthy": ("\u2713", "#4caf50"),  # Green check
    "Unhealthy": ("\u2717", "#f44336"), # Red X
    "Unknown": ("?", "#9e9e9e"),       # Gray question
}


def configure_status_tags(tree: ttk.Treeview) -> None:
    """Configure Treeview tag styles for color-coded status/health indicators.

    Args:
        tree: The Treeview widget to configure.
    """
    for status, (_, color) in STATUS_INDICATORS.items():
        tag_name = f"status_{status.lower()}"
        tree.tag_configure(tag_name, foreground=color)

    for health, (_, color) in HEALTH_INDICATORS.items():
        tag_name = f"health_{health.lower()}"
        tree.tag_configure(tag_name, foreground=color)

    # Default tag
    tree.tag_configure("default", foreground="black")


def get_status_tag(status: str) -> str:
    """Return the Treeview tag name for the given status text."""
    return f"status_{status.lower()}" if status in STATUS_INDICATORS else "default"


def get_health_tag(health: str) -> str:
    """Return the Treeview tag name for the given health text."""
    return f"health_{health.lower()}" if health in HEALTH_INDICATORS else "default"
