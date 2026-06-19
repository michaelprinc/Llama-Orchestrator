# Plan B — app.py Integration, Wiring & Cleanup
# llama-orchestrator GUI Refactoring Phase 2

> Independent workstream — safe to execute in parallel with Plan A.
> No changes to table.py, gpu_inventory.py, or metadata_cache.py
> (those are covered exclusively by Plan A).

---

## Scope

Wire all already-fixed extracted modules into app.py (delegate -> delete),
clean up __init__.py, and remove legacy files. This plan touches:

- src/llama_orchestrator/gui/app.py         (primary target)
- src/llama_orchestrator/gui/__init__.py    (minor cleanup)
- src/llama_orchestrator/gui/app.py.bak    (delete)
- src/llama_orchestrator/gui.py            (root-level, delete)

Zero overlap with Plan A. Can be merged independently.

---

## Session Context — Fixed Modules Available for Wiring

The following modules were fixed in the current session and are ready to
be wired into app.py. Do NOT re-fix these — just import and call them:

| Module | Key exports | app.py methods to replace |
|---|---|---|
| activity_log.py | build_activity_log_frame, append_activity | _schedule_message_pump, _pump_messages, _append_activity |
| health.py | check_health, check_health_batch | _run_health_check (partial) |
| benchmark_controls.py | build_benchmark_frame, configure_benchmark_buttons | _update_benchmark_controls, _begin_benchmark_job, _finish_benchmark_job |
| actions.py | copy_cli_command, rename_instance, diff_instances | _copy_cli_command, _rename_instance, _diff_instances |
| row_renderer.py | build_table_rows, filter_visible_rows, render_full_rows | _render_full_rows, _visible_rows |
| toolbar.py | build_toolbar, ToolbarCallbacks, ToolbarWidgets | _build_widgets (toolbar section), _render_daemon_status |
| gpu_inventory.py | render_gpu_inventory, edit_gpu_alias, toggle_gpu_inventory | _render_gpu_inventory, _edit_gpu_alias, _toggle_gpu_inventory |

IMPORTANT: table.py and metadata_cache.py fixes are in Plan A. If Plan A
is not yet merged, do NOT wire table.py/_toggle_sort or metadata_cache.py
into app.py. Wire those last, after Plan A is complete.

---

## Task 1 — Pre-flight: Scan Legacy Import Paths

Before deleting any file, verify no remaining import references exist.
Run these searches on the current codebase:

```powershell
# Check for any import of the root-level gui.py
Select-String -Path "src\**\*.py" -Pattern "from llama_orchestrator import gui" -Recurse
Select-String -Path "src\**\*.py" -Pattern "^import llama_orchestrator.gui$" -Recurse

# Check for exec-compile hack (must be 0 results before deletion)
Select-String -Path "src\**\*.py" -Pattern "exec\(compile" -Recurse
```

Expected: 0 results for all three. If any results appear, add them to the
exclusion list and do NOT delete those files until the imports are updated.

---

## Task 2 — Wire `activity_log` into app.py

### 2.1 Current app.py methods to replace

```
_schedule_message_pump()   line ~1037 — sets up periodic message pump
_pump_messages()           line ~1041 — reads queue and calls _append_activity
_append_activity()         line ~1052 — writes to the ScrolledText widget
_post_message()            line ~1059 — posts a string to the queue
```

### 2.2 Integration steps

Step 1 — In `LlamaOrchestratorGui.__init__`, replace the inline
activity log creation with:
```python
from llama_orchestrator.gui.activity_log import (
    build_activity_log_frame,
    append_activity,
)
# In _build_widgets:
self.activity_log_frame, self.activity, self.activity_scroll = \
    build_activity_log_frame(self)
```

Step 2 — Replace `_append_activity` body with:
```python
def _append_activity(self, message: str) -> None:
    append_activity(self.activity, message)
```

Step 3 — Verify GUI still logs messages. Then delete the inline
`build_activity_log_frame` block from `_build_widgets` and the
body of the old `_append_activity`.

Estimated lines freed: ~30

---

## Task 3 — Wire `gpu_inventory` into app.py

### 3.1 Current app.py methods to replace

```
_render_gpu_inventory()    line ~972  — renders GPU rows into the panel
_edit_gpu_alias()          line ~1005 — opens alias edit dialog
_toggle_gpu_inventory()    line ~966  — shows/hides the panel
```

### 3.2 Integration steps

Step 1 — Add imports:
```python
from llama_orchestrator.gui.gpu_inventory import (
    build_gpu_inventory_frame,
    render_gpu_inventory,
    edit_gpu_alias,
    toggle_gpu_inventory,
)
```

Step 2 — In `_build_widgets`, replace inline GPU frame creation:
```python
self.gpu_inventory_frame, self.gpu_inventory_rows = \
    build_gpu_inventory_frame(self)
```

Step 3 — Replace method bodies:
```python
def _render_gpu_inventory(self, gpus) -> None:
    render_gpu_inventory(
        self.gpu_inventory_rows,
        gpus,
        self.gpu_aliases,
        gpu_alias_button_width=GPU_ALIAS_BUTTON_WIDTH,
    )

def _edit_gpu_alias(self, adapter_name: str | None) -> None:
    if adapter_name is None:
        return
    edit_gpu_alias(
        parent=self,
        adapter_name=adapter_name,
        current_alias=self.gpu_aliases.get(adapter_name, ""),
        aliases_store=self.gpu_aliases,
        on_alias_changed=lambda name, alias: self._on_gpu_alias_changed(name, alias),
        normalize_fn=normalize_gpu_alias,
        save_fn=save_gpu_aliases,
    )

def _toggle_gpu_inventory(self) -> None:
    toggle_gpu_inventory(self.show_gpu_inventory_var, self.gpu_inventory_frame)
```

Step 4 — Verify. Then delete the inline implementations.

Estimated lines freed: ~85

---

## Task 4 — Wire `row_renderer` into app.py

### 4.1 Current app.py methods to replace

```
_render_full_rows()     line ~1284  — delete+insert all rows
_visible_rows()         line ~1275  — filter by tag/search
```

### 4.2 Integration steps

Step 1 — Add imports:
```python
from llama_orchestrator.gui.row_renderer import (
    render_full_rows,
    filter_visible_rows,
    TableRow,
)
```

Step 2 — Replace `_render_full_rows` body:
```python
def _render_full_rows(self, rows) -> None:
    render_full_rows(self.tree, rows, self._visible_column_names())
```

Step 3 — Replace `_visible_rows` body:
```python
def _visible_rows(self, rows) -> tuple[TableRow, ...]:
    return tuple(filter_visible_rows(list(rows)))
```

NOTE: `_build_table_rows` (line ~1122, ~150 lines) is the largest
remaining method. Do NOT replace it in this plan — it is a candidate
for a separate Phase 3 extraction after Plan A + Plan B are both merged.

Estimated lines freed: ~30 (excluding _build_table_rows)

---

## Task 5 — Wire `benchmark_controls` into app.py

### 5.1 Current app.py methods to replace

```
_update_benchmark_controls()   line ~1457  — enable/disable buttons
_begin_benchmark_job()         line ~1444  — set running state
_finish_benchmark_job()        line ~1452  — clear running state
_benchmark_job_running()       line ~1440  — bool check
```

### 5.2 Integration steps

Step 1 — Add imports:
```python
from llama_orchestrator.gui.benchmark_controls import (
    build_benchmark_frame,
    configure_benchmark_buttons,
    update_benchmark_controls,
)
```

Step 2 — In `_build_widgets`, replace inline benchmark frame creation:
```python
self.benchmark_frame, _ = build_benchmark_frame(self)
self._benchmark_btns = configure_benchmark_buttons(
    self.benchmark_frame,
    on_run_background=self._run_background,
    on_run_selected=self._run_selected,
    on_run_batch=self._run_batch,
)
```

Step 3 — Replace `_update_benchmark_controls`:
```python
def _update_benchmark_controls(self) -> None:
    update_benchmark_controls(
        self.benchmark_frame,
        benchmark_running=self._benchmark_job_running(),
        has_queue=bool(self._queued_benchmark_names),
    )
```

Estimated lines freed: ~80

---

## Task 6 — Wire `actions` into app.py

### 6.1 Current app.py methods to replace

```
Any inline clipboard.copy pattern using tk.Tk()
Any inline rename dialog using tk.Tk() + mainloop()
Any inline diff dialog
```

### 6.2 Integration steps

Step 1 — Add imports:
```python
from llama_orchestrator.gui.actions import (
    copy_cli_command,
    rename_instance,
    diff_instances,
)
```

Step 2 — Replace each inline implementation with the extracted function,
passing `self` as the `root`/`parent` parameter:

```python
def _copy_cli_command(self, name: str) -> None:
    copy_cli_command(name, self._get_cli_command, root=self)

def _rename_instance(self, name: str) -> None:
    if rename_instance(name, self._get_display_name(name),
                       confirm_fn=self._confirm_rename, parent=self):
        self.refresh()

def _diff_instances(self, name1: str, name2: str) -> None:
    diff_instances(name1, name2, diff_fn=self._compute_diff, parent=self)
```

Step 3 — Verify dialogs open correctly as Toplevel windows.
Then delete inline implementations.

Estimated lines freed: ~120

---

## Task 7 — Wire `toolbar` into app.py

This is the largest single wiring task. The goal is to replace the
toolbar-building section of `_build_widgets` (starting at line ~760)
with a call to `build_toolbar()`.

### 7.1 Integration steps

Step 1 — Import:
```python
from llama_orchestrator.gui.toolbar import build_toolbar, ToolbarCallbacks
```

Step 2 — In `_build_widgets`, immediately after `self.columnconfigure(...)`:
```python
callbacks = ToolbarCallbacks(
    on_refresh=self.refresh,
    on_add_model=self._add_model,
    on_apply_args=self._apply_runtime_args,
    on_install_llama_server=self._install_llama_server,
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
)
self._toolbar_widgets = build_toolbar(
    self,
    callbacks=callbacks,
    column_headings=COLUMN_HEADINGS,
    all_columns=ALL_COLUMNS,
    visible_columns=tuple(self.gui_settings.visible_columns),
    tag_filter_var=self.tag_filter_var,
    show_gpu_inventory_var=self.show_gpu_inventory_var,
    prompt_var=self.prompt_var,
)
self._toolbar_widgets.toolbar.grid(row=0, column=0, sticky="ew")
```

Step 3 — Update `_render_daemon_status()` to reference the stored
widget handles:
```python
def _render_daemon_status(self) -> None:
    running = self._is_daemon_running()
    self._toolbar_widgets.start_daemon_btn.config(
        state="disabled" if running else "normal"
    )
    self._toolbar_widgets.stop_daemon_btn.config(
        state="normal" if running else "disabled"
    )
```

Step 4 — Verify toolbar renders and all buttons work. Then delete the
inline toolbar-building block from `_build_widgets`.

Estimated lines freed: ~200

---

## Task 8 — __init__.py Cleanup

File: src/llama_orchestrator/gui/__init__.py

Remove the duplicate import alias:
```python
# DELETE this block (lines 72-74 in current file):
from llama_orchestrator.gui.dialogs import (
    ExistingModelFileDialog as _ExistingModelFileDialog,
)
```

`ExistingModelFileDialog` is already exported from `app.py` on line 25
of the same `__init__.py`. The aliased import creates a second name
(prefixed `_`) that is never in `__all__` and causes confusion.

Verify `__all__` is consistent after removal.

---

## Task 9 — Delete Legacy Files

Prerequisite: Task 1 pre-flight scan must show 0 results.

Step 1 — Delete `app.py.bak`:
```powershell
Remove-Item src\llama_orchestrator\gui\app.py.bak
```

Step 2 — Delete root-level `gui.py` (152 KB legacy file):
```powershell
Remove-Item src\llama_orchestrator\gui.py
```

Verify project still imports correctly:
```powershell
.\.venv\Scripts\python.exe -c "from llama_orchestrator.gui import LlamaOrchestratorGui; print('OK')"
```

---

## Task 10 — Verification

### 10.1 Import check

```powershell
.\.venv\Scripts\python.exe -c "
from llama_orchestrator.gui import (
    LlamaOrchestratorGui,
    GuiRefreshSnapshot,
    TableRow,
    RefreshController,
    configure_status_tags,
)
print('All imports OK')
"
```

### 10.2 app.py line count

```powershell
(Get-Content src\llama_orchestrator\gui\app.py).Count
```

Target: <= 1 600 lines (after all 7 wiring tasks complete).
Starting point: 2 536 lines. Expected reduction: ~550 lines from Tasks 2-7.

NOTE: The full 1 200-line target from REFACTORING_SPEC.md requires
further extraction of `_build_table_rows` (~150 lines) and
`_collect_refresh_snapshot` (~80 lines) — these are deferred to Phase 3.

### 10.3 Run existing tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v --tb=short -x 2>&1 |
    Tee-Object docs\test_results_planB.txt
```

All existing tests must pass. Focus areas:
- tests/test_gui.py — the main GUI test suite (36 KB, comprehensive)
- tests/test_health.py — after health.py dependency change to httpx
- tests/test_benchmark.py — after BenchmarkSettings deduplication

### 10.4 Manual smoke test (5 critical items for Plan B scope)

1. GUI opens without error or traceback
2. Toolbar buttons all clickable and call correct actions
3. GPU inventory panel toggles show/hide
4. Rename dialog opens as modal Toplevel, does not block event loop
5. CLI copy command works (no second root window created)

---

## Execution Order (must be sequential within Plan B)

```
Task 1 (pre-flight scan) -> must be 0 results before any deletion
Task 2 (activity_log)    -> safe to do first, low risk
Task 3 (gpu_inventory)   -> safe to do second
Task 4 (row_renderer)    -> safe third
Task 5 (benchmark_controls)
Task 6 (actions)
Task 7 (toolbar)         -> largest; do last among wiring tasks
Task 8 (__init__.py)     -> after all wiring is verified
Task 9 (delete files)    -> only after Task 8 verified
Task 10 (verification)   -> final gate before PR
```

---

## Dependency on Plan A

Tasks 2-7 are fully independent of Plan A.

If Plan A has been merged before Plan B starts Task 7 (toolbar wiring),
the following additional wirings become available for the same session:

- Wire `table._toggle_sort` into `app.py._toggle_sort`
- Wire `metadata_cache.MetadataCache` into the RefreshController snapshot
  collection via `_collect_refresh_snapshot` (WS-3 from spec)

If Plan A has NOT been merged yet, skip those two and add a TODO comment
in app.py for the follow-up.

---

## Files Touched (Plan B only)

| File | Change |
|---|---|
| src/llama_orchestrator/gui/app.py | Wire 7 modules, delete ~550 lines |
| src/llama_orchestrator/gui/__init__.py | Remove duplicate ExistingModelFileDialog alias |
| src/llama_orchestrator/gui/app.py.bak | DELETE |
| src/llama_orchestrator/gui.py (root) | DELETE |

## Files NOT Touched by Plan B (reserved for Plan A)

table.py, gpu_inventory.py, metadata_cache.py, tests/test_gui_modules.py
