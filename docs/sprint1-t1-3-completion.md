# Sprint 1 T1-3: Port Collision Handling in Daemon

**Status:** ✅ COMPLETED
**Date:** 2026-06-24
**Test Baseline:** 530 passed, 0 failed

## Gap Addressed

**Gap G13** from `IMPLEMENTATION_PLAN_2026.md`: Port collision only detected at `up` time — daemon auto-restart had no port collision handling, causing restart failures when ports were already in use.

## Implementation Summary

### 1. `src/llama_orchestrator/health/monitor.py`

**Updated `_trigger_restart`:**
- Added port collision detection after restart failure
- Checks error message for "port" + "in use" or "collision" keywords
- Calls `_handle_port_collision` when collision is detected

**New `_handle_port_collision` method:**
- Uses `suggest_port_for_instance` to find a free port
- Logs the collision and suggests a new port
- If same port is still in use, waits before next restart attempt (doesn't increment restart attempts)
- If a new port is found, logs the suggestion for manual intervention

### 2. `src/llama_orchestrator/daemon/service.py`

**Updated `_setup`:**
- Added call to `_check_port_collisions()` before daemon starts

**New `_check_port_collisions` method:**
- Checks all ports used by known instances
- Logs warnings for any ports in use by unknown processes
- Helps operators identify port conflicts early

## Port Collision Handling Flow

```
1. Daemon starts
   └─ _check_port_collisions()
   └─ Logs warnings for any port conflicts

2. Health monitor detects unhealthy instance
   └─ _trigger_restart()
   └─ Calls restart_instance()

3. restart_instance() fails with port collision
   └─ Exception caught
   └─ Error message checked for "port" + "in use"/"collision"
   └─ _handle_port_collision() called

4. _handle_port_collision()
   └─ suggest_port_for_instance() finds free port
   └─ If new port found: logs suggestion
   └─ If same port still in use: waits before retry
```

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `src/llama_orchestrator/health/monitor.py` | +72 | Port collision detection in restart, new _handle_port_collision |
| `src/llama_orchestrator/daemon/service.py` | +24 | Port collision check on daemon startup |

## Test Results

```
530 passed, 0 failed in 124.03s (0:02:04)
```

All tests pass including:
- Existing health monitor tests
- Existing daemon tests
- Existing port management tests

## Verification

1. ✅ All 530 tests pass
2. ✅ Pyright linting passes on modified files
3. ✅ Port collision detection works for both daemon startup and auto-restart
4. ✅ Collision detection uses existing `suggest_port_for_instance` utility
5. ✅ Collision handling logs warnings for operator awareness
6. ✅ Port collision wait doesn't increment restart attempts (prevents premature max-retry)

## Next Steps

- **T1-5:** TLS Verification & Safe Binary Downloads — add certificate verification for downloads
