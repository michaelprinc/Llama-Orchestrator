"""Refresh controller and row-level diffing for the llama-orchestrator GUI.

This module provides the ``RefreshController`` class that manages the refresh
lifecycle (background data collection + main-thread diffing) and a
``RenderDiffMixin`` that can be mixed into the main ``LlamaOrchestratorGui``
class to replace the legacy full-tree rebuild with incremental row updates.

The controller runs on a background thread and posts ``"refresh_done"``
events via ``after_idle()`` so the Tkinter event loop stays responsive.

Usage inside the GUI class::

    class LlamaOrchestratorGui(tk.Tk):
        def __init__(self, ...):
            super().__init__()
            self.refresh_ctrl = RefreshController(self)
            self.refresh_ctrl.on_snapshot(self._on_refresh_done)
            self.refresh_ctrl.start()

    def _on_refresh_done(self, snapshot: GuiRefreshSnapshot) -> None:
        # self._render_diff_rows(snapshot.rows)
        pass
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from collections.abc import Callable

from llama_orchestrator.benchmark import BenchmarkSettings
from llama_orchestrator.engine.detection import DetectedGpu
from llama_orchestrator.gui import RUNNING_BENCHMARK_ROW_TAG, GuiRefreshSnapshot, TableRow

logger = logging.getLogger(__name__)


class RefreshController:
    """Background data collector that posts immutable snapshots.

    The controller:
    1. Runs ``_collect_snapshot()`` on a background thread every
       ``refresh_interval_ms`` (default 2 000 ms).
    2. Posts the resulting ``GuiRefreshSnapshot`` to the main thread via
       the registered ``_on_snapshot`` callback (called with ``root.after``).
    3. Exposes ``start()`` / ``stop()`` for lifecycle management.
    """

    def __init__(
        self,
        root: tk.Tk,
        *,
        refresh_interval_ms: int = 2000,
        gpu_inventory_interval_s: float = 60.0,
        _collect_snapshot: Callable[[], GuiRefreshSnapshot] | None = None,
        _render_diff_rows: Callable[[tuple, tuple, tuple[str, ...]], None] | None = None,
        _render_refresh_metadata: Callable[[GuiRefreshSnapshot, tuple[str, ...], str | None], None] | None = None,
        _render_daemon_status: Callable[[], None] | None = None,
        _update_benchmark_controls: Callable[[], None] | None = None,
        _on_tree_click: Callable[[tk.Event], str | None] | None = None,
    ) -> None:
        self._root = root
        self._refresh_interval_ms = refresh_interval_ms
        self._gpu_inventory_interval_s = gpu_inventory_interval_s
        self._collect_snapshot = _collect_snapshot or self._default_collect
        self._render_diff_rows = _render_diff_rows
        self._render_refresh_metadata = _render_refresh_metadata
        self._render_daemon_status = _render_daemon_status
        self._update_benchmark_controls = _update_benchmark_controls
        self._on_tree_click = _on_tree_click

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._on_snapshot: Callable[[GuiRefreshSnapshot], None] | None = None
        self._last_gpu_inventory: tuple[DetectedGpu, ...] | None = None
        self._gpu_inventory_ts: float = 0
        self._row_map: dict[str, TableRow] = {}
        self._selected_names: tuple[str, ...] = ()
        self._selected_name: str | None = None
        self._focused_name: str | None = None
        self._queued_benchmark_names: set[str] = set()
        self._benchmark_active_name: str | None = None
        self._gpu_aliases: dict[str, str] = {}
        self._detected_gpus: tuple[DetectedGpu, ...] = ()
        self._benchmark_settings: BenchmarkSettings | None = None

    def on_snapshot(self, callback: Callable[[GuiRefreshSnapshot], None]) -> None:
        """Register the callback that receives each new snapshot."""
        self._on_snapshot = callback

    def start(self) -> None:
        """Start the background refresh loop."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.warning("RefreshController already running")
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="GuiRefresh",
        )
        self._worker_thread.start()
        logger.info("RefreshController started (interval=%d ms)", self._refresh_interval_ms)

    def stop(self) -> None:
        """Stop the background loop and wait for the worker to exit."""
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=5)
            self._worker_thread = None
        logger.info("RefreshController stopped")

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """Background loop that collects snapshots and posts them."""
        while not self._stop_event.is_set():
            try:
                snapshot = self._collect_snapshot()
                if self._on_snapshot is not None:
                    self._root.after(0, self._on_snapshot, snapshot)
            except Exception as exc:
                logger.error("RefreshController error: %s", exc)
            self._stop_event.wait(self._refresh_interval_ms / 1000.0)

    def _default_collect(self) -> GuiRefreshSnapshot:
        """Default data collection (callable is overridden by the GUI)."""
        return GuiRefreshSnapshot(
            rows=(),
            detected_gpus=(),
            all_tags=(),
            collected_at=time.time(),
        )

    # ------------------------------------------------------------------
    # Public helpers (called by the GUI after receiving a snapshot)
    # ------------------------------------------------------------------

    def render_full_refresh(
        self,
        snapshot: GuiRefreshSnapshot,
        selection: tuple[str, ...],
        focused: str | None,
    ) -> None:
        """Full refresh: rebuild tree, metadata, benchmark controls."""
        if self._render_diff_rows is not None:
            self._render_diff_rows(snapshot.rows, snapshot.detected_gpus, snapshot.all_tags)
        if self._render_refresh_metadata is not None:
            self._render_refresh_metadata(snapshot, selection, focused)
        if self._render_daemon_status is not None:
            self._render_daemon_status()
        if self._update_benchmark_controls is not None:
            self._update_benchmark_controls()

    def render_diff_rows(
        self,
        new_rows: tuple[TableRow, ...],
        detected_gpus: tuple[DetectedGpu, ...],
        all_tags: tuple[str, ...],
    ) -> None:
        """Incremental row update: diff against the previous map."""
        if self._render_diff_rows is not None:
            self._render_diff_rows(new_rows, detected_gpus, all_tags)

    @property
    def running(self) -> bool:
        return (
            self._worker_thread is not None
            and self._worker_thread.is_alive()
            and not self._stop_event.is_set()
        )


# ------------------------------------------------------------------
# Row diffing mixin
# ------------------------------------------------------------------

class RenderDiffMixin:
    """Mixin that provides row-diffing methods for the GUI class.

    Mix this into ``LlamaOrchestratorGui`` (or replace the inline methods)
    to get incremental tree updates instead of full rebuilds.
    """

    def render_diff_rows(
        self,
        new_rows: tuple[TableRow, ...],
        detected_gpus: tuple[DetectedGpu, ...],
        all_tags: tuple[str, ...],
    ) -> None:
        """Diff-based row update for the Treeview.

        Replaces the legacy ``_render_full_rows()`` which destroyed and
        re-inserted every row.  Now only changed/added/removed rows touch
        the Treeview.
        """
        new_map: dict[str, TableRow] = {row.name: row for row in new_rows}
        existing = self._row_map  # type: ignore[attr-defined]

        # Update or insert
        for name, row in new_map.items():
            if name in existing:
                old_row = existing[name]
                if old_row.values != row.values:
                    self.tree.set(name, row.values)  # type: ignore[attr-defined]
            else:
                tags = (RUNNING_BENCHMARK_ROW_TAG,) if name == getattr(self, "_benchmark_active_name", None) else ()  # type: ignore[attr-defined]
                self.tree.insert("", tk.END, iid=name, values=row.values, tags=tags)  # type: ignore[attr-defined]

        # Delete rows no longer present
        for name in list(existing.keys()):
            if name not in new_map:
                self.tree.delete(name)  # type: ignore[attr-defined]

        # Update the map
        self._row_map = new_map  # type: ignore[attr-defined]

    def _debounced_gpu_inventory(self, detected_gpus: tuple[DetectedGpu, ...]) -> bool:
        """Return True if the GPU inventory actually changed.

        Caches the result and skips rebuild if less than
        ``_gpu_inventory_interval_s`` (default 60 s) has passed.
        """
        now = time.monotonic()
        if (
            self._last_gpu_inventory == detected_gpus  # type: ignore[attr-defined]
            and now - self._gpu_inventory_ts < 60  # type: ignore[attr-defined]
        ):
            return True  # skip rebuild
        self._last_gpu_inventory = detected_gpus  # type: ignore[attr-defined]
        self._gpu_inventory_ts = now  # type: ignore[attr-defined]
        return False
