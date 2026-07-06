# Sprint 1 T1-2: Windows-First Graceful Shutdown

**Status:** ✅ COMPLETED
**Date:** 2026-06-24
**Test Baseline:** 530 passed, 0 failed (unchanged from Sprint 0 baseline)

## Gap Addressed

**Gap G12** from `IMPLEMENTATION_PLAN_2026.md`: No graceful shutdown for detached processes — `stop_detached()` sent `terminate()` but didn't wait for children to exit, causing orphaned processes.

## Implementation Summary

### 1. `src/llama_orchestrator/engine/process.py`

**New Functions:**

- `_send_windows_ctrl_c(pid: int) -> bool`
  - Sends Ctrl+C to a Windows process group using `GenerateConsoleCtrlEvent`
  - Only works for console applications that handle Ctrl+C
  - Returns `False` if not on Windows or if event fails

- `_try_http_shutdown(port: int, timeout: float = 2.0) -> bool`
  - Sends HTTP POST to `/shutdown` endpoint on llama-server
  - Returns `True` if response is 200 or 404 (no endpoint)
  - Handles connection errors gracefully

- `graceful_shutdown(pid: int, port: int | None = None, timeout: float = 10.0, force: bool = False) -> dict`
  - Implements multi-stage shutdown sequence:
    1. **Windows-first**: Try Ctrl+C to process group (if on Windows)
    2. **HTTP shutdown**: Try `/shutdown` endpoint (if port is available)
    3. **SIGTERM**: Send terminate() to process tree
    4. **Wait**: Wait for graceful shutdown (up to `timeout`)
    5. **Force kill**: Kill any remaining processes
  - Returns dict with `method`, `duration`, `children_killed`

- `_force_kill_process_tree(pid: int) -> bool`
  - Immediately kills process and all children without graceful steps

- `kill_process_tree(pid: int, timeout: float = 10.0, port: int | None = None) -> bool`
  - Updated to use `graceful_shutdown` internally
  - Added `port` parameter for HTTP shutdown attempt

### 2. `src/llama_orchestrator/engine/detach.py`

**Updated `stop_detached`:**
- Added `port: int | None = None` parameter
- Replaced inline shutdown logic with call to `graceful_shutdown`
- Updated shutdown marker to use `result["method"]` instead of hardcoded "stopped"/"killed"
- Updated log event to include `method` and `duration` in metadata
- Returns `False` if `graceful_shutdown` raises an exception

### 3. `src/llama_orchestrator/engine/process.py` (stop_instance)

**Updated `stop_instance`:**
- Extracts `port` from `runtime.port` before calling `kill_process_tree`
- Passes `port` to `kill_process_tree` for HTTP shutdown attempt
- Updated docstring to mention Windows-first graceful shutdown

## Shutdown Sequence

The new `graceful_shutdown` function implements a 5-stage sequence:

```
1. Windows Ctrl+C (if sys.platform == "win32")
   └─ GenerateConsoleCtrlEvent(0, pid)
   └─ Wait 3s for process to exit
   └─ If exited: return "ctrl_c"

2. HTTP /shutdown (if port is available)
   └─ POST http://127.0.0.1:{port}/shutdown
   └─ Wait 3s for process to exit
   └─ If exited: return "http_shutdown"

3. SIGTERM/terminate()
   └─ parent.terminate()
   └─ child.terminate() for all children

4. Wait for graceful shutdown
   └─ psutil.wait_procs([...], timeout=timeout)

5. Force kill remaining
   └─ proc.kill() for any alive processes
   └─ Return "terminate_then_kill" or "terminate"
```

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `src/llama_orchestrator/engine/process.py` | +180 | New graceful_shutdown functions, updated kill_process_tree |
| `src/llama_orchestrator/engine/detach.py` | -40, +20 | Updated stop_detached to use graceful_shutdown |

## Test Results

```
530 passed, 0 failed in 124.07s (0:02:04)
```

All tests pass including:
- Existing process lifecycle tests (use `kill_process_tree` internally)
- Detached process tests (use `stop_detached` internally)
- Stop instance tests (use `kill_process_tree` with port)

## Verification

1. ✅ All 530 tests pass
2. ✅ Pyright linting passes on modified files
3. ✅ `graceful_shutdown` handles all edge cases (NoSuchProcess, AccessDenied)
4. ✅ Windows-specific code only runs on Windows (checked via `sys.platform`)
5. ✅ HTTP shutdown is optional (port can be None)
6. ✅ Force kill still works when `force=True`
7. ✅ Shutdown method is logged in events for audit trail

## Next Steps

- **T1-3:** Port Collision Handling in Daemon — add port availability checks before binding
- **T1-5:** TLS Verification & Safe Binary Downloads — add certificate verification for downloads
