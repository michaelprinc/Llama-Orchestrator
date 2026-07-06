# Sprint 1 T1-1: Canonical Runtime State & Migration (Dual State Sync Fix)

**Status:** ✅ COMPLETED
**Date:** 2026-06-24
**Test Baseline:** 530 passed, 0 failed (unchanged from Sprint 0 baseline)

## Gap Addressed

**Gap G2** from `IMPLEMENTATION_PLAN_2026.md`: Dual state sync fragility — `save_state()` and `_sync_runtime_from_state()` were called as separate operations, risking state divergence if the second operation failed after the first succeeded.

## Implementation Summary

### 1. `src/llama_orchestrator/engine/state.py` (1077 lines)

**Changes:**
- Introduced **V2 Schema** for runtime state persistence
- Created `save_state_atomic(state: InstanceState, runtime: RuntimeState | None = None) -> None`
  - Wraps both legacy state and V2 runtime writes in a **single SQLite transaction**
  - Uses `BEGIN IMMEDIATE` to ensure exclusive write lock
  - Commits both writes atomically or rolls back on any failure
- Added `_runtime_to_state(runtime: RuntimeState) -> InstanceState` helper for converting runtime-only states to legacy format
- Added `load_runtime(name: str) -> RuntimeState | None` for reading V2 runtime data
- Added `load_all_runtime() -> dict[str, RuntimeState]` for listing all runtime states
- Added `check_stale_state(state: InstanceState) -> InstanceState` for detecting stale PIDs
- Added `get_health_history(name: str, limit: int = 50) -> list[dict]` for health history queries

**Key design decisions:**
- V2 Schema is additive — legacy state format is preserved for backward compatibility
- Atomic transaction uses `BEGIN IMMEDIATE` (not `BEGIN DEFERRED`) to acquire write lock immediately
- Runtime state includes: pid, port, status, health, started_at, restart_attempts, last_health_ok_at, cmdline
- `_runtime_to_state()` converts runtime-only states to legacy format for code paths that still expect it

### 2. `src/llama_orchestrator/engine/process.py` (797 lines)

**Changes:**
- Replaced all separate `save_state()` + `_sync_runtime_from_state()` call pairs with single `save_state_atomic(state, runtime)` calls
- Updated imports: removed `check_stale_state` (now defined in `process.py` itself), added `get_health_history`
- All code paths now use atomic state+runtime sync:
  - `start_instance()` — after process start and health check
  - `stop_instance()` — after process kill and state update
  - `restart_instance()` — after restart completes
  - Health check callbacks
  - Detached mode handlers

**Affected call paths (21 total):**
- Lines 130, 159, 185, 217, 243, 269, 301, 407, 470, 488, 510, 520, 569, 588, 611, 658, 664, 669, 680, 745

### 3. `tests/test_process_v2_integration.py` (413 lines)

**Changes:**
- Updated all test mocks from `save_runtime` to `save_state_atomic`
- Fixed assertions to check `mock_save_state_atomic.called` instead of `mock_save_state.called`
- Fixed `test_stop_instance_uses_runtime_when_legacy_state_missing` — `save_state_atomic(state)` is called with only one argument in the main stop path
- Fixed `test_restart_instance_waits_by_default_and_can_skip_waiting` — `save_state` is no longer called directly (only via `save_state_atomic`)
- Fixed syntax error in `test_start_instance_detached_can_skip_readiness_wait` — removed extra `)` on line 255

## Test Results

```
530 passed, 0 failed in 152.57s (0:02:32)
```

All tests pass including:
- 11 process lifecycle integration tests in `test_process_v2_integration.py`
- All existing unit tests for state management
- All existing integration tests

## Files Modified

| File | Lines | Description |
|------|-------|-------------|
| `src/llama_orchestrator/engine/state.py` | 1077 | V2 Schema, atomic save, runtime helpers |
| `src/llama_orchestrator/engine/process.py` | 797 | Atomic save integration |
| `tests/test_process_v2_integration.py` | 413 | Mock updates, assertion fixes |

## Verification

1. ✅ All 530 tests pass
2. ✅ Pyright linting passes on modified files
3. ✅ No `save_runtime` references remain in source or tests
4. ✅ All code paths use `save_state_atomic` for state+runtime sync
5. ✅ Backward compatibility maintained — legacy state format preserved

## Next Steps

- **T1-2:** Windows-First Graceful Shutdown — implement Windows-specific graceful shutdown signals
- **T1-3:** Port Collision Handling in Daemon — add port availability checks before binding
- **T1-5:** TLS Verification & Safe Binary Downloads — add certificate verification for downloads
