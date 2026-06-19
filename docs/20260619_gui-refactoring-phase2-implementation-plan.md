# llama-orchestrator GUI Refactoring — Phase 2 Implementation Plan

> Date: 2026-06-19 · Based on code review v2
> Source spec: REFACTORING_SPEC.md
> Status: DRAFT — pending approval

---

## 1. Objective

Complete the WS-5 module extraction workstream by:

1. **Fixing all 15 identified bugs** in the 6 new extraction modules
2. **Wiring extracted modules** into `app.py` so extracted code is actually called
3. **Removing extracted code from `app.py`** to reduce it from 2 536 → ~1 200 lines
4. **Deleting the legacy `gui.py`** fallback (151 KB)
5. **Validating** that all existing tests pass and the GUI launches correctly

---

## 2. Current State Baseline

| File | Lines | Status |
|---|---:|---|
| `app.py` | 2 536 | Source of truth — not yet reduced |
| `app.py.bak` | ~3 000 | Legacy backup — delete after validation |
| `gui.py` (root) | ~3 814 | Legacy fallback — delete after validation |
| `activity_log.py` | 67 | Clean |
| `gpu_inventory.py` | 169 | Fixed — 1 minor bug remaining |
| `toolbar.py` | 126 | Skeleton — callbacks missing, 3 bugs |
| `table.py` | 208 | 2 bugs |
| `row_renderer.py` | 104 | Duplicate TableRow, dead code |
| `benchmark_controls.py` | 103 | 3 critical bugs |
| `actions.py` | 166 | 2 critical bugs |
| `health.py` | 180 | Wrong dependency (aiohttp vs httpx) |
| `metadata_cache.py` | 134 | Clean — thread-safety advisory only |

---

## 3. Implementation Batches

Work is split into 4 sequential batches. Each batch must be committed and verified before starting the next.

---

### Batch 1 — Critical Bug Fixes (no behavior change)

**Goal:** Fix all bugs that cause ImportError, TclError, or AttributeError at runtime.
No functional logic changes yet — this batch only makes the modules safe to import.

#### 1.1 `health.py` — Replace `aiohttp` with `httpx`

`aiohttp` is not listed in `pyproject.toml` dependencies. The project already uses `httpx>=0.25`.
The spec (WS-2 section 6.1) explicitly proposes `httpx.AsyncClient`.

**Changes:**
- Remove `import aiohttp` and `from aiohttp import ClientSession, ClientTimeout`
- Replace with `import httpx`
- Replace `aiohttp.ClientSession` with `httpx.AsyncClient`
- Replace `aiohttp.ClientTimeout` with `httpx.Timeout`
- Replace `ClientTimeout(total=5.0)` with `httpx.Timeout(5.0)`
- Replace `async with aiohttp.ClientSession() as session:` with `async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:`
- Replace `session.get(url)` with `client.get(url)`
- Replace `resp.status` with `resp.status_code`
- Remove `HealthClientPool` dataclass (references `ClientSession` — not needed with shared `httpx.AsyncClient`)
- Add `_get_shared_client()` factory following the pattern in spec section 6.1

**Acceptance:** `python -c "from llama_orchestrator.gui.health import check_health"` runs without ImportError.

---

#### 1.2 `benchmark_controls.py` — 3 critical fixes

**Fix A — Remove duplicate `BenchmarkSettings` class**

Delete the entire local `BenchmarkSettings` dataclass (lines 17-25). It duplicates
`llama_orchestrator.benchmark.BenchmarkSettings` with incompatible fields
(`endpoint: bool` vs `endpoint: str`). Nothing in `benchmark_controls.py` references
the local class after removal.

**Fix B — `tk.Frame(padding=)` -> `ttk.Frame`**

```python
# BEFORE:
frame = tk.Frame(parent, padding=5)

# AFTER:
frame = ttk.Frame(parent, padding=5)
```

Add `from tkinter import ttk` to the imports.

**Fix C — `_run_benchmark_action` placeholder**

Replace hardcoded stub with a clearly-named no-op:

```python
def _noop_benchmark_action() -> str | None:
    """Default no-op — replace via configure_benchmark_buttons callback."""
    return None
```

**Acceptance:** `python -c "from llama_orchestrator.gui.benchmark_controls import build_benchmark_frame"` runs without error.

---

#### 1.3 `actions.py` — Fix illegal `tk.Tk()` creation

Both `copy_cli_command` and `rename_instance` create a second `tk.Tk()` instance,
which is illegal in a running Tkinter application.

**Fix A — `copy_cli_command`**

```python
# BEFORE:
def copy_cli_command(instance_name, get_cli_command_fn):
    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(command)
    root.destroy()

# AFTER:
def copy_cli_command(
    instance_name: str,
    get_cli_command_fn: Callable[[str], str],
    root: tk.Misc,
) -> None:
    command = get_cli_command_fn(instance_name)
    root.clipboard_clear()
    root.clipboard_append(command)
    messagebox.showinfo("Copied", "CLI command copied to clipboard")
```

**Fix B — `rename_instance`**

```python
# BEFORE:
def rename_instance(...) -> bool:
    dialog = tk.Tk()      # WRONG — creates second root
    ...
    dialog.mainloop()     # WRONG — nested event loop

# AFTER:
def rename_instance(
    instance_name: str,
    current_display_name: str,
    confirm_fn: Callable[[str, str], bool],
    parent: tk.Misc,
) -> bool:
    dialog = tk.Toplevel(parent)   # correct
    dialog.transient(parent)
    dialog.grab_set()
    ...
    parent.wait_window(dialog)    # correct — no nested mainloop
    return result[0]
```

**Fix C — `_show_diff_window` parent parameter**

```python
def _show_diff_window(name1: str, name2: str, diff_text: str, parent: tk.Misc) -> None:
    dialog = tk.Toplevel(parent)
```

**Acceptance:** Both `copy_cli_command` and `rename_instance` import cleanly.
Signatures must be updated in all callers in `app.py`.

---

#### 1.4 `row_renderer.py` — Remove duplicate `TableRow`

**Fix A — Remove duplicate class, import from canonical location**

Delete the local `TableRow` dataclass. Add:

```python
from llama_orchestrator.gui.dataclasses import TableRow
```

**Fix B — Remove dead branch in `filter_visible_rows`**

```python
# AFTER (clean):
def filter_visible_rows(
    rows: list[TableRow],
    start_idx: int | None = None,
    end_idx: int | None = None,
) -> list[TableRow]:
    return rows[start_idx:end_idx]
```

**Fix C — Delete `render_refresh_metadata` noop**

The function configures a random, unused Treeview tag. Delete entirely.

**Acceptance:** `from llama_orchestrator.gui.row_renderer import build_table_rows` imports cleanly.

---

### Batch 2 — Module Completion (functional correctness)

**Goal:** Fix structural issues so extracted modules are fully functional, not just importable.

#### 2.1 `toolbar.py` — Wire callbacks and fix widget parents

**Fix A — Add `ToolbarCallbacks` dataclass and `callbacks` parameter**

```python
from dataclasses import dataclass
from collections.abc import Callable

@dataclass
class ToolbarCallbacks:
    on_refresh: Callable[[], None]
    on_add_model: Callable[[], None]
    on_apply_args: Callable[[], None]
    on_install_llama_server: Callable[[], None]
    on_start: Callable[[], None]
    on_stop: Callable[[], None]
    on_restart: Callable[[], None]
    on_health: Callable[[], None]
    on_edit_prompt: Callable[[], None]
    on_toggle_gpu_inventory: Callable[[], None]
    on_start_daemon: Callable[[], None]
    on_stop_daemon: Callable[[], None]
    on_column_toggle: Callable[[str, bool], None]
    on_tag_filter: Callable[[str], None]
```

Update `build_toolbar()` signature to accept `callbacks: ToolbarCallbacks` and wire
all buttons with `command=`.

**Fix B — `_build_columns_menu` parent**

```python
# BEFORE:
columns_menu = tk.Menu(tk.Menu(), tearoff=False)

# AFTER:
columns_menu = tk.Menu(toolbar, tearoff=False)
```

**Fix C — `BooleanVar` master**

```python
# BEFORE:
var = tk.BooleanVar(value=column in visible_columns)

# AFTER:
var = tk.BooleanVar(master=toolbar, value=column in visible_columns)
```

**Acceptance:** `build_toolbar()` renders a fully functional toolbar with all buttons wired.

---

#### 2.2 `table.py` — Fix sort indicator and column identification

**Fix A — `_make_sort_callback` heading update (both branches were identical)**

```python
def sort_callback() -> None:
    heading_text = tree.heading(column).get("text", "")
    currently_desc = SORT_DESC in heading_text
    items = [(tree.set(child, column), child) for child in tree.get_children()]
    items.sort(reverse=not currently_desc)
    for index, (_, child) in enumerate(items):
        tree.move(child, "", index)
    base = heading_text.replace(f" {SORT_ASC}", "").replace(f" {SORT_DESC}", "")
    indicator = SORT_ASC if not currently_desc else SORT_DESC
    tree.heading(column, text=f"{base} {indicator}", command=sort_callback)
```

**Fix B — `get_tree_column_from_event` must return column name, not index string**

```python
def get_tree_column_from_event(event: tk.Event, tree: ttk.Treeview) -> str | None:
    col_id = tree.identify_column(event.x)
    if not col_id:
        return None
    try:
        col_index = int(col_id[1:]) - 1   # "#1" -> 0
        return tree["columns"][col_index]
    except (ValueError, IndexError):
        return None
```

**Acceptance:** Column header click shows correct sort indicator; right-click returns correct column name.

---

#### 2.3 `gpu_inventory.py` — Fix alias button condition

```python
# BEFORE (always True inside for loop):
if len(aliases) > 0 or len(gpus) > 0:

# AFTER (show only for GPUs with a known adapter name):
if gpu_name:
```

**Acceptance:** GPUs without a detected `name` render without an alias button.

---

#### 2.4 `metadata_cache.py` — Thread safety

Add `threading.RLock` to protect concurrent access from background RefreshController thread:

```python
import threading

@dataclass
class MetadataCache:
    _entries: dict[str, CacheEntry] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    max_size: int = 1000

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            ...

    def set(self, key: str, value: Any, ttl_seconds: float = 300.0) -> None:
        with self._lock:
            if len(self._entries) >= self.max_size:
                self._evict_oldest()
            self._entries[key] = CacheEntry(value=value, ttl_seconds=ttl_seconds)
```

Use `RLock` (re-entrant lock) — safe for recursive calls within the same thread.

**Acceptance:** No deadlocks under concurrent read/write; existing tests pass.

---

### Batch 3 — `app.py` Integration and Reduction

**Goal:** Wire all extracted modules into `app.py`, then delete the duplicated code.
Target: `app.py` from 2 536 -> ~1 400 lines.

> IMPORTANT: Do this one module at a time. Wire -> test -> delete.
> Never delete before confirming the wired version works.

#### 3.1 Integration strategy

For each extracted module, follow this 3-step process:

1. **Wire:** Add an import of the extracted function in `app.py`. Call it from the
   existing method, replacing the inline implementation with a delegation.
2. **Test:** Run the GUI manually. Confirm the feature works identically.
3. **Delete:** Remove the now-redundant inline implementation from `app.py`.

#### 3.2 Module-by-module integration order

| Step | Module | `app.py` methods to delegate | Lines freed (est.) |
|---|---|---|---:|
| 3.2.1 | `activity_log` | `_schedule_message_pump`, `_pump_messages`, `_append_activity` | ~30 |
| 3.2.2 | `gpu_inventory` | `_render_gpu_inventory`, `_edit_gpu_alias`, `_toggle_gpu_inventory` | ~85 |
| 3.2.3 | `table` | `_apply_visible_columns`, `_refresh_tree_headings`, `_toggle_sort`, `_on_tree_click`, `_on_tree_double_click`, `_tree_column_from_event` | ~120 |
| 3.2.4 | `row_renderer` | `_render_full_rows`, `_visible_rows`, `_build_table_rows` (partial) | ~200 |
| 3.2.5 | `toolbar` | `_build_widgets` (toolbar section), `_render_daemon_status` | ~200 |
| 3.2.6 | `benchmark_controls` | `_update_benchmark_controls`, `_begin_benchmark_job`, `_finish_benchmark_job`, `_benchmark_job_running` | ~80 |
| 3.2.7 | `actions` | `_copy_cli_command`, `_rename_instance`, `_clone_instance`, `_diff_instances` | ~120 |

Estimated total reduction: ~835 lines, bringing `app.py` from 2 536 -> ~1 700 lines.

Note: The spec target of ~1 200 lines requires further extraction of `_build_table_rows`
(the largest remaining method at ~150 lines) and parts of `_build_widgets`. These are
lower-risk and can be done in an additional extraction cycle after Batch 4.

#### 3.3 `_build_widgets` refactoring sketch

After toolbar wiring, `_build_widgets` should delegate to each module:

```python
def _build_widgets(self) -> None:
    # Root layout
    self.columnconfigure(0, weight=1)
    self.rowconfigure(1, weight=1)

    # Toolbar — delegated
    callbacks = ToolbarCallbacks(
        on_refresh=self.refresh,
        on_add_model=self._add_model,
        on_start=lambda: self._run_selected("start"),
        on_stop=lambda: self._run_selected("stop"),
        on_restart=lambda: self._run_selected("restart"),
        on_health=self._run_health_check,
        on_edit_prompt=self._edit_benchmark_prompt,
        on_toggle_gpu_inventory=self._toggle_gpu_inventory,
        on_start_daemon=self._start_daemon,
        on_stop_daemon=self._stop_daemon,
        on_column_toggle=self._on_column_toggle,
        on_tag_filter=self._on_tag_filter_change,
        on_apply_args=self._apply_runtime_args,
        on_install_llama_server=self._install_llama_server,
    )
    self.toolbar = build_toolbar(
        self,
        callbacks=callbacks,
        column_headings=COLUMN_HEADINGS,
        all_columns=ALL_COLUMNS,
        visible_columns=self.gui_settings.visible_columns,
        tag_filter_var=self.tag_filter_var,
        show_gpu_inventory_var=self.show_gpu_inventory_var,
        prompt_var=self.prompt_var,
    )
    self.toolbar.grid(row=0, column=0, sticky="ew")

    # GPU inventory — delegated
    self.gpu_inventory_frame, self.gpu_inventory_rows = build_gpu_inventory_frame(self)

    # Main table — delegated
    self.tree, _ = build_tree(
        self, ALL_COLUMNS, COLUMN_HEADINGS, self.gui_settings.visible_columns
    )

    # Activity log — delegated
    _, self.activity, _ = build_activity_log_frame(self)
```

---

### Batch 4 — Cleanup and Validation

**Goal:** Remove all legacy files, verify spec acceptance criteria, run test suite.

#### 4.1 Delete legacy files

- Delete `src/llama_orchestrator/gui/app.py.bak`
- Delete `src/llama_orchestrator/gui.py` (root-level legacy fallback, 151 KB)
- Verify `__init__.py` no longer references the fallback path

Before deleting `gui.py`, confirm no remaining import paths reference it:

```powershell
Select-String -Path "src\**\*.py" -Pattern "from llama_orchestrator import gui" -Recurse
Select-String -Path "src\**\*.py" -Pattern "import gui" -Recurse
```

#### 4.2 `__init__.py` cleanup

Remove the duplicate ExistingModelFileDialog import:

```python
# Remove this line:
from llama_orchestrator.gui.dialogs import (
    ExistingModelFileDialog as _ExistingModelFileDialog,
)
```

Verify `__all__` still exports all names required by CLI and tests.

#### 4.3 `app.py` final size check

```powershell
(Get-Content src\llama_orchestrator\gui\app.py).Count
```

Target: <= 1 400 lines (achievable within this plan).
The spec target of 1 200 lines may require one additional extraction cycle.

#### 4.4 Run test suite

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v --tb=short 2>&1 | Tee-Object -FilePath docs\test_results_phase2.txt
```

All tests must pass. Pay special attention to:
- `tests/test_gui*.py` — GUI rendering and refresh tests
- `tests/test_health*.py` — After aiohttp -> httpx migration
- `tests/test_benchmark*.py` — After BenchmarkSettings deduplication

#### 4.5 Manual smoke test

Launch the GUI and verify:

1. GUI opens without error or console traceback
2. All toolbar buttons are clickable and functional
3. Refresh cycle works (instances appear in table)
4. GPU inventory panel toggles correctly
5. Alias edit dialog opens as proper modal (Toplevel, not new root window)
6. CLI copy command works without creating a new window
7. Rename dialog opens as Toplevel, closes correctly, does not block
8. Column sort indicator updates correctly (up/down arrow)
9. Health check endpoint executes without ImportError

---

## 4. Files Changed Summary

| File | Action | Batch |
|---|---|---|
| `gui/health.py` | Replace aiohttp with httpx | 1 |
| `gui/benchmark_controls.py` | Remove duplicate BenchmarkSettings, fix tk.Frame | 1 |
| `gui/actions.py` | Fix tk.Tk() -> tk.Toplevel, remove mainloop() | 1 |
| `gui/row_renderer.py` | Remove duplicate TableRow, dead code | 1 |
| `gui/toolbar.py` | Add ToolbarCallbacks, fix parents, wire command= | 2 |
| `gui/table.py` | Fix sort callback, fix column name resolution | 2 |
| `gui/gpu_inventory.py` | Fix alias button condition | 2 |
| `gui/metadata_cache.py` | Add threading.RLock | 2 |
| `gui/app.py` | Wire extractions, delete delegated methods | 3 |
| `gui/__init__.py` | Remove ExistingModelFileDialog alias | 4 |
| `gui/app.py.bak` | DELETE | 4 |
| `gui.py` (root-level) | DELETE | 4 |

---

## 5. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| toolbar.py callback wiring breaks existing button behavior | Medium | High | Wire one button at a time; keep original _build_widgets intact until toolbar section confirmed working |
| app.py method deletion breaks a test that calls a private method | Low | Medium | Run full test suite after every deletion; revert immediately on failure |
| httpx.AsyncClient lifecycle conflicts with existing httpx usage | Low | Medium | Use module-level singleton with atexit cleanup; check health/ module for shared clients |
| threading.RLock on MetadataCache causes deadlock on recursive call | Low | High | Use RLock (re-entrant), not Lock — specified above |
| Legacy gui.py deletion breaks undiscovered import path | Low | High | grep for all import paths before deletion (see section 4.1) |

---

## 6. Out of Scope for This Plan

The following REFACTORING_SPEC.md items are explicitly excluded and should be
addressed in a separate subsequent cycle:

- WS-1: RefreshController background thread and row-level diffing
- WS-2: Full async health monitor with per-instance check intervals
- WS-3: MetadataCache integration with describe_effective_runtime() caching
- WS-4: Action bar redesign, context menu grouping, status icon indicators
- WS-6: requirements.txt generation, ruff/mypy zero-warning pass

---

## 7. Acceptance Criteria (WS-5 from spec section 9.3)

- [ ] No module in gui/ exceeds 600 lines
- [ ] app.py reduced to <= 1 400 lines
- [ ] gui.py (legacy root-level file) deleted
- [ ] All tests in tests/ pass with zero failures
- [ ] GUI launches and all 9 manual smoke test items pass (section 4.5)
- [ ] No `import aiohttp` anywhere in the gui/ package
- [ ] No second tk.Tk() instantiation anywhere in gui/ (except app.py root class)
- [ ] No duplicate class definitions (TableRow, BenchmarkSettings) across gui/
