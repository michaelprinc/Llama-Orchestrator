"""Activity log widget and message handling.

Extracted from app.py (Phase 5: Module extraction).
Handles the ScrolledText activity log and message pump.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext


def build_activity_log_frame(
    parent: tk.Widget,
    height: int = 9,
) -> tuple[tk.Widget, tk.Widget, str]:
    """Build the activity log frame.

    Returns:
        Tuple of (log_frame, activity_widget, widget_ref_name)
        widget_ref_name should be stored as self.activity
    """
    log_frame = tk.Frame(parent)
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(1, weight=1)

    tk.Label(log_frame, text="Activity").grid(
        row=0, column=0, sticky="w", pady=(8, 2)
    )
    activity = scrolledtext.ScrolledText(
        log_frame, height=height, wrap=tk.WORD, state=tk.DISABLED
    )
    activity.grid(row=1, column=0, sticky="nsew")

    return log_frame, activity, "activity"


def append_activity(activity: tk.Text, message: str) -> None:
    """Append a message to the activity log.

    Args:
        activity: The ScrolledText widget
        message: The message to append
    """
    activity.config(state=tk.NORMAL)
    activity.insert(tk.END, message + "\n")
    activity.see(tk.END)
    activity.config(state=tk.DISABLED)


def schedule_message_pump(
    gui_instance: object,
    after_id: int | None = None,
) -> int | None:
    """Schedule the message pump on the Tkinter main thread.

    Args:
        gui_instance: The LlamaOrchestratorGui instance (must have .after() and _pump_messages)
        after_id: Optional existing after_id to cancel

    Returns:
        New after_id for scheduling
    """
    if after_id is not None:
        gui_instance.after_cancel(after_id)  # type: ignore[attr-defined]
    return gui_instance.after(100, gui_instance._pump_messages)  # type: ignore[attr-defined]
