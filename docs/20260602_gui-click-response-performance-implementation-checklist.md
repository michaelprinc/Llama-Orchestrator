# Implementation Checklist: GUI Click Response Performance

**Date:** 2026-06-02
**Purpose:** Verification checklist for the GUI click-response optimization.
**Related plan:** `20260602_gui-click-response-performance-implementation-plan.md`

---

## Phase 1: Baseline and Instrumentation

- [ ] Add optional timing guard, disabled by default.
- [ ] Measure `_on_tree_click()` end-to-end.
- [ ] Measure `_toggle_queue_name()` end-to-end.
- [ ] Measure `_toggle_selected_queue_rows()` end-to-end.
- [ ] Measure `_on_tree_double_click()` for args column.
- [ ] Measure `_on_tree_double_click()` for non-args column.
- [ ] Measure `_show_context_menu()` end-to-end.
- [ ] Measure toolbar actions that synchronously update UI.
- [ ] Measure full `refresh()` total duration.
- [ ] Measure `refresh()` sub-phases: states, configs, GPU detection, benchmark results, row build, sort, Treeview render, GPU panel, tag filter, benchmark controls, daemon status.
- [ ] Create `reports/gui-performance-baseline-20260602.md`.
- [ ] Include instance count and hardware/context in the baseline report.

## Phase 2: Queue Fast Path

- [ ] Add a helper for updating queue cells without full refresh.
- [ ] Update `_toggle_queue_name()` to use the queue fast path.
- [ ] Update `_toggle_selected_queue_rows()` to use the queue fast path.
- [ ] Ensure hidden or filtered rows are handled safely.
- [ ] Ensure `_update_benchmark_controls()` still runs after queue changes.
- [ ] Verify selected rows remain selected after queue toggle.
- [ ] Verify focus remains stable after queue toggle.
- [ ] Verify no full `refresh()` occurs for queue-only changes.
- [ ] Add unit tests for queue glyph updates.
- [ ] Add regression test for multi-selection queue toggle.

## Phase 3: Row Snapshot Refactor

- [ ] Add snapshot collection helper.
- [ ] Add row-building helper that preserves current row values.
- [ ] Add full-render helper equivalent to existing behavior.
- [ ] Verify current full refresh output is unchanged.
- [ ] Add deterministic tests for row values from fixture data.
- [ ] Add tests for benchmark result rendering.
- [ ] Add tests for config load failure fallback row rendering.

## Phase 4: Incremental Treeview Render

- [ ] Insert rows missing from current Treeview.
- [ ] Delete rows no longer visible.
- [ ] Update changed row values.
- [ ] Update changed row tags.
- [ ] Reorder rows with `tree.move()` to match desired sorted order.
- [ ] Preserve multi-selection.
- [ ] Preserve focus.
- [ ] Preserve visible row when possible.
- [ ] Handle active tag filter changes.
- [ ] Handle sort column changes.
- [ ] Handle sort direction changes.
- [ ] Handle instance creation.
- [ ] Handle instance deletion.
- [ ] Keep full-render fallback.
- [ ] Add tests comparing incremental output to full-render output.

## Phase 5: Lazy GPU Inventory

- [ ] Confirm profiling justifies GPU cache before implementation.
- [ ] Add instance-owned GPU cache if justified.
- [ ] Do not use class-level mutable cache state.
- [ ] Do not hold cache lock during GPU detection I/O.
- [ ] Atomically swap detected GPU data under lock.
- [ ] Invalidate on daemon start.
- [ ] Invalidate on daemon stop.
- [ ] Invalidate on daemon restart.
- [ ] Invalidate when GPU map is manually enabled.
- [ ] Invalidate when GPU aliases change.
- [ ] Invalidate when config GPU mapping changes.
- [ ] Verify routine queue toggle does not run GPU detection.
- [ ] Verify GPU panel updates after invalidation.
- [ ] Add error-path test for GPU detection failure display.

## Phase 6: Optional Refresh Data Cache

- [ ] Confirm earlier phases do not meet targets before adding data cache.
- [ ] Use mappings for states, configs, and benchmark results.
- [ ] Document full invalidation matrix.
- [ ] Invalidate on start, stop, restart, and daemon actions.
- [ ] Invalidate on health persistence.
- [ ] Invalidate on benchmark completion.
- [ ] Invalidate on benchmark settings changes.
- [ ] Invalidate on config add, clone, rename, args edit, delete/import if applicable.
- [ ] Invalidate on GPU alias and GPU map changes.
- [ ] Manual refresh forces fresh data.
- [ ] Auto-refresh behavior is documented and tested.
- [ ] Semaphore or reload guard only releases after successful acquisition.
- [ ] Reload state flags are protected by a lock.
- [ ] Reload failure keeps previous valid snapshot.
- [ ] Add concurrency tests for rapid click plus auto-refresh.

## Verification

- [ ] Existing test suite passes.
- [ ] New helper tests pass.
- [ ] Manual queue checkbox click is visually immediate.
- [ ] Manual selected-row queue toggle works for multiple rows.
- [ ] Manual args double-click opens editor without unwanted queue change.
- [ ] Manual right-click menu opens promptly and preserves intended selection.
- [ ] Manual sort and filter behavior remains correct.
- [ ] Manual benchmark controls enable/disable correctly.
- [ ] Manual GPU panel behavior remains correct.
- [ ] Manual auto-refresh still updates runtime state.
- [ ] No visual flicker for unchanged rows.
- [ ] Optimized measurements are recorded in `reports/gui-performance-optimization-20260602.md`.
- [ ] Any target miss is documented with follow-up.
