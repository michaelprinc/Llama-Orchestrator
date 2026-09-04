"""Movable, non-modal detail window for one server's live metrics."""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from llama_orchestrator.live_metrics import LiveMetricSnapshot


def format_live_rate(value: float | None, digits: int = 2) -> str:
    """Format a rate without implying unavailable values are zero."""

    return "—" if value is None else f"{value:.{digits}f} tok/s"


def detail_rate_rows(snapshot: LiveMetricSnapshot) -> tuple[tuple[str, ...], ...]:
    """Build the two precise rows shown in the detail overlay."""

    prompt_whole = (
        "analyzing…"
        if snapshot.prompt_in_progress
        else format_live_rate(snapshot.last_prompt_tokens_per_second)
    )
    decode_whole = (
        "generating…"
        if snapshot.decode_in_progress
        else format_live_rate(snapshot.last_decode_tokens_per_second)
    )
    return (
        (
            "Prefill",
            format_live_rate(snapshot.prefill_tokens_per_second),
            format_live_rate(snapshot.prefill_1m_tokens_per_second),
            format_live_rate(snapshot.prefill_10m_tokens_per_second),
            prompt_whole,
        ),
        (
            "Decode",
            format_live_rate(snapshot.decode_tokens_per_second),
            format_live_rate(snapshot.decode_1m_tokens_per_second),
            format_live_rate(snapshot.decode_10m_tokens_per_second),
            decode_whole,
        ),
    )


class LiveMetricsDetailWindow(tk.Toplevel):
    """Non-modal OS window that can be moved, resized, or closed normally."""

    def __init__(
        self,
        master: tk.Tk,
        model_name: str,
        *,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.model_name = model_name
        self._on_close = on_close
        self.title(f"Live metrics — {model_name}")
        self.geometry("760x250")
        self.minsize(680, 220)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)

        container = ttk.Frame(self, padding=10)
        container.pack(fill=tk.BOTH, expand=True)
        ttk.Label(container, text=model_name, font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w"
        )
        self.status_var = tk.StringVar(value="Waiting for a metrics sample.")
        ttk.Label(container, textvariable=self.status_var).pack(anchor="w", pady=(2, 8))

        columns = ("phase", "now", "minute", "ten_minutes", "whole")
        self.tree = ttk.Treeview(container, columns=columns, show="headings", height=2)
        headings = {
            "phase": "Phase",
            "now": "Now",
            "minute": "Last 1 minute",
            "ten_minutes": "Last 10 minutes",
            "whole": "Last whole phase",
        }
        widths = {"phase": 90, "now": 125, "minute": 135, "ten_minutes": 145, "whole": 150}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.W, stretch=True)
        self.tree.pack(fill=tk.X)
        ttk.Label(
            container,
            text=(
                "Now is the latest polling interval. Rolling speeds use llama.cpp processing "
                "time, so idle time does not dilute them. Whole-phase values remain until the "
                "next corresponding phase begins."
            ),
            wraplength=720,
            foreground="#555555",
        ).pack(anchor="w", pady=(8, 0))

    def update_snapshot(self, snapshot: LiveMetricSnapshot) -> None:
        """Refresh two rows and the compact operational context."""

        age = max(0.0, time.monotonic() - snapshot.sampled_monotonic)
        requests = "unknown"
        if snapshot.requests_processing is not None:
            requests = str(snapshot.requests_processing)
            if snapshot.requests_deferred:
                requests += f" active, {snapshot.requests_deferred} deferred"
            else:
                requests += " active"
        self.status_var.set(
            f"{snapshot.status.replace('_', ' ')} · {snapshot.scope} · "
            f"requests: {requests} · sampled {age:.1f}s ago · {snapshot.message}"
        )
        rows = detail_rate_rows(snapshot)
        for index, values in enumerate(rows):
            iid = str(index)
            if self.tree.exists(iid):
                self.tree.item(iid, values=values)
            else:
                self.tree.insert("", tk.END, iid=iid, values=values)

    def close(self) -> None:
        """Close the window and release the owner's reference."""

        self._on_close()
        self.destroy()
