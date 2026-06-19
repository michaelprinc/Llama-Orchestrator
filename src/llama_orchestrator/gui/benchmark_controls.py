"""Benchmark controls and queue management.

Extracted from app.py (Phase 5: Module extraction).
Handles benchmark buttons, queue management, and serial/batch operations.

NOTE: This module does NOT import from app.py to avoid circular imports.
All benchmark logic is delegated back to the caller via callbacks.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class BenchmarkSettings:
    """Settings for a benchmark run."""
    max_tokens: int = 128
    temperature: float = 0.7
    num_prompts: int = 1
    ignore_eos: bool = False
    endpoint: bool = False


# ─── Public API ───────────────────────────────────────────────────────


def build_benchmark_frame(
    parent: tk.Widget,
) -> tuple[tk.Widget, str]:
    """Build the benchmark controls frame.

    Args:
        parent: The parent container widget.

    Returns:
        Tuple of (frame, widget_ref_name).
        widget_ref_name should be stored as self.benchmark_frame.
    """
    frame = tk.Frame(parent, padding=5)
    return frame, "benchmark_frame"


def configure_benchmark_buttons(
    frame: tk.Widget,
    on_run_background: Callable[[str, Callable[[], str | None]], None],
    on_run_selected: Callable[[str], None],
    on_run_batch: Callable[[str], None],
) -> tuple[tk.Button, ...]:
    """Add benchmark action buttons to the frame.

    Args:
        frame: The benchmark frame.
        on_run_background: Callback to run action in background thread.
        on_run_selected: Callback to run action on selected instance.
        on_run_batch: Callback to run action on batch.

    Returns:
        Tuple of created button widgets for reference.
    """
    btn_run = tk.Button(frame, text="Run benchmark", command=lambda: on_run_background("benchmark", _run_benchmark_action))
    btn_run.pack(side="left", padx=2)

    btn_serial = tk.Button(frame, text="Serial benchmark", command=lambda: on_run_selected("serial_benchmark"))
    btn_serial.pack(side="left", padx=2)

    btn_batch = tk.Button(frame, text="Batch benchmark", command=lambda: on_run_batch("batch_benchmark"))
    btn_batch.pack(side="left", padx=2)

    return btn_run, btn_serial, btn_batch


def _run_benchmark_action() -> str | None:
    """Placeholder benchmark action. Called via on_run_background."""
    return "Benchmark action executed"


def update_benchmark_controls(
    frame: tk.Widget,
    running: bool,
    active_benchmark: str | None,
) -> None:
    """Update benchmark controls based on state.

    Args:
        frame: The benchmark frame.
        running: Whether a benchmark is currently running.
        active_benchmark: Name of the active benchmark (if any).
    """
    # Update button states
    for widget in frame.winfo_children():
        if isinstance(widget, tk.Button):
            widget.config(state="disabled" if running else "normal")

    # Update status label if present
    status_label = getattr(frame, '_benchmark_status', None)
    if status_label and active_benchmark:
        status_label.config(text=f"Active: {active_benchmark}")
    elif status_label:
        status_label.config(text="No active benchmark")
