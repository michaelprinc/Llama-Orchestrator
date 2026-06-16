# llama-orchestrator — Refactoring Spec

> Spec-driven development artifact · v1.0 · 2026-06-16

---

## 1.  Purpose

Improve **GUI performance** (refresh latency, responsiveness, perceived lag)
and **GUI usability** (discoverability, ergonomics, visual clarity) for
`llama-orchestrator` by restructuring the codebase along spec-driven
principles.

---

## 2.  Current State Summary

| Module | File | Lines | Concern |
|--------|------|------:|---------|
| `gui.py` | src/…/gui.py | 3 814 | Full desktop UI |
| `gui_state.py` | src/…/gui_state.py | 227 | Persisted table prefs |
| `cli.py` | src/…/cli.py | 1 534 | Typer CLI routing |
| `engine/process.py` | src/…/engine/process.py | 778 | Process lifecycle |
| `engine/state.py` | src/…/engine/state.py | 814 | SQLite persistence |
| `engine/detection.py` | src/…/engine/detection.py | 345 | GPU / runtime discovery |
| `engine/command.py` | src/…/engine/command.py | 152 | Command builder |
| `benchmark.py` | src/…/benchmark.py | 1 677 | Benchmark harness |
| `config/schema.py` | src/…/config/schema.py | ~500 | Pydantic schemas |
| `health/probes.py` | src/…/health/probes.py | 477 | Pluggable probes |
| `health/checker.py` | src/…/health/checker.py | 261 | Health check flow |
| `health/monitor.py` | src/…/health/monitor.py | 340 | Background monitor |

**Total measured: ~12 400 lines across core modules.**

The GUI alone is `3 814` lines — the single largest file and the primary
performance bottleneck.  The health checker and benchmark modules are also
large but secondary targets.

---

## 3.  Identified Problems

### 3.1  GUI Performance Bottlenecks

#### P-1 — Synchronous full refresh on every cycle

`_auto_refresh()` calls `refresh()` every `refresh_interval_ms` (default
~2 000 ms).  Each refresh:

1. Loads **all instance states** via `list_instances()` (SQLite query).
2. Loads **all instance configs** via `load_all_instances()` (JSON reads).
3. Calls `collect_detected_gpu_inventory(configs.values())` — spawns
   `vulkaninfo` subprocess, reads every instance's log file, parses GPU
   labels.
4. Builds **every table row** — calls `describe_effective_runtime()` (builds
   full command, parses flags, resolves GPU labels), `resolve_model_size_gb()`
   (stat() every model file).
5. **Destroys and re-inserts every row** in the Tkinter Treeview.
6. Rebuilds GPU inventory panel, tag filter, daemon status, benchmark controls.

**Impact:** Every 2 s the main thread does 3 814 lines of work.  With
N instances, each refresh blocks the event loop for `O(N)` subprocesses,
disk I/O, and Treeview rebuilds.

#### P-2 — No state diffing; entire Treeview is wiped and rebuilt

`_render_full_rows()` calls `self.tree.delete(item)` for every row then
`self.tree.insert()` for every row — even when only the health indicator
changed.

#### P-3 — Health check creates a new httpx.Client per call

`HealthProbe.check()` opens an `httpx.Client(timeout=5.0)` context for each
health check.  The health monitor checks every instance every 10 s.  With
N running instances that is `N` client creations per cycle.

#### P-4 — HealthMonitor uses a single-threaded blocking loop

`_monitor_loop()` checks instances sequentially with 10-s intervals.  If
one health check times out (5 s), the entire cycle is delayed.

#### P-5 — `describe_effective_runtime()` builds the full command line

Every refresh rebuilds the entire `llama-server` command from scratch,
including resolving binary paths, parsing all config sections, and building
GPU labels — work that rarely changes between refreshes.

### 3.2  GUI Usability Issues

#### U-1 — No visual feedback during long operations

When a benchmark runs or a daemon starts, the GUI has no progress indicator.
The activity log updates late and users don't know whether the action
succeeded or failed without checking logs.

#### U-2 — Too many action buttons

The `detail_bar` frame contains 14+ buttons for a single row (Quick
benchmark, Serial benchmark, Grid benchmark, Stop queue, Stop grid, Clone,
Rename, Diff, Copy CLI, Open config, Open config folder, Open logs, Open
project, Open prompt, Reset).  Many users need only 3-4 of these.

#### U-3 — Context menu is deep and hard to discover

The context menu has 10+ actions but no grouping or icons.  Users don't
know that right-click offers "Clone row", "Copy CLI", "Diff selected", etc.

#### U-4 — No keyboard shortcuts for common actions

Start, stop, restart, and benchmark can only be triggered via context menu
or the toolbar buttons — no `Ctrl+S` / `Ctrl+R` / `Ctrl+T` shortcuts.

#### U-5 — GPU inventory panel uses screen space inefficiently

The GPU inventory panel at the top of the table shows one row per GPU with
an editable alias button.  Most users never edit aliases.

#### U-6 — Status/health indicators use text-only symbols

Status is shown as `*`, `~`, `-`, `X` and health as `*`, `~`, `!`, `X`.
These are hard to parse at a glance, especially in the compact Treeview
cells.

---

## 4.  Proposed Refactoring Plan

The work is split into **six workstreams**, each with a spec-driven
lifecycle (DRAFT → CLARIFIED → PLANNED → IN_PROGRESS → VALIDATED).

```
WS-1: GUI Performance — Event-driven refresh + row diffing
WS-2: Health Monitor — Async health checks + client pool
WS-3: Engine — Command-line / runtime metadata caching
WS-4: GUI Usability — Action bar, shortcuts, visual indicators
WS-5: Module Delegation — Extract submodules from gui.py
WS-6: Dependency & Project hygiene
```

---

## 5.  Workstream 1 — GUI Performance (P-1, P-2, P-5)

**Goal:** Reduce per-refresh main-thread work from `O(N)` subprocesses +
full Treeview rebuilds to `O(1)` incremental updates, with background
computation.

### 5.1  Introduce a `RefreshController` (new module)

**File:** `src/llama_orchestrator/gui/refresh.py`

A new class that manages the refresh lifecycle:

```python
@dataclass
class RefreshSnapshot:
    """Immutable snapshot of all instance data for one refresh cycle."""
    rows: tuple[TableRow, ...]
    detected_gpus: tuple[DetectedGpu, ...]
    all_tags: tuple[str, ...]
    collected_at: float
```

The `RefreshController`:

1. Runs on a **background thread** (or uses `concurrent.futures.ThreadPoolExecutor`).
2. Produces a `RefreshSnapshot` every `refresh_interval_ms`.
3. Posts a lightweight event (`"refresh_done"`) when a new snapshot is ready.
4. The main thread only receives the snapshot and **diffs** it against the
   previous one.

### 5.2  Row-level diffing

Replace `_render_full_rows()`:

```python
def _render_diff_rows(self, new_rows: Sequence[TableRow]) -> None:
    new_map = {r.name: r for r in new_rows}
    existing = self._row_map  # {name: TableRow} from last cycle
    for name, row in new_map.items():
        if name in existing:
            # Update in-place — only changed values
            if existing[name].values != row.values:
                self.tree.set(name, row)  # partial update per column
        else:
            self.tree.insert("", tk.END, iid=name, values=row.values)
    for name in existing:
        if name not in new_map:
            self.tree.delete(name)
    self._row_map = new_map
```

**Impact:** When only health indicators change (the common case), only 2
columns are updated per row instead of all 20.  Treeview redraw is
~5–10× faster.

### 5.3  Debounce GPU inventory updates

GPU inventory changes rarely (<1× per minute).  Cache the result for 60 s
and only rebuild the GPU panel when it actually changes.

```python
self._last_gpu_inventory: tuple[DetectedGpu, ...] | None = None
self._gpu_inventory_ts: float = 0

def _refresh_gpu_inventory(self):
    now = time.monotonic()
    if now - self._gpu_inventory_ts < 60:
        return
    self._gpu_inventory_ts = now
    # rebuild only if changed
```

### 5.4  Acceptance criteria

- [ ] Refresh cycle completes in < 200 ms for 10 instances (vs current
      ~2–5 s on typical hardware).
- [ ] Main thread is never blocked for > 50 ms.
- [ ] Treeview row updates are incremental (no full delete + insert cycle
      unless instance list changed).
- [ ] GPU inventory rebuilds happen at most once per minute.

---

## 6.  Workstream 2 — Health Monitor (P-3, P-4)

**Goal:** Reduce health check latency and improve reliability.

### 6.1  Shared httpx.AsyncClient pool

Replace per-call `httpx.Client()` with a singleton shared across all
health checks.

**File:** `src/llama_orchestrator/health/client_pool.py` (new)

```python
class HealthClientPool:
    _client: httpx.AsyncClient | None = None
    @classmethod
    def get(cls) -> httpx.AsyncClient:
        if cls._client is None:
            cls._client = httpx.AsyncClient(timeout=5.0)
        return cls._client
```

### 6.2  Concurrent health checks

Replace the sequential `_check_all_instances()` loop with `asyncio.gather()`:

```python
async def _check_all_instances_async(self):
    running_names = [n for n, _ in discover_instances()]
    tasks = [self._check_one(name) for name in running_names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for name, result in zip(running_names, results):
        if isinstance(result, Exception):
            logger.error(f"Health check failed for {name}: {result}")
        else:
            self._handle_health_result(name, result)
```

### 6.3  Per-instance health check intervals

Some instances are idle (no benchmark running) and can have longer
check intervals.  Implement per-instance interval from config:

```yaml
healthcheck:
  interval: 10          # default
  idle_interval: 30     # when no benchmark queued
```

### 6.4  Acceptance criteria

- [ ] N running instances all health-checked in < 1 s total (vs current
      `N × 5 s` worst case).
- [ ] No `httpx.Client` leaks or unclosed connections.
- [ ] Health checks use `asyncio` and do not block the Tkinter event loop.

---

## 7.  Workstream 3 — Engine Caching (P-5)

**Goal:** Eliminate redundant command-building and model stat operations
during refresh cycles.

### 7.1  Runtime metadata cache

**File:** `src/llama_orchestrator/engine/metadata.py` (new)

```python
@dataclass
class InstanceMetadata:
    effective_runtime: EffectiveRuntimeSelection
    model_size_gb: float | None
    config_hash: str

class MetadataCache:
    """Cache built once per refresh cycle, shared by GUI."""
    _cache: dict[str, InstanceMetadata] = {}
    
    def load_all(self, configs: dict[str, InstanceConfig]) -> None:
        for name, config in configs.items():
            self._cache[name] = InstanceMetadata(
                effective_runtime=describe_effective_runtime(config),
                model_size_gb=resolve_model_size_gb(config),
                config_hash=hashlib.md5(
                    json.dumps(config.model_dump(), sort_keys=True).encode()
                ).hexdigest(),
            )
    
    def get(self, name: str) -> InstanceMetadata | None:
        return self._cache.get(name)
    
    def is_stale(self, configs: dict[str, InstanceConfig]) -> bool:
        """True if any config changed since last load."""
        for name, config in configs.items():
            h = hashlib.md5(
                json.dumps(config.model_dump(), sort_keys=True).encode()
            ).hexdigest()
            existing = self._cache.get(name)
            if existing is None or existing.config_hash != h:
                return True
        return False
```

The GUI creates **one** `MetadataCache`, calls `load_all()` once per
background refresh, and passes it to `_build_table_rows()` so that
`describe_effective_runtime()` is never called during row rendering.

### 7.2  Acceptance criteria

- [ ] `describe_effective_runtime()` and `resolve_model_size_gb()` are
      called at most once per refresh cycle per instance.
- [ ] Metadata cache invalidation triggers on config change only.

---

## 8.  Workstream 4 — GUI Usability (U-1 – U-6)

**Goal:** Improve discoverability, reduce visual clutter, add keyboard
shortcuts and progress indicators.

### 8.1  Action bar redesign (U-2, U-3)

Replace the 14+ buttons in `detail_bar` with a **single collapsible bar**:

| State | Visible actions |
|-------|----------------|
| No selection | Quick benchmark, Clone model, Grid benchmark |
| Single selection | Start, Stop, Restart, Quick benchmark, Grid benchmark, Copy CLI |
| Multi selection | Start selected, Stop selected, Restart selected, Clone |

Use icons (small PNG/SVG labels like ▶, ⏹, ↻, 📊, 📋) instead of text
buttons for common actions.  Show text only on hover/tooltip.

### 8.2  Keyboard shortcuts (U-4)

Register the following Tkinter bindings:

| Shortcut | Action |
|----------|--------|
| `Ctrl+S` | Start selected |
| `Ctrl+Shift+S` | Stop selected |
| `Ctrl+R` | Restart selected |
| `Ctrl+T` | Quick benchmark |
| `Ctrl+G` | Grid benchmark |
| `Ctrl+C` | Copy CLI command |
| `Delete` | Stop + remove selected |
| `Ctrl+A` | Select all visible |
| `Escape` | Clear selection |

### 8.3  Status/health icon indicators (U-6)

Replace text symbols with color-coded icons in the Treeview cells:

| Status | Symbol | Color |
|--------|--------|-------|
| Running | ● | Green |
| Starting | ~ | Yellow |
| Stopped | ○ | Gray |
| Stopping | ~ | Orange |
| Error | ✗ | Red |

Use `ttk.Treeview.tag_configure()` with `foreground` and a Unicode
circle/stop icon.  Color coding is ~3× faster to parse than text symbols.

### 8.4  Progress indicator during long operations (U-1)

Add a `ttk.Progressbar` at the bottom of the GUI window:

```python
self.progress = ttk.Progressbar(toolbar, mode="indeterminate")
self.progress.pack(fill="x", side="bottom")

def _show_progress(self):
    self.progress.start(15)
    self.progress.pack()

def _hide_progress(self):
    self.progress.stop()
    self.progress.pack_forget()
```

Show it whenever a benchmark, daemon start/stop, or batch operation is
active.  Hide it when the action completes (success or failure).

### 8.5  Acceptance criteria

- [ ] Common actions (start, stop, restart, benchmark) are accessible via
      keyboard shortcut or single click.
- [ ] Progress bar visible during long operations.
- [ ] Status/health indicators use color + icon, not text-only.
- [ ] GPU inventory panel can be toggled off and collapsed by default.
- [ ] No visible button clutter when no selection is active.

---

## 9.  Workstream 5 — Module Delegation (File size reduction)

**Goal:** Break the 3 814-line `gui.py` into manageable submodules using
spec-driven extraction.

### 9.1  Extract to `gui/` package

```
src/llama_orchestrator/gui/
├── __init__.py          # Re-exports LlamaOrchestratorGui
├── app.py               # LlamaOrchestratorGui class (reduced to ~1 200 lines)
├── toolbar.py           # Toolbar, search, tag filter, daemon buttons
├── table.py             # Treeview setup, column config, rendering
├── row_renderer.py      # TableRow, _build_table_rows, _render_full_rows
├── activity_log.py      # Activity ScrolledText, _append_activity
├── gpu_inventory.py     # GPU panel, _render_gpu_inventory, alias editing
├── dialogs.py           # GridBenchmarkDialog, AddModelDialog, etc.
├── benchmark_controls.py # Benchmark buttons, queue management
└── actions.py           # _run_selected, _run_batch, benchmark actions
```

### 9.2  Extraction criteria per module

- Each submodule has < 500 lines.
- Each module has a single clear responsibility.
- The main `app.py` orchestrates but does not implement UI details.
- All modules use only the `tk` standard library (no extra GUI deps).

### 9.3  Acceptance criteria

- [ ] `gui.py` is replaced by `gui/__init__.py` that re-exports the main
      class (≤ 50 lines of glue code).
- [ ] No module in `gui/` exceeds 600 lines.
- [ ] All tests in `tests/test_gui.py` pass after extraction.
- [ ] `gui.py` import paths in other modules updated accordingly.

---

## 10.  Workstream 6 — Dependency & Project Hygiene

### 10.1  Add `requirements.txt`

The project already has `pyproject.toml` but no `requirements.txt`.  Add
one for compatibility with tools that expect it (CI, Docker, etc.):

```bash
uv export -o requirements.txt
```

### 10.2  Version pinning for reproducible builds

In `pyproject.toml`, use `~=` (compatible release) for stable deps and
`>=` for major-version APIs to avoid unexpected breakage.

### 10.3  Acceptance criteria

- [ ] `uv export` produces a clean `requirements.txt` with no dev deps.
- [ ] `pip install -r requirements.txt` installs all runtime deps.
- [ ] `ruff check src/` passes with zero warnings.
- [ ] `mypy src/` passes with zero errors.

---

## 11.  Implementation Order & Dependencies

```
Phase 1 (Foundation)
  WS-6 → requirements.txt, project hygiene (no deps on other changes)

Phase 2 (Performance core)
  WS-3 → MetadataCache (depends on WS-6)
  WS-2 → Health client pool + async (independent)

Phase 3 (GUI performance)
  WS-1 → RefreshController + row diffing (depends on WS-3, WS-2)

Phase 4 (GUI usability)
  WS-4 → Action bar, shortcuts, progress bar, icons (independent of 2/3)

Phase 5 (Module extraction)
  WS-5 → gui/ package (depends on 1–4; refactor in place)
```

Estimated effort:

| Phase | Estimated days | Risk |
|-------|---------------:|-----:|
| 1 | 0.5 | Low |
| 2 | 2 | Low (well-tested async patterns) |
| 3 | 3 | Medium (Tkinter threading requires care) |
| 4 | 2 | Low (pure UI polish) |
| 5 | 3 | Medium (large refactor, requires regression tests) |
| **Total** | **~10.5 days** | |

---

## 12.  Metrics & Validation

After refactoring, measure and compare against baseline:

| Metric | Before | Target | Measurement |
|--------|--------|-------:|-------------|
| Single refresh time | ~2–5 s | < 200 ms | `time.perf_counter_ns()` in `refresh()` |
| Main-thread block | 2–5 s per cycle | < 50 ms max | `tk.update_idletasks()` timing |
| Health check latency | N × 5 s | < 1 s total | `time.perf_counter()` in monitor |
| GUI module size | 3 814 lines | < 600 lines/file | `wc -l` per file |
| Treeview rebuilds | Full delete+insert | Incremental set | Debug log of `insert`/`set`/`delete` calls |

---

## 13.  Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------:|-------:|-------------|
| Tkinter not thread-safe | High | High | All Tkinter calls stay on main thread; background thread posts via `after()` |
| Row diffing loses visual state | Medium | Medium | Preserve `selection` and `focus` across diffs |
| Async health checks break existing callbacks | Low | Medium | Keep `on_health_change` / `on_restart` callbacks as-is |
| gui/ extraction breaks existing extensions | Medium | High | Full regression test suite before extraction |
| Metadata cache staleness | Low | Medium | Hash-based invalidation; reload on config change |

---

## 14.  Out of Scope (Future Cycles)

These are explicitly **not** part of this spec:

- Migrating from Tkinter to a modern GUI toolkit (PySide6, DearPyGui).
- Adding a web-based admin panel.
- Refactoring `config/schema.py` beyond what the cache requires.
- Adding distributed / remote management features.
- MLOps integration (Weights & Biases, MLflow).

---

## 15.  Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-16 | Use Tkinter `after()` for background refresh | No external async GUI deps; `after()` is the idiomatic Tkinter pattern |
| 2026-06-16 | Use `asyncio.gather()` for health checks | Standard library, minimal code change from sequential loop |
| 2026-06-16 | MetadataCache uses content-hash invalidation | Simplest way to detect config changes without deep-equal |
| 2026-06-16 | Extract gui/ as a package, not more sub-modules | Keeps the number of import paths manageable for a Tkinter app |

---

*Spec complete. Ready for CLARIFIED → PLANNED → IN_PROGRESS transition.*
