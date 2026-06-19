"""Tests for table.py, gpu_inventory.py, and metadata_cache.py fixes.

Covers the three modules fixed in Plan A (Phase 2 GUI refactoring).
Does NOT modify test_gui.py — runs independently.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────
# table.py tests
# ──────────────────────────────────────────────────────────────────────


class TestTableSortCallback:
    """Verify sort callback toggles indicator and sorts correctly."""

    @pytest.fixture
    def mock_tree(self):
        """Build a mock Treeview with realistic heading/column state."""
        tree = MagicMock()
        tree.heading.return_value = {"text": "Name"}
        tree.__getitem__ = MagicMock(return_value=["name", "status", "health"])
        tree.get_children.return_value = ("row1", "row2", "row3")
        # Simulate data: row1->"beta", row2->"alpha", row3->"gamma"
        tree.set.side_effect = lambda child, col: {
            "row1": {"name": "beta", "status": "running", "health": "healthy"},
            "row2": {"name": "alpha", "status": "stopped", "health": "unknown"},
            "row3": {"name": "gamma", "status": "running", "health": "healthy"},
        }[child].get(col, "")
        return tree

    def test_sort_asc_sets_indicator(self, mock_tree):
        """First click on a column should set the ↑ (SORT_ASC) indicator."""
        from llama_orchestrator.gui.table import (
            SORT_ASC,
            SORT_DESC,
            _make_sort_callback,
        )

        callback = _make_sort_callback("name", mock_tree)
        callback()

        # Verify heading was updated with sort indicator
        heading_call = mock_tree.heading.call_args_list[-1]
        assert "text" in heading_call.kwargs
        assert SORT_ASC in heading_call.kwargs["text"]

    def test_sort_desc_on_second_click(self, mock_tree):
        """Second click on the same column should toggle to ↓ (SORT_DESC)."""
        from llama_orchestrator.gui.table import (
            SORT_ASC,
            SORT_DESC,
            _make_sort_callback,
        )

        callback = _make_sort_callback("name", mock_tree)

        # First click: asc
        callback()
        first_heading = mock_tree.heading.call_args_list[-1].kwargs["text"]
        assert SORT_ASC in first_heading

        # Second click: desc — set heading to show ASC so callback detects asc
        mock_tree.heading.return_value = {"text": "Name " + SORT_ASC}
        callback()
        second_heading = mock_tree.heading.call_args_list[-1].kwargs["text"]
        assert SORT_DESC in second_heading
        assert SORT_ASC not in second_heading

    def test_sort_moves_items_once(self, mock_tree):
        """Sort should call tree.move() exactly once per item."""
        from llama_orchestrator.gui.table import _make_sort_callback

        callback = _make_sort_callback("name", mock_tree)
        callback()

        # Verify move() was called for each child (3 items)
        assert mock_tree.move.call_count == 3

    def test_sort_toggles_direction(self, mock_tree):
        """Clicking twice should sort ascending then descending."""
        from llama_orchestrator.gui.table import (
            SORT_ASC,
            SORT_DESC,
            _make_sort_callback,
        )

        callback = _make_sort_callback("name", mock_tree)

        # First click: ascending
        callback()
        assert mock_tree.heading.call_args.kwargs["text"] == "Name " + SORT_ASC

        # Second click: descending
        mock_tree.heading.return_value = {"text": "Name " + SORT_ASC}
        callback()
        assert mock_tree.heading.call_args.kwargs["text"] == "Name " + SORT_DESC


class TestGetTreeColumnFromEvent:
    """Verify get_tree_column_from_event returns column names, not indices."""

    @pytest.fixture
    def mock_tree(self):
        tree = MagicMock()
        tree.__getitem__ = MagicMock(return_value=["name", "status", "health"])
        return tree

    def test_returns_column_name(self, mock_tree):
        """identify_column returns '#2', function should return 'status'."""
        from llama_orchestrator.gui.table import get_tree_column_from_event

        mock_tree.identify_column.return_value = "#2"
        event = MagicMock()
        event.x = 0

        result = get_tree_column_from_event(event, mock_tree)
        assert result == "status"

    def test_returns_first_column(self, mock_tree):
        """'#1' should return the first column name."""
        from llama_orchestrator.gui.table import get_tree_column_from_event

        mock_tree.identify_column.return_value = "#1"
        event = MagicMock()
        event.x = 0

        result = get_tree_column_from_event(event, mock_tree)
        assert result == "name"

    def test_returns_none_on_empty_col_id(self, mock_tree):
        """Empty col_id should return None."""
        from llama_orchestrator.gui.table import get_tree_column_from_event

        mock_tree.identify_column.return_value = ""
        event = MagicMock()
        event.x = 0

        result = get_tree_column_from_event(event, mock_tree)
        assert result is None

    def test_returns_none_on_no_col_id(self, mock_tree):
        """None col_id should return None."""
        from llama_orchestrator.gui.table import get_tree_column_from_event

        mock_tree.identify_column.return_value = None
        event = MagicMock()
        event.x = 0

        result = get_tree_column_from_event(event, mock_tree)
        assert result is None

    def test_returns_none_on_out_of_range(self, mock_tree):
        """Column index beyond range should return None."""
        from llama_orchestrator.gui.table import get_tree_column_from_event

        mock_tree.identify_column.return_value = "#99"
        event = MagicMock()
        event.x = 0

        result = get_tree_column_from_event(event, mock_tree)
        assert result is None


# ──────────────────────────────────────────────────────────────────────
# gpu_inventory.py tests
# ──────────────────────────────────────────────────────────────────────


class TestGpuInventory:
    """Verify alias button is shown only when GPU name is known."""

    def test_alias_button_shown_only_when_gpu_name_known(self):
        """GPU with name -> button rendered; GPU with name=None -> no button."""
        from llama_orchestrator.gui.gpu_inventory import render_gpu_inventory
        from llama_orchestrator.engine.detection import DetectedGpu

        # Create a parent mock with winfo_children returning empty
        parent = MagicMock()
        parent.winfo_children.return_value = []
        parent.columnconfigure = MagicMock()

        gpus = [
            DetectedGpu(label="GPU 0", name="NVIDIA RTX 4090"),
            DetectedGpu(label="GPU 1", name=None),
        ]
        aliases: dict[str, str] = {}

        widgets = render_gpu_inventory(parent, gpus, aliases, 15)

        # Should have 2 labels + 1 button (only for GPU 0 with name)
        assert len(widgets) == 3

    def test_render_clears_existing_widgets(self):
        """Second render call should destroy first render's widgets."""
        from llama_orchestrator.gui.gpu_inventory import render_gpu_inventory
        from llama_orchestrator.engine.detection import DetectedGpu

        # Track widgets created so we can return them on second call
        created_widgets: list = []

        class MockWidget(MagicMock):
            pass

        # Use a factory that appends to the list
        def widget_factory(*a, **kw):
            w = MockWidget()
            created_widgets.append(w)
            return w

        parent = MagicMock()
        parent.winfo_children.side_effect = lambda: created_widgets[:]
        parent.columnconfigure = MagicMock()

        # Patch tk.Label and tk.Button to use our factory
        with patch("tkinter.Label", widget_factory), \
             patch("tkinter.Button", widget_factory):
            gpus = [
                DetectedGpu(label="GPU 0", name="NVIDIA RTX 4090"),
            ]

            # First render
            widgets1 = render_gpu_inventory(parent, gpus, {}, 15)
            assert len(created_widgets) == 2  # 1 label + 1 button (name is known)

            # Second render — should destroy first render's widgets
            widgets2 = render_gpu_inventory(parent, gpus, {}, 15)
            for w in widgets1:
                assert w.destroy.called

    def test_no_alias_button_when_gpu_name_none(self):
        """GPU with name=None should NOT render an alias button."""
        from llama_orchestrator.gui.gpu_inventory import render_gpu_inventory
        from llama_orchestrator.engine.detection import DetectedGpu

        parent = MagicMock()
        parent.winfo_children.return_value = []
        parent.columnconfigure = MagicMock()

        gpus = [
            DetectedGpu(label="GPU 0", name=None),
        ]

        widgets = render_gpu_inventory(parent, gpus, {}, 15)

        # Only 1 widget (the label), no button
        assert len(widgets) == 1


# ──────────────────────────────────────────────────────────────────────
# metadata_cache.py tests
# ──────────────────────────────────────────────────────────────────────


class TestMetadataCacheThreadSafety:
    """Verify thread-safe cache operations."""

    def test_concurrent_read_write_no_corruption(self):
        """10 writer threads + 10 reader threads, 100 ops each — no errors."""
        from llama_orchestrator.gui.metadata_cache import MetadataCache

        cache = MetadataCache(max_size=1000)
        errors = []
        barrier = threading.Barrier(20)

        def writer(thread_id: int):
            try:
                for i in range(100):
                    cache.set(f"key:{thread_id}:{i}", f"value:{i}")
            except Exception as exc:
                errors.append(exc)

        def reader(thread_id: int):
            try:
                for i in range(100):
                    cache.get(f"key:{thread_id % 10}:{i}")
            except Exception as exc:
                errors.append(exc)

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {[str(e) for e in errors]}"

    def test_ttl_expiry(self):
        """Entry expires after ttl_seconds."""
        from llama_orchestrator.gui.metadata_cache import MetadataCache

        cache = MetadataCache(max_size=1000)
        cache.set("expire_key", "value", ttl_seconds=0.1)

        # Should be present immediately
        assert cache.get("expire_key") == "value"

        # Wait for expiry
        time.sleep(0.15)

        # Should be expired
        assert cache.get("expire_key") is None

    def test_lru_eviction_at_max_size(self):
        """Oldest entry removed when max_size reached."""
        from llama_orchestrator.gui.metadata_cache import MetadataCache

        cache = MetadataCache(max_size=3)
        cache.set("a", 1, ttl_seconds=300)
        time.sleep(0.01)
        cache.set("b", 2, ttl_seconds=300)
        time.sleep(0.01)
        cache.set("c", 3, ttl_seconds=300)
        time.sleep(0.01)
        cache.set("d", 4, ttl_seconds=300)  # Should evict "a"

        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.get("d") == 4
        assert cache.size() == 3

    def test_invalidate_instance_cache_thread_safe(self):
        """invalidate_instance_cache with concurrent set() calls."""
        from llama_orchestrator.gui.metadata_cache import (
            MetadataCache,
            invalidate_instance_cache,
        )

        cache = MetadataCache(max_size=1000)
        errors = []

        # Pre-populate
        for i in range(50):
            cache.set(f"instance:test:{i}", f"data{i}")
        cache.set("other:key", "other_value")

        def writer():
            try:
                for i in range(50, 100):
                    cache.set(f"instance:test:{i}", f"data{i}")
            except Exception as exc:
                errors.append(exc)

        def invalidator():
            try:
                invalidate_instance_cache(cache, "test")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=invalidator),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {[str(e) for e in errors]}"

        # All instance:test:* keys should be gone
        for i in range(100):
            assert cache.get(f"instance:test:{i}") is None

        # Other keys should remain
        assert cache.get("other:key") == "other_value"

    def test_rlock_reentrant_eviction(self):
        """_evict_oldest called from set() should not deadlock (RLock re-entrant)."""
        from llama_orchestrator.gui.metadata_cache import MetadataCache

        cache = MetadataCache(max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)

        # This should NOT deadlock — set() holds the lock, _evict_oldest()
        # is called from within set(), and RLock allows re-entry.
        cache.set("c", 3)  # Should evict "a"

        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_clear_is_thread_safe(self):
        """clear() should not corrupt state during concurrent access."""
        from llama_orchestrator.gui.metadata_cache import MetadataCache

        cache = MetadataCache(max_size=1000)
        errors = []

        def writer():
            try:
                for i in range(100):
                    cache.set(f"key:{i}", i)
            except Exception as exc:
                errors.append(exc)

        def clearer():
            try:
                for _ in range(50):
                    cache.clear()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=clearer),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {[str(e) for e in errors]}"
