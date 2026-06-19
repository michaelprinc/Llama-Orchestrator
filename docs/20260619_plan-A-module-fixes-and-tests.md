# Plan A — Module Fixes & Unit Tests
# llama-orchestrator GUI Refactoring Phase 2

> Independent workstream — safe to execute in parallel with Plan B.
> No changes to app.py, __init__.py, or any file that Plan B also touches.

---

## Scope

Fix 3 remaining buggy modules and add/extend unit tests for all fixed
extraction modules. This plan touches exclusively:

- src/llama_orchestrator/gui/table.py
- src/llama_orchestrator/gui/gpu_inventory.py
- src/llama_orchestrator/gui/metadata_cache.py
- tests/test_gui_modules.py  (new file — does NOT overlap with test_gui.py)

Zero overlap with Plan B. Can be merged independently.

---

## Session Context — What Has Already Been Fixed

The following modules were fixed in the current session and require NO
further changes; they are listed here only for reference:

| Module | Status |
|---|---|
| health.py | Fixed — aiohttp replaced with httpx |
| benchmark_controls.py | Fixed — duplicate BenchmarkSettings removed, ttk.Frame |
| actions.py | Fixed — tk.Tk() -> tk.Toplevel, mainloop() -> wait_window() |
| row_renderer.py | Fixed — duplicate TableRow removed, dead code deleted |
| toolbar.py | Fixed — ToolbarCallbacks added, all buttons wired |

---

## Task 1 — Fix `table.py` (2 bugs)

File: src/llama_orchestrator/gui/table.py

### Bug 1 — `_make_sort_callback`: both branches are identical (lines 140-143)

Current code:
```python
if reverse:
    tree.heading(column, command=_make_sort_callback(column, tree))
else:
    tree.heading(column, command=_make_sort_callback(column, tree))
```

Both branches do the same thing — they update the command but never update
the heading TEXT with the sort indicator (SORT_ASC / SORT_DESC).

Fix: Replace the entire `sort_callback` inner function body:

```python
def sort_callback() -> None:
    heading_info = tree.heading(column)
    heading_text = heading_info.get("text", "") if heading_info else ""

    # Determine current sort direction by looking at the indicator in text
    currently_desc = SORT_DESC in str(heading_text)

    # Sort items
    items = [(tree.set(child, column), child) for child in tree.get_children()]
    items.sort(reverse=not currently_desc)   # toggle: was desc -> now asc
    for index, (_, child) in enumerate(items):
        tree.move(child, "", index)

    # Strip any existing indicator and re-apply the toggled one
    base = (
        str(heading_text)
        .replace(f" {SORT_ASC}", "")
        .replace(f" {SORT_DESC}", "")
        .strip()
    )
    new_indicator = SORT_ASC if not currently_desc else SORT_DESC
    tree.heading(column, text=f"{base} {new_indicator}", command=sort_callback)
```

### Bug 2 — `get_tree_column_from_event` returns index string, not column name (line 189)

Current code:
```python
col = tree.identify_column(event.x)
if col:
    return col[1:]  # Remove the '#' prefix  -> returns "1", "2", ...
return None
```

`tree.identify_column()` returns "#1", "#2", etc. Stripping "#" yields "1",
"2" — still an index, not the column name like "name" or "status".

Fix:
```python
def get_tree_column_from_event(event: tk.Event, tree: ttk.Treeview) -> str | None:
    col_id = tree.identify_column(event.x)
    if not col_id:
        return None
    try:
        col_index = int(col_id[1:]) - 1   # "#1" -> 0
        columns = tree["columns"]
        if 0 <= col_index < len(columns):
            return columns[col_index]
    except (ValueError, IndexError):
        pass
    return None
```

---

## Task 2 — Fix `gpu_inventory.py` (1 bug)

File: src/llama_orchestrator/gui/gpu_inventory.py

### Bug — Alias button condition always True (line 93)

Current code (inside `for gpu in gpus:` loop):
```python
if len(aliases) > 0 or len(gpus) > 0:
    alias_btn = tk.Button(...)
```

Inside the for-loop, `gpus` is the outer sequence and always has len > 0,
so this condition is ALWAYS True — the alias button is shown for every GPU
including those without a detectable adapter name.

Fix: Replace the condition with a check on `gpu_name` (the local variable
already defined 2 lines above):
```python
if gpu_name:   # Only show alias button if adapter name is known
    alias_btn = tk.Button(...)
```

---

## Task 3 — Fix `metadata_cache.py` (thread safety)

File: src/llama_orchestrator/gui/metadata_cache.py

### Issue — No lock on _entries dict

The `MetadataCache._entries` dict is mutated in `set()`, `delete()`, and
`_evict_oldest()`. The RefreshController (refresh.py) runs on a background
thread and will call these methods concurrently with the main Tkinter thread.
Without a lock, dict mutation from two threads causes data corruption.

Fix: Add `threading.RLock` (re-entrant lock, safe for recursive calls within
the same thread) to the dataclass and wrap all `_entries` mutations.

Step 1 — Add import at top of file:
```python
import threading
```

Step 2 — Add `_lock` field to `MetadataCache`:
```python
@dataclass
class MetadataCache:
    """Thread-safe cache for engine and instance metadata."""
    _entries: dict[str, CacheEntry] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    max_size: int = 1000
```

Step 3 — Wrap `get()`, `set()`, `delete()`, `clear()`, `size()`,
`_evict_oldest()` with `with self._lock:`:

```python
def get(self, key: str) -> Any | None:
    with self._lock:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.time() - entry.created_at > entry.ttl_seconds:
            del self._entries[key]
            return None
        return entry.value

def set(self, key: str, value: Any, ttl_seconds: float = 300.0) -> None:
    with self._lock:
        if len(self._entries) >= self.max_size:
            self._evict_oldest()
        self._entries[key] = CacheEntry(value=value, ttl_seconds=ttl_seconds)

def delete(self, key: str) -> bool:
    with self._lock:
        if key in self._entries:
            del self._entries[key]
            return True
        return False

def clear(self) -> None:
    with self._lock:
        self._entries.clear()

def size(self) -> int:
    with self._lock:
        return len(self._entries)

def _evict_oldest(self) -> None:
    # Called only from within set(), which already holds the lock.
    # RLock allows re-entry, so this is safe.
    if not self._entries:
        return
    oldest_key = min(
        self._entries,
        key=lambda k: self._entries[k].created_at,
    )
    del self._entries[oldest_key]
```

Also fix `invalidate_instance_cache()` — currently accesses `cache._entries`
directly without the lock. Fix:
```python
def invalidate_instance_cache(cache: MetadataCache, instance_name: str) -> None:
    with cache._lock:
        keys_to_delete = [
            key for key in cache._entries
            if key.startswith(f"instance:{instance_name}:")
        ]
    for key in keys_to_delete:
        cache.delete(key)   # delete() acquires the lock individually
```

---

## Task 4 — New Unit Tests

File: tests/test_gui_modules.py  (NEW — does not modify test_gui.py)

Create a new test file covering the 3 fixed modules. Use `unittest.mock` and
`pytest` with no additional dependencies.

### Test coverage required

#### table.py tests

```python
class TestTableSortCallback:
    def test_sort_asc_sets_indicator(self, tree):
        # After first click: heading should contain SORT_ASC
        ...

    def test_sort_desc_on_second_click(self, tree):
        # After second click on same column: indicator toggles to SORT_DESC
        ...

    def test_get_tree_column_from_event_returns_name(self, tree):
        # identify_column returns "#2", function must return columns[1] name
        ...

    def test_get_tree_column_from_event_invalid(self, tree):
        # Empty col_id returns None
        ...
```

#### gpu_inventory.py tests

```python
class TestGpuInventory:
    def test_alias_button_shown_only_when_gpu_name_known(self, parent):
        # GPU with name -> button rendered
        # GPU with name=None -> no alias button
        ...

    def test_render_clears_existing_widgets(self, parent):
        # Second render call should destroy first render's widgets
        ...
```

#### metadata_cache.py tests

```python
class TestMetadataCacheThreadSafety:
    def test_concurrent_read_write_no_corruption(self):
        # 10 writer threads + 10 reader threads, 100 ops each
        # No KeyError or RuntimeError after all threads complete
        ...

    def test_ttl_expiry(self):
        # Entry expires after ttl_seconds
        ...

    def test_lru_eviction_at_max_size(self):
        # Oldest entry removed when max_size reached
        ...

    def test_invalidate_instance_cache_thread_safe(self):
        # invalidate_instance_cache with concurrent set() calls
        ...
```

---

## Acceptance Criteria

- [ ] `table.py`: Column header click toggles ↑/↓ indicator in heading text
- [ ] `table.py`: `get_tree_column_from_event` returns "name", "status", etc. — not "1", "2"
- [ ] `gpu_inventory.py`: GPU rows with `name=None` render without alias button
- [ ] `gpu_inventory.py`: GPU rows with `name="NVIDIA RTX..."` render with alias button
- [ ] `metadata_cache.py`: Concurrent read/write test completes without error
- [ ] `metadata_cache.py`: `_evict_oldest` is safe when called from `set()` (RLock re-entrant)
- [ ] All tests in `tests/test_gui_modules.py` pass with `pytest -v`
- [ ] `tests/test_gui.py` and all existing tests continue to pass unchanged

## Files Touched (Plan A only)

| File | Change |
|---|---|
| src/llama_orchestrator/gui/table.py | Fix sort callback, fix column ID |
| src/llama_orchestrator/gui/gpu_inventory.py | Fix alias button condition |
| src/llama_orchestrator/gui/metadata_cache.py | Add threading.RLock |
| tests/test_gui_modules.py | NEW — unit tests for 3 fixed modules |

## Files NOT Touched by Plan A (reserved for Plan B)

app.py, __init__.py, refresh.py, activity_log.py, toolbar.py, actions.py,
benchmark_controls.py, row_renderer.py, health.py, dialogs.py, any *.bak,
gui.py (root-level), any file outside src/llama_orchestrator/gui/ and tests/
