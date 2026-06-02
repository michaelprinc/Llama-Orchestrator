# Implementation Plan: GUI Click Response Performance Optimization

**Date:** 2026-06-02
**Type:** Performance optimization, zero intentional functional changes
**Status:** Revised after QualityGate review
**Related artifacts:**
- Requirements: `20260602_gui-click-response-performance-requirements-spec.md`
- Checklist: `20260602_gui-click-response-performance-implementation-checklist.md`
- Agent process: `20260602_gui-click-response-performance-agent-process.md`

---

## 1. Objective

Reduce perceived latency for common GUI interactions in the tkinter llama-orchestrator GUI while preserving current behavior, data formats, and workflows.

The target is immediate visual feedback for local UI-only actions and measured click-to-visible-response latency below the limits defined in the requirements spec.

---

## 2. Current Findings

The previous draft assumed that all mouse handlers call a blocking `refresh()`. The current code is more specific:

- `_on_tree_click()` only handles the `queue` column and calls `_toggle_queue_name()`.
- `_toggle_queue_name()` mutates `_queued_benchmark_names` and then calls full `refresh()`.
- `_on_tree_double_click()` does not call `refresh()` directly.
- `_show_context_menu()` selects/focuses the row and opens the menu without calling `refresh()`.
- `refresh()` clears and reinserts all Treeview rows, reloads instance states, reloads configs, detects GPUs, loads benchmark results, updates tags, updates benchmark controls, and updates daemon status.

Implication: the first implementation must measure actual handler latency before adding broad caching. Queue toggles are the most likely low-risk win because they currently force a full refresh for a single local checkbox state change.

---

## 3. Scope

### In Scope

- Optional timing instrumentation for `refresh()` and key UI handlers.
- Low-risk local update for queue checkbox state without full refresh.
- Incremental Treeview rendering that preserves sort order, selection, focus, row tags, filters, and benchmark controls.
- Lazy or cached GPU inventory only when profiling proves GPU detection contributes meaningful latency.
- Data cache only after simpler changes are measured and shown insufficient.
- Tests and performance reports for before/after comparison.

### Out of Scope

- Replacing tkinter.
- Web UI migration.
- Changing daemon, engine, health-check, benchmark, config, or state file semantics.
- Changing JSON config formats, SQLite schemas, or public CLI behavior.
- Adding new user-facing features.

---

## 4. Design Principles

1. Measure before optimizing.
2. Prefer narrow UI updates over global caching.
3. Keep tkinter calls on the main thread.
4. Treat cache invalidation as a correctness boundary, not a convenience.
5. Preserve row ordering and selection exactly.
6. Keep each phase independently revertible.
7. Avoid debounce as a broad workaround unless a measured duplicate event problem remains.

---

## 5. Implementation Phases

### Phase 1: Instrument and Baseline

**Goal:** Establish which interaction paths are actually slow.

Add optional timing instrumentation guarded by an environment variable such as `LLAMA_ORCH_DEBUG_GUI_TIMING=1`.

Measure:

- `_on_tree_click()` end-to-end.
- `_toggle_queue_name()` end-to-end.
- `_on_tree_double_click()` end-to-end for args and non-args columns.
- `_show_context_menu()` end-to-end.
- Toolbar actions that synchronously affect visible UI.
- `refresh()` total time.
- `refresh()` phases: state load, config load, GPU detection, benchmark load, row build, sort, Treeview delete/insert/update, GPU panel render, tag filter update, daemon status.

Deliverable:

- `reports/gui-performance-baseline-20260602.md`

Do not implement cache in this phase.

### Phase 2: Fast Path for Queue Toggle

**Goal:** Make the most common local UI-only click immediate.

Current behavior:

```text
queue click -> _toggle_queue_name() -> full refresh()
```

Revised behavior:

```text
queue click -> mutate _queued_benchmark_names
            -> update only the queue cell for affected visible row(s)
            -> update benchmark controls
```

Implementation notes:

- Add helper such as `_update_queue_cells(names: Iterable[str])`.
- Use `tree.set(name, "queue", format_queue_checkbox(...))` for visible rows.
- Preserve current selection and focus.
- Keep `_toggle_selected_queue_rows()` on the same fast path for selected rows.
- Do not call `refresh()` for queue-only changes.
- If a row is not visible, skip direct update; the next normal refresh will render correct state.

Acceptance:

- Queue checkbox visual state changes without full Treeview rebuild.
- Benchmark controls still enable/disable correctly.
- No impact on args double-click or context menu behavior.

### Phase 3: Extract Row Snapshot Builder

**Goal:** Separate data collection, row construction, and Treeview rendering before incremental updates.

Create an internal snapshot type that matches the current renderer needs:

```python
@dataclass(frozen=True)
class GuiRefreshSnapshot:
    states: Mapping[str, InstanceState]
    configs: Mapping[str, InstanceConfig]
    detected_gpus: tuple[DetectedGpu, ...]
    benchmark_results: Mapping[str, BenchmarkResult]
    daemon_status: DaemonStatus
    collected_at: float
```

Create helper functions/methods:

- `_collect_refresh_snapshot()`
- `_build_table_rows(snapshot) -> tuple[TableRow, ...]`
- `_visible_rows(rows, active_tag) -> tuple[TableRow, ...]`
- `_render_full_rows(rows)` as a behavior-preserving equivalent of current delete/reinsert logic.

Acceptance:

- Existing full refresh behavior remains unchanged after refactor.
- Tests can compare row values produced from deterministic fixture data.

### Phase 4: Incremental Treeview Render

**Goal:** Replace full delete/reinsert with minimal Treeview changes where safe.

Implementation requirements:

- Compare desired ordered row list with current Treeview children.
- Insert missing rows.
- Delete removed rows.
- Update changed values and changed tags.
- Reorder existing rows with `tree.move()` to match current `stable_sort_rows()` output.
- Preserve multi-selection, focus, and scroll visibility.
- Respect active tag filter.
- Preserve `_selected_names`, `_selected_name`, and `_focused_name`.

The incremental renderer must handle:

- Row value changes.
- Sort key changes.
- Sort column changes.
- Filter changes.
- Instance creation and deletion.
- Benchmark active row tag changes.

Fallback:

- Keep a full-render helper available for initialization, error recovery, and tests.

Acceptance:

- Incremental render output is equivalent to full render for the same snapshot.
- No visible flicker for unchanged rows.
- Row order remains correct after sort/filter changes.

### Phase 5: Lazy GPU Inventory

**Goal:** Avoid expensive GPU detection on refresh paths where it is not needed.

Only implement this phase if Phase 1 shows GPU detection is a meaningful contributor.

Preferred design:

- Keep GPU detection result in an instance-owned cache, not class-level globals.
- Protect shared state with a lock.
- Do not hold the lock while running I/O or detection.
- Compute new inventory in a local variable, then atomically swap under lock.
- Invalidate on daemon start/stop/restart, manual GPU map enable, config changes that affect GPU mapping, and explicit refresh if the user expects fresh inventory.

Do not run tkinter updates from background threads. Background work must post messages or schedule a main-thread update.

Acceptance:

- Routine queue toggle and context menu paths do not trigger GPU detection.
- GPU panel still updates correctly after invalidation triggers.

### Phase 6: Optional Refresh Data Cache

**Goal:** Add broader data caching only if Phases 2-5 do not meet targets.

This phase is intentionally optional and must be justified by profiling.

Requirements if implemented:

- Cache key/value must preserve mappings: `name -> InstanceState`, `name -> InstanceConfig`, `name -> BenchmarkResult`.
- Cache invalidation must cover all data sources:
  - start, stop, restart, daemon actions
  - health persistence
  - benchmark completion and benchmark settings changes
  - config add, clone, rename, args edit, delete/import if present
  - tag/filter relevant config changes
  - GPU alias and GPU map changes
  - manual refresh
  - auto-refresh
- Concurrency must use one owner lock and a checked non-blocking reload guard.
- Never release a semaphore unless acquisition succeeded.
- `_reloading` or equivalent flags must be read and written under lock.
- Reload failure must keep the previous valid snapshot and surface a debug/activity message.

Acceptance:

- Unit tests prove no concurrent reload under rapid click plus auto-refresh.
- Stale data behavior is bounded and documented.
- Manual refresh can force fresh data.

### Phase 7: Benchmark, Document, and Decide

**Goal:** Prove the change and record residual risk.

Deliverables:

- `reports/gui-performance-optimization-20260602.md`
- Updated implementation notes in this docs set if the final implementation differs.
- Tests for helper behavior and regression paths.

Acceptance:

- Baseline and optimized measurements use the same scenario.
- Any missed target has a documented reason and next action.

---

## 6. Files Expected to Change

Likely implementation files:

| File | Expected change |
|------|-----------------|
| `src/llama_orchestrator/gui.py` | Timing hooks, queue fast path, row snapshot helpers, incremental renderer, optional GPU/cache integration |
| `tests/test_gui.py` | Helper and behavior tests for queue state, row rendering, sort/filter preservation |
| `tests/test_gui_cache.py` | Only if optional refresh cache is implemented |

Likely documentation/report files:

| File | Expected change |
|------|-----------------|
| `reports/gui-performance-baseline-20260602.md` | New baseline report |
| `reports/gui-performance-optimization-20260602.md` | New final performance report |

Do not change `gui_state.py` unless profiling or implementation proves a persistent setting is required.

---

## 7. Rollback Plan

Each phase should be committed or staged separately so rollback can target the smallest change.

Rollback order:

1. Revert optional cache changes first.
2. Revert lazy GPU cache if GPU panel correctness is affected.
3. Revert incremental renderer to full render helper.
4. Revert queue fast path to `self.refresh()`.
5. Keep timing instrumentation if disabled by default and useful.

Avoid destructive git commands. Use targeted restore/revert only after checking the working tree.

---

## 8. Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| Optimizing the wrong path | High | Phase 1 measures handlers before cache work |
| Queue fast path misses button state updates | Medium | Always call `_update_benchmark_controls()` after queue changes |
| Incremental renderer breaks sort order | High | Use ordered desired rows and `tree.move()` |
| Selection/focus lost | Medium | Save and restore visible selection/focus in renderer tests |
| Cache shows stale benchmark/config data | High | Make cache optional and require complete invalidation matrix |
| Threading bug in background reload | High | Keep tkinter on main thread; lock flags; checked semaphore acquisition |
| GPU inventory stale | Medium | Explicit invalidation triggers and manual refresh behavior |

---

## 9. Success Gate

Implementation is complete only when:

- The checklist file is fully satisfied or explicitly waived with reason.
- Baseline and optimized reports exist.
- Existing tests pass.
- New tests cover the changed behavior.
- Manual GUI smoke test confirms queue click, args double-click, right-click menu, sort, filter, benchmark controls, GPU panel, and auto-refresh behavior.
