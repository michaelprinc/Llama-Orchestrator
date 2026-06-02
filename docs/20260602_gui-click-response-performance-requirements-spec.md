# Requirements Spec: GUI Click Response Performance

**Date:** 2026-06-02
**Component:** `infra-local/llama-orchestrator` tkinter GUI
**Type:** Non-functional performance requirement with zero intentional functional change

---

## 1. Target State

The llama-orchestrator GUI should provide prompt visual feedback for common mouse interactions with up to 65 configured instances, without changing existing user-facing behavior.

The implementation must preserve:

- Existing config formats.
- Existing state and runtime persistence behavior.
- Existing daemon and benchmark behavior.
- Existing sort, filter, selection, focus, and row display semantics.
- Existing GUI controls and labels unless explicitly approved.

---

## 2. Performance Requirements

Measured on the project owner's local development machine unless otherwise stated:

| Interaction | Target |
|-------------|--------|
| Queue checkbox toggle, visible row | Visual checkbox update <= 50 ms |
| Multi-row queue toggle from context menu | Visible checkbox updates <= 100 ms for 65 rows |
| Args column double-click | Editor opens <= 100 ms |
| Non-args row double-click | Existing open-config behavior starts <= 100 ms before external editor cost |
| Right-click context menu | Menu opens <= 100 ms |
| Manual refresh | No stricter target; must remain correct and preferably measured |
| Auto-refresh | Must not freeze the GUI noticeably under normal 65-row usage |

If a target cannot be met because of environment constraints, the final report must document the measured result, cause, and proposed follow-up.

---

## 3. Functional Requirements

### Queue Behavior

- Clicking the queue column toggles only the intended row.
- Context-menu queue toggle still works for selected rows.
- Queue glyphs remain accurate after sort, filter, refresh, and benchmark state changes.
- Benchmark controls reflect the current queue state after toggles.

### Double-Click Behavior

- Double-click on the args column opens the inline args editor.
- Double-click on other columns preserves existing open-config behavior.
- Double-click must not accidentally toggle queue state unless the queue column behavior already explicitly does so.

### Context Menu Behavior

- Right-click on a row selects/focuses that row if needed.
- Right-click on an already selected row preserves multi-selection.
- Context menu opens promptly.
- Menu commands operate on the intended selection.

### Refresh Behavior

- Manual refresh displays fresh states, configs, benchmark results, GPU information, tag values, benchmark controls, and daemon status.
- Auto-refresh continues to update runtime state.
- Refresh preserves selection and focus when the selected/focused rows remain visible.
- Refresh handles removed rows without errors.
- Refresh handles config load failures with existing fallback display behavior.

### Sort and Filter Behavior

- Current sort order is preserved.
- Sort changes reorder rows correctly.
- Tag filter changes show the correct row set.
- Incremental rendering, if implemented, must produce the same visible row order as full rendering.

### GPU Inventory Behavior

- GPU inventory display remains accurate after invalidation triggers.
- Routine queue-only UI changes should not require GPU detection.
- Detection failure should be visible in the same style as current error handling, without crashing the GUI.

---

## 4. Non-Functional Requirements

- Timing instrumentation must be disabled by default.
- No new runtime dependency is allowed for the optimization unless explicitly approved.
- Broad refresh caching is optional and must be justified by measurement.
- Cache data must never be partially visible after failed reload.
- Background threads must not call tkinter widgets directly.
- Tests should cover helper logic and critical behavioral regressions.

---

## 5. Acceptance Criteria

The work is accepted when:

1. Baseline measurements exist.
2. Optimized measurements exist.
3. Required performance targets are met or documented with reason.
4. Existing tests pass.
5. New tests cover the changed behavior.
6. Manual GUI smoke testing covers queue toggle, multi-row queue toggle, args double-click, non-args double-click, right-click menu, sort, filter, manual refresh, auto-refresh, benchmark controls, and GPU panel.
7. The implementation checklist is complete or explicitly waived item by item.

---

## 6. Explicit Non-Goals

- No web frontend.
- No tkinter replacement.
- No daemon protocol changes.
- No benchmark algorithm changes.
- No config schema migration.
- No database schema migration.
- No feature additions beyond performance instrumentation and internal rendering improvements.
