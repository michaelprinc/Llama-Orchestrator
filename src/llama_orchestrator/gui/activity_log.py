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
    activity.bind("<Control-c>", lambda _event: copy_activity_selection_and_stop(activity))

    copy_menu = tk.Menu(log_frame, tearoff=False)
    copy_menu.add_command(label="Copy", command=lambda: copy_activity_selection(activity))

    def show_copy_menu(event: tk.Event) -> str:
        """Show the activity log's copy menu at the pointer."""
        copy_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    activity.bind("<Button-3>", show_copy_menu)

    return log_frame, activity, "activity"


def copy_activity_selection(activity: tk.Text) -> bool:
    """Copy the selected activity-log text to the clipboard.

    Returns ``False`` when no text is selected, so an accidental Copy action
    leaves the clipboard untouched.
    """
    try:
        selected_text = activity.get(tk.SEL_FIRST, tk.SEL_LAST)
    except tk.TclError:
        return False

    if not selected_text:
        return False

    activity.clipboard_clear()
    activity.clipboard_append(selected_text)
    return True


def copy_activity_selection_and_stop(activity: tk.Text) -> str:
    """Copy selected activity text without invoking the window-wide shortcut."""
    copy_activity_selection(activity)
    return "break"


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
