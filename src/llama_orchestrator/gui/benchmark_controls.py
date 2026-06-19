"""Benchmark controls and queue management.

Extracted from app.py (Phase 5: Module extraction).
Handles benchmark buttons, queue management, and serial/batch operations.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from collections.abc import Callable


def build_benchmark_frame(
    parent: tk.Widget,
    *,
    grid_benchmark_label: str,
    on_quick_benchmark: Callable[[], None],
    on_serial_benchmark: Callable[[], None],
    on_grid_benchmark: Callable[[], None],
    on_stop_serial: Callable[[], None],
    on_stop_grid: Callable[[], None],
) -> tuple[ttk.Frame, dict[str, ttk.Button]]:
    """Build the benchmark controls frame containing all benchmark buttons.

    Args:
        parent: The parent container widget.
        grid_benchmark_label: Label text for the grid benchmark button.
        on_quick_benchmark: Callback for quick benchmark.
        on_serial_benchmark: Callback for serial benchmark.
        on_grid_benchmark: Callback for grid benchmark.
        on_stop_serial: Callback to stop serial benchmark queue.
        on_stop_grid: Callback to stop grid benchmark.

    Returns:
        Tuple of (frame, buttons_dict).
    """
    frame = ttk.Frame(parent)

    buttons: dict[str, ttk.Button] = {}

    btn_quick = ttk.Button(frame, text="Quick benchmark", command=on_quick_benchmark)
    btn_quick.pack(side=tk.LEFT)
    buttons["quick"] = btn_quick

    btn_serial = ttk.Button(frame, text="Serial benchmark", command=on_serial_benchmark)
    btn_serial.pack(side=tk.LEFT, padx=(6, 0))
    buttons["serial"] = btn_serial

    btn_grid = ttk.Button(frame, text=grid_benchmark_label, command=on_grid_benchmark)
    btn_grid.pack(side=tk.LEFT, padx=(6, 0))
    buttons["grid"] = btn_grid

    btn_stop_serial = ttk.Button(frame, text="Stop queue", command=on_stop_serial)
    btn_stop_serial.pack(side=tk.LEFT, padx=(6, 0))
    buttons["stop_serial"] = btn_stop_serial

    btn_stop_grid = ttk.Button(frame, text="Stop grid", command=on_stop_grid)
    btn_stop_grid.pack(side=tk.LEFT, padx=(6, 0))
    buttons["stop_grid"] = btn_stop_grid

    return frame, buttons


def update_benchmark_controls(
    buttons: dict[str, ttk.Button],
    *,
    running: bool,
    queued_visible: bool,
    has_grid_target: bool,
    serial_active: bool,
    serial_stopping: bool,
    grid_active: bool,
    grid_stopping: bool,
) -> None:
    """Update the enabled/disabled states of the benchmark buttons.

    Args:
        buttons: Dictionary containing the benchmark buttons.
        running: Whether any benchmark job is currently running.
        queued_visible: Whether there are benchmark items queued.
        has_grid_target: Whether a grid benchmark target exists.
        serial_active: Whether a serial benchmark is active.
        serial_stopping: Whether a serial benchmark is stopping.
        grid_active: Whether a grid benchmark is active.
        grid_stopping: Whether a grid benchmark is stopping.
    """
    buttons["quick"].configure(state=tk.DISABLED if running else tk.NORMAL)
    buttons["serial"].configure(
        state=tk.DISABLED if running or not queued_visible else tk.NORMAL
    )
    buttons["grid"].configure(
        state=tk.DISABLED if running or not has_grid_target else tk.NORMAL
    )
    buttons["stop_serial"].configure(
        state=tk.NORMAL
        if serial_active and not serial_stopping
        else tk.DISABLED
    )
    buttons["stop_grid"].configure(
        state=tk.NORMAL if grid_active and not grid_stopping else tk.DISABLED
    )
