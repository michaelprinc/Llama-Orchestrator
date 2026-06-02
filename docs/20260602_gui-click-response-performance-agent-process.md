# Agent Process: GUI Click Response Performance

**Date:** 2026-06-02
**Purpose:** Execution instructions for agents implementing the GUI performance work.

---

## Operating Rules

1. Read this file, the requirements spec, the implementation plan, and the checklist before editing code.
2. Work in small phases and verify each phase before continuing.
3. Do not introduce broad caching until profiling proves it is needed.
4. Keep tkinter widget calls on the main thread.
5. Do not change daemon, engine, config, benchmark, or health semantics unless the user approves a scope change.
6. Do not use destructive git commands.
7. Preserve unrelated user changes in the working tree.

---

## Required Start Sequence

1. Inspect current status with `git status --short`.
2. Read relevant code paths in `src/llama_orchestrator/gui.py`.
3. Confirm current handler behavior before changing it.
4. Add timing instrumentation first.
5. Create the baseline report before implementing performance changes.

---

## Implementation Sequence

Use this order unless a measured result makes a later phase unnecessary:

1. Timing instrumentation and baseline report.
2. Queue checkbox fast path.
3. Row snapshot extraction with behavior-preserving full render.
4. Incremental Treeview renderer.
5. Lazy GPU inventory if justified.
6. Refresh data cache only if justified.
7. Final benchmark report and documentation update.

After each phase:

- Run focused tests.
- Manually reason through selection, focus, sort, filter, and benchmark controls.
- Update the checklist.
- Record any deviation from the plan.

---

## Measurement Guidance

Use `time.perf_counter()` or `time.perf_counter_ns()` for timing.

Timing output must be disabled by default and enabled by environment variable. Prefer concise structured log lines that can be copied into the performance report.

Measure both:

- Handler wall time from event handler entry to return.
- Render time for the visible Treeview update.

Do not rely on estimated timings in the old plan. Replace estimates with measured values.

---

## Tkinter and Threading Guidance

- Tkinter widget reads and writes must happen on the main thread.
- Background threads may collect non-widget data.
- Background threads must communicate through the existing queue/message pump or `after()` scheduling.
- Protect shared non-widget state with locks.
- Do not hold locks during I/O-heavy detection or file/database reads unless there is a proven reason.
- If using a semaphore or non-blocking guard, track whether acquisition succeeded and only release when it did.

---

## Cache Decision Rules

The default answer is no broad cache.

Add a refresh data cache only if:

- Baseline and post-Phase 4 measurements still miss the requirements.
- The slow portion is repeated data collection, not Treeview rendering.
- A complete invalidation matrix is implemented and tested.

If a cache is added, it must store data in structures compatible with the renderer:

- `name -> InstanceState`
- `name -> InstanceConfig`
- `name -> BenchmarkResult`
- GPU inventory as a tuple
- daemon status as part of the collected snapshot or a separately fresh read

---

## Testing Guidance

Prefer helper-level unit tests over fragile full tkinter integration tests where possible.

Required test themes:

- Queue glyph changes without full refresh.
- Multi-row queue toggle.
- Row builder output from deterministic data.
- Incremental renderer equivalence to full render.
- Sort and filter preservation.
- Selection and focus preservation.
- GPU cache invalidation if GPU cache is implemented.
- Reload concurrency if refresh cache is implemented.

---

## Reporting Guidance

Create two reports:

- Baseline report before optimization.
- Optimization report after implementation.

Each report should include:

- Date and commit/worktree context.
- Instance count.
- Machine/runtime notes relevant to timing.
- Interaction measurements.
- `refresh()` phase breakdown.
- Interpretation and next action.

---

## Stop Conditions

Stop and ask for direction if:

- The measured slow path is outside `gui.py`.
- Meeting the requirement requires changing daemon, engine, or config semantics.
- Reliable verification requires a manual GUI session that cannot be run in the current environment.
- Existing tests fail for reasons unrelated to the current changes and block meaningful verification.
