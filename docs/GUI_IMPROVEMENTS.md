# llama-orchestrator — GUI & Functionality Improvement Recommendations

> Prepared: 2026-06-17
> Scope: Desktop GUI (`llama-orch gui`), supporting modules, and overall user experience
> Based on: full codebase review of `src/llama_orchestrator/gui/app.py` (3 846 lines),
> `gui/refresh.py`, `gui/usability.py`, `gui/dialogs.py`, `gui_state.py`, `benchmark.py`,
> `benchmark_grid.py`, `hf_import.py`, `engine/`, and the 30-file test suite.

---

## Table of Contents

1. [Layout & UX](#1-layout--ux)
2. [Visual Design & Theming](#2-visual-design--theming)
3. [Table & Data Presentation](#3-table--data-presentation)
4. [Functionality & Features](#4-functionality--features)
5. [Benchmark Workflow](#5-benchmark-workflow)
6. [Configuration & Dialogs](#6-configuration--dialogs)
7. [Performance & Responsiveness](#7-performance--responsiveness)
8. [Code Architecture & Maintainability](#8-code-architecture--maintainability)
9. [Testing & Quality](#9-testing--quality)
10. [Documentation & Discoverability](#10-documentation--discoverability)

---

## 1. Layout & UX

### 1.1 Toolbar Overflow & Information Density
**Problem:** The toolbar packs 19 grid columns into a single row (buttons, menus, labels, daemon status). On screens narrower than ~1300 px, widgets clip or overlap. The detail bar below the table adds another 14 buttons, creating a dense, flat action surface where important actions blend with infrequent ones.

**Recommendations:**
- **Group toolbar actions into logical sections** separated by visual dividers (`ttk.Separator(orient=VERTICAL)`): Instance actions | Benchmark controls | Columns & Filters | Daemon.
- **Collapse rarely-used toolbar items** (e.g. "Apply args", "Edit Benchmark Prompt", "GPU map") into a `≡ More` overflow menu, keeping the primary toolbar to ≤ 10 items.
- **Replace the flat detail bar** with a context-aware panel that shows actions relevant to the current selection count:
  - 0 selected → hide or show only global actions.
  - 1 selected → full detail bar with instance-specific actions.
  - 2 selected → show "Diff selected" prominently; hide single-instance actions.
  - 3+ selected → show batch actions and queue controls.

### 1.2 Status Bar & Persistent Footer
**Problem:** The footer frame only hosts the indeterminate progress bar. There is no persistent status line showing the count of running/stopped instances, active filters, or last refresh timestamp.

**Recommendations:**
- Add a persistent status bar at the bottom:
  ```
  [Running: 3 | Stopped: 2 | Filtered: router]  Last refresh: 19:32:15  [progress-bar]
  ```
- Show the active sort specification as a compact label in the footer so users see the current sort state without scanning column headers.

### 1.3 Window Geometry & Layout Persistence
**Problem:** Tag filter, window geometry, and GPU inventory panel visibility reset on every GUI restart. Column visibility and sort order already persist.

**Recommendations:**
- Persist window geometry (position + size) in `state/gui_settings.json`. Restore on launch, clamped to the current monitor bounds.
- Persist the tag filter selection and GPU inventory panel toggle state.
- Persist the `PanedWindow` sash position (table vs. activity log ratio).

### 1.4 Keyboard Navigation & Accessibility
**Problem:** Keyboard shortcuts exist but are not discoverable in the UI. Users cannot navigate the table or trigger actions without the mouse beyond the six registered Ctrl shortcuts.

**Recommendations:**
- Add a `Help → Keyboard shortcuts` dialog listing all bindings (already registered in `SHORTCUT_REGISTRY`).
- Add mnemonics/accelerator labels to toolbar buttons and context menu items.
- Support `Up`/`Down` arrow keys + `Space` to toggle queue checkboxes, and `Enter` to open config.
- Add `Ctrl+A` to select all visible rows (currently listed in `SHORTCUT_REGISTRY` but not bound).
- Add `Delete` binding to stop + prompt for removal of the selected instance.

---

## 2. Visual Design & Theming

### 2.1 Tkinter Theme & Color Scheme
**Problem:** The GUI uses the default Tkinter/ttk theme which looks dated and inconsistent across Windows 10 and 11.

**Recommendations:**
- Apply `ttk.Style().theme_use('vista')` or `'winnative'` on Windows to pick up native controls and rounded corners on Win 11.
- Alternatively, consider using `sv_ttk` (Sun Valley theme) or `ttkbootstrap` for a modern, dark-mode-capable appearance while still staying standard-library-compatible for Tkinter.
- Define a `LlamaOrchestratorStyle` class that sets consistent padding, fonts, and colors across all dialogs and frames.

### 2.2 Status & Health Color Coding in the Table
**Problem:** `usability.py` defines `STATUS_INDICATORS` and `HEALTH_INDICATORS` with colors but the Treeview `tag_configure()` only sets `foreground`. The entire row text changes color, but cells like "Name" and "Model" don't need status coloring — it creates visual noise.

**Recommendations:**
- Use row-level tag colors more selectively: apply status coloring only to the Status and Health columns. Consider using icon/glyph prefixes (✓, ✗, ⏳) instead of full-row coloring.
- Add a subtle row background color for running vs. stopped instances (e.g. very light green/grey) so status is visible even with columns scrolled.
- The `RUNNING_BENCHMARK_ROW_TAG` yellow background (`#fff4cc`) is good — extend this pattern for other transient states.

### 2.3 GPU Inventory Panel
**Problem:** The GPU inventory panel uses a flat grid layout with `ttk.Label` widgets. When there are 3+ GPUs it takes significant vertical space.

**Recommendations:**
- Collapse the panel into a compact horizontal summary by default: `Vulkan0: RX 7900 XTX [rx7900] | Vulkan1: RTX 4090 | Vulkan2: RX 580`.
- Provide an expand/collapse toggle to show the full table with alias edit buttons.

---

## 3. Table & Data Presentation

### 3.1 Column Resizing & Auto-Fit
**Problem:** Column widths are fixed constants in `COLUMN_WIDTHS`. Users cannot resize columns, and long values (e.g. model paths, runtime args) get truncated.

**Recommendations:**
- Enable column resizing by not setting `stretch=False` on `ttk.Treeview` columns.
- Add a "Fit columns to content" action in the Columns menu.
- Persist custom column widths in `gui_settings.json`.

### 3.2 Search / Filter Bar
**Problem:** The only filtering mechanism is tag-based. There is no free-text search to find an instance by name, model path, or any other field.

**Recommendations:**
- Add a search entry field (e.g. in the toolbar) that filters visible rows by substring match across Name, Model, Tags, and Runtime args.
- Support prefix operators: `status:running`, `port:8001`, `tag:router`.
- Combine with the tag filter as an AND condition.

### 3.3 Row Tooltips
**Problem:** Truncated cells (model path, runtime args, memory details) are hard to read. Double-click only opens config; there's no quick preview.

**Recommendations:**
- Add hover tooltips for cells with truncated content showing the full value.
- For the Memory MB column, the tooltip could show the breakdown: `VRAM: 8192 MB, Shared RAM: 0 MB, Source: windows_process_counter`.

### 3.4 Multi-Select Operations Feedback
**Problem:** When multiple rows are selected, single-instance actions like "Open config" or "Health" silently use only the first selected row. This can confuse users.

**Recommendations:**
- Disable single-instance buttons when multiple rows are selected, or
- Show a count badge: `Open config (1 of 3 selected)`.
- For batch operations, add a confirmation dialog: `Start 5 instances?`

### 3.5 Column Reordering
**Problem:** Column display order is fixed; users can only toggle visibility.

**Recommendation:**
- Allow drag-and-drop column reordering and persist the display order in `gui_settings.json`.

---

## 4. Functionality & Features

### 4.1 Instance Deletion / Removal
**Problem:** There is no way to remove an instance through the GUI. Users must manually delete the `instances/<name>/` directory.

**Recommendations:**
- Add a "Remove instance" option in the context menu and detail bar.
- Require confirmation: `Remove instance 'gpt-oss'? This will delete instances/gpt-oss/config.json.`
- Offer to also remove logs (`logs/<name>/`) and benchmark history for that instance.
- Bind to `Delete` key (already listed in `SHORTCUT_REGISTRY`).

### 4.2 Instance Log Viewer
**Problem:** "Open logs" opens the folder in Explorer. There is no in-GUI log viewer.

**Recommendations:**
- Add an embedded log viewer tab/pane that tails `stdout.log` and `stderr.log` for the selected instance.
- Support log level filtering and search within the viewer.
- This avoids the context switch of leaving the GUI to view logs.

### 4.3 Config Editor
**Problem:** "Open config" opens the JSON file in the default editor. Editing config requires JSON knowledge.

**Recommendations:**
- Provide a structured config editor dialog similar to the Add Model dialog, pre-filled with the current instance config.
- Separate static vs. dynamic parameters (already defined in `parameter_mutability`) and warn about restart requirements.
- Highlight changed fields with a visual diff indicator before saving.

### 4.4 Batch Configuration Changes
**Problem:** Actions like "Apply args" work on a single instance. There's no way to apply settings to multiple instances at once.

**Recommendations:**
- Add batch operations in the Batch menu: `Set GPU layers`, `Set backend`, `Apply default args`, `Set tags`.
- These should iterate over all visible/selected instances.

### 4.5 Instance Import / Export
**Problem:** "Export config to VS Code" exports a single instance. There's no way to import or bulk-export.

**Recommendations:**
- Add "Export all configs" to export the entire instance catalog as a single JSON or ZIP archive.
- Add "Import config" to create an instance from a JSON file.
- This enables migration between machines and backup/restore workflows.

### 4.6 Notifications for Long-Running Operations
**Problem:** Long operations (binary install, benchmark, model download) only update the Activity log. If the GUI is minimized, the user has no notification.

**Recommendations:**
- Use the Windows system tray balloon notification (`win10toast` or `plyer`) when a benchmark or download completes.
- Flash the taskbar icon when a background operation finishes.

---

## 5. Benchmark Workflow

### 5.1 Benchmark History Viewer
**Problem:** Benchmark results are stored in SQLite (`benchmark_history.sqlite`) and as Markdown artifacts, but there is no in-GUI history viewer. Users must open individual files or query the database.

**Recommendations:**
- Add a "Benchmark History" pane or dialog showing a table of past results:
  `Timestamp | Instance | TPS | Latency | VRAM | Prompt | Status`.
- Support sorting and filtering by instance, date range, and status.
- Allow selecting two results for a side-by-side comparison.
- Show trend charts (TPS over time) using a simple canvas or embedded matplotlib.

### 5.2 Benchmark Comparison
**Problem:** Users cannot easily compare benchmark results across instances or across different configurations.

**Recommendations:**
- Add a "Compare benchmarks" action when 2+ instances are selected.
- Display a comparison table: `Instance | TPS | Latency | Memory | Config Hash`.
- Highlight the best performer in each metric.

### 5.3 Quick Benchmark Result Preview
**Problem:** After a benchmark completes, the result only appears in the Activity log. The user must scroll and parse text.

**Recommendations:**
- Show a toast-style popup or a transient banner at the top of the table: `gpt-oss: 32.5 TPS, TTFT 145 ms, 4096 MB VRAM`.
- Auto-dismiss after 10 seconds or on click.

### 5.4 Benchmark Progress
**Problem:** The progress bar is indeterminate during benchmarks. There's no progress indication for serial or grid benchmarks.

**Recommendations:**
- For serial benchmarks, update the progress bar to show `2/5 complete` (determinate mode).
- For grid benchmarks, show `Combination 4/12, Instance 1/3`.
- Display an estimated time remaining based on average per-run duration.

### 5.5 Benchmark Prompt Management
**Problem:** The prompt selection workflow requires the user to use a file dialog every time. There's no way to manage multiple prompts.

**Recommendations:**
- Add a `Prompts` submenu listing all `.txt` and `.md` files in `benchmarks/prompts/`.
- Allow creating a new prompt from the GUI.
- Show prompt preview (first 100 characters) in the detail bar next to the prompt filename.

---

## 6. Configuration & Dialogs

### 6.1 Add Model Dialog — Validation & Preview
**Problem:** The Add Model dialog validates only on save. Invalid port numbers, non-existent model files, and duplicate names produce error dialogs after the user has filled everything in.

**Recommendations:**
- Add inline validation with red borders / error labels:
  - "Name" → warn if name already exists as you type.
  - "GGUF model" → check file existence and show file size when valid.
  - "Port" → check availability in real-time (debounced).
- Show a live preview of the generated config JSON at the bottom of the dialog.

### 6.2 Add Model Dialog — GPU Layers Auto-Suggest
**Problem:** The "GPU layers" field defaults to `0` (CPU inference). Users must know their model's layer count to set this correctly.

**Recommendations:**
- When a GGUF file is selected, parse its metadata (layer count is in `model_metadata`) and auto-suggest `layers = total_layers` for full GPU offload.
- Show the estimated VRAM usage (already implemented in `memory_fit.py`) next to the layers field.

### 6.3 Grid Benchmark Dialog — Usability
**Problem:** The grid dialog shows all parameters in a single long list. Disabled/read-only parameters clutter the view. The "Configure..." button for KV cache profiles is the only composite control.

**Recommendations:**
- Group parameters into collapsible sections: `Request parameters` | `Runtime parameters` | `Model parameters`.
- Hide disabled/read-only parameters by default, with a "Show all" toggle.
- Add a "Quick presets" dropdown: `Speed sweep (context 1024→8192)`, `Quality sweep (temp 0→1)`.

### 6.4 Install Binary Dialog — Version Discovery
**Problem:** The version field defaults to `"latest"`. Users don't know which versions are available.

**Recommendations:**
- Fetch available versions from the GitHub releases API and populate a dropdown.
- Show the installed binaries list alongside the install dialog.
- Indicate which versions are already installed.

---

## 7. Performance & Responsiveness

### 7.1 Refresh Cycle Overhead
**Problem:** `refresh()` runs synchronously on the main thread every 5 seconds. It calls `list_instances()`, `load_all_instances()`, `collect_detected_gpu_inventory()`, and `latest_benchmark_results()` — all of which involve filesystem and SQLite access. With many instances, this can cause perceptible UI freezes.

**Recommendations:**
- Move data collection to the `RefreshController` background thread (already scaffolded in `refresh.py` but not fully wired). The main thread should only receive and render immutable snapshots.
- Implement progressive/lazy loading: only refresh GPU inventory every 60s (already debounced), but refresh instance state every 2s.
- Cache `load_all_instances()` and invalidate on filesystem change (`watchdog` or `ReadDirectoryChangesW` on Windows).
- The `_GUI_TIMING_ENABLED` instrumentation is already there — document how to use it and add timing thresholds that log warnings.

### 7.2 Tree Rendering Performance
**Problem:** `render_diff_rows` iterates all rows and calls `tree.item()` or `tree.move()` for each. With 50+ instances, this causes visible flickering on each refresh.

**Recommendations:**
- Batch Treeview updates: wrap the entire update in `self.tree.configure(selectmode="none")` / restore to suppress selection change events during the update.
- Skip the reorder step when the order hasn't changed (the `desired_order != current_children` check already does this, but a hash comparison would be faster for large lists).
- Consider using `Treeview.detach()` / `Treeview.reattach()` instead of `move()` for reordering, as detach+reattach is generally faster in Tk.

### 7.3 Benchmark Settings File I/O
**Problem:** `_reload_benchmark_settings()` reads from disk every time a benchmark is triggered. `save_benchmark_settings()` writes immediately for each parameter change.

**Recommendations:**
- Cache settings in memory and write on a debounce (e.g. 500 ms after the last change).
- For reads, compare file mtime before re-reading.

---

## 8. Code Architecture & Maintainability

### 8.1 GUI Module Size
**Problem:** `app.py` is 3 846 lines long with the `LlamaOrchestratorGui` class containing all UI logic. This makes navigation, testing, and code review difficult.

**Recommendations:**
- Continue the modular extraction started with `gui/refresh.py`, `gui/usability.py`, and `gui/dialogs.py`:
  - Extract benchmark-related methods to `gui/benchmark_actions.py`.
  - Extract HuggingFace import dialogs to `gui/hf_import_dialog.py` (currently partially duplicated between `gui/dialogs.py` and `app.py`).
  - Extract instance management actions (start/stop/restart/health) to `gui/instance_actions.py`.
  - Extract the Add Model / Port Settings / Install Binary dialogs from `app.py` into `gui/dialogs.py` (they are currently defined in both places — the `app.py` versions are the authoritative ones).

### 8.2 Dialog Duplication
**Problem:** `gui/dialogs.py` contains skeleton versions of `AddModelDialog`, `GridBenchmarkDialog`, `KvCacheProfileDialog`, `HuggingFaceImportDialog`, and `ExistingModelFileDialog`. The real, functional implementations live in `app.py`. This creates confusion about which is authoritative.

**Recommendations:**
- Remove the duplicate skeleton implementations from `gui/dialogs.py` or replace them with the real implementations from `app.py`.
- Use `gui/dialogs.py` as the single source of truth for all dialogs.

### 8.3 Type Safety & Mypy
**Problem:** The mixin pattern in `RenderDiffMixin` uses `# type: ignore[attr-defined]` annotations to access `self.tree`, `self._row_map`, etc. This bypasses type checking.

**Recommendations:**
- Define a `Protocol` or abstract base that declares the interface expected by `RenderDiffMixin`.
- Use `typing.cast()` or restructure as composition (pass the Treeview and state as constructor arguments) instead of a mixin.

### 8.4 Error Handling in Background Threads
**Problem:** `_run_background()` catches all exceptions and posts them as activity messages. Some errors (e.g. file-not-found for config) should trigger error dialogs.

**Recommendations:**
- Distinguish between recoverable errors (post to activity log) and critical errors (show messagebox via `after()` on the main thread).
- Add error categories and handle them differently in the message pump.

---

## 9. Testing & Quality

### 9.1 GUI Test Coverage
**Problem:** `test_gui.py` (36 KB) covers helpers and some rendering but does not test dialog behavior, context menu actions, or the Add Model workflow end-to-end.

**Recommendations:**
- Add dialog-level tests using headless Tkinter (`Tk()` is already used in tests):
  - Test `AddModelDialog` save validation.
  - Test `GridBenchmarkDialog` plan building.
  - Test `HuggingFaceImportDialog` event pump with mock variants.
- Add integration tests for the `_on_tree_click` → `_toggle_queue_name` → `_update_benchmark_controls` chain.
- Add snapshot tests for `_build_table_rows` to catch regressions in column order or sort value mapping.

### 9.2 Linting & Formatting
**Problem:** The README notes that "repository-wide Ruff may still report older pre-existing style issues" and Ruff is only scoped to touched files.

**Recommendations:**
- Run a one-time Ruff fix across the entire `src/` and `tests/` tree to clean up legacy issues.
- Add a `pre-commit` hook or CI check to enforce formatting going forward.

---

## 10. Documentation & Discoverability

### 10.1 GUI User Guide
**Problem:** The README documents features extensively but there is no standalone GUI user guide or tutorial.

**Recommendations:**
- Create `docs/GUI_USER_GUIDE.md` with:
  - Annotated screenshots of the main window layout.
  - Workflow walkthroughs: adding a model, running a benchmark, interpreting results.
  - Keyboard shortcuts reference table.
  - Troubleshooting: "No GPU mapping detected", "Vulkan binary missing", common error messages.

### 10.2 Tooltips & In-App Help
**Problem:** Buttons and menu items have no tooltips. New users must read the README to understand what "Apply args" or "Grid benchmark" does.

**Recommendations:**
- Add tooltips to all toolbar buttons and detail bar buttons using a simple `ttk`-compatible tooltip class.
- Add `?` icons next to complex controls (e.g. the "Params" menu, GPU layers field in Add Model).

### 10.3 Activity Log Improvements
**Problem:** The activity log is append-only with no search or filtering. Over a long session it becomes very long and hard to navigate.

**Recommendations:**
- Add a search/filter bar above the activity log.
- Add log level indicators: `[INFO]`, `[WARN]`, `[ERROR]` with color coding.
- Add a "Clear log" button.
- Allow copying individual log lines to clipboard via right-click.

---

## Priority Matrix

| Priority | Category | Item | Effort |
|----------|----------|------|--------|
| 🔴 High | Architecture | 8.1 — Extract large `app.py` into modules | Medium |
| 🔴 High | Architecture | 8.2 — Remove duplicate dialog skeletons | Low |
| 🔴 High | UX | 1.1 — Toolbar overflow & detail bar grouping | Medium |
| 🔴 High | Functionality | 4.1 — Instance deletion | Low |
| 🟡 Medium | Performance | 7.1 — Move refresh data collection off main thread | Medium |
| 🟡 Medium | UX | 1.3 — Persist window geometry & layout state | Low |
| 🟡 Medium | Table | 3.1 — Column resizing & auto-fit | Low |
| 🟡 Medium | Table | 3.2 — Search / filter bar | Medium |
| 🟡 Medium | Benchmark | 5.1 — Benchmark history viewer | High |
| 🟡 Medium | Benchmark | 5.4 — Determinate progress for serial/grid | Low |
| 🟡 Medium | Dialogs | 6.1 — Add Model inline validation | Medium |
| 🟡 Medium | Dialogs | 6.2 — GPU layers auto-suggest | Low |
| 🟡 Medium | Visual | 2.1 — Apply Windows native theme | Low |
| 🟡 Medium | Documentation | 10.2 — Add tooltips to all buttons | Low |
| 🟢 Low | Functionality | 4.2 — Instance log viewer | High |
| 🟢 Low | Functionality | 4.3 — Config editor dialog | High |
| 🟢 Low | Functionality | 4.5 — Instance import/export | Medium |
| 🟢 Low | Functionality | 4.6 — System tray notifications | Low |
| 🟢 Low | Table | 3.3 — Row tooltips | Low |
| 🟢 Low | Table | 3.5 — Column reordering | Medium |
| 🟢 Low | UX | 1.2 — Status bar | Low |
| 🟢 Low | UX | 1.4 — Keyboard shortcuts dialog | Low |
| 🟢 Low | Benchmark | 5.2 — Benchmark comparison | Medium |
| 🟢 Low | Benchmark | 5.5 — Prompt management | Low |
| 🟢 Low | Visual | 2.2 — Selective status column coloring | Low |
| 🟢 Low | Visual | 2.3 — Collapsible GPU panel | Low |
| 🟢 Low | Testing | 9.1 — Dialog-level test coverage | Medium |
| 🟢 Low | Testing | 9.2 — Repository-wide Ruff cleanup | Low |
| 🟢 Low | Documentation | 10.1 — GUI user guide | Medium |
| 🟢 Low | Documentation | 10.3 — Activity log improvements | Low |

---

## Implementation Notes

- All recommendations preserve the current **stdlib-only constraint** (no external GUI dependencies unless explicitly chosen) and **Windows-native focus**.
- Visual theming (2.1) can be achieved with zero dependencies by selecting the right `ttk` theme and overriding a few styles.
- The `RefreshController` in `gui/refresh.py` is already scaffolded for background refresh (7.1); the main work is wiring it into `LlamaOrchestratorGui.__init__` and removing the synchronous `_collect_refresh_snapshot()` call from `refresh()`.
- The dialog cleanup (8.2) is a prerequisite for adding new dialogs — without it, developers waste time determining which implementation is authoritative.
