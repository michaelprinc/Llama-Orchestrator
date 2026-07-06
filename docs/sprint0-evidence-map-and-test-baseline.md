# Sprint 0 — Evidence Map & Test Baseline

**Date:** 2026-06-23  
**Status:** ✅ Completed  
**Test Baseline:** 530 tests, 0 failures (baseline established)

## Overview

Sprint 0 established the evidence map of the codebase and verified the test baseline. Two bugs were discovered and fixed:

1. **Reconciler interval timing bug** — `Reconciler.run()` set `_last_run` at the start of the method instead of the end, causing `should_run()` to return `True` immediately after `run()` completed if `reconcile_all()` took longer than the interval.
2. **Windows-only constant on Linux** — `subprocess.CREATE_NEW_PROCESS_GROUP` was referenced unconditionally in `start_instance()`, causing `AttributeError` on Linux even when `Popen` was mocked.

## Changes Made

### 1. `src/llama_orchestrator/engine/reconciler.py`

**Bug:** `Reconciler.run()` set `self._last_run = time.time()` before calling `reconcile_all()`. If reconciliation took longer than `self.interval`, `should_run()` would immediately return `True` after `run()` returned.

**Fix:** Moved `self._last_run = time.time()` to the end of `run()`, after `reconcile_all()` completes. This ensures `should_run()` returns `False` immediately after a run, regardless of how long reconciliation takes.

```diff
     def run(self) -> ReconcileSummary:
         """Run a reconciliation pass."""
-        self._last_run = time.time()
         self._run_count += 1

         summary = reconcile_all(...)

         if self.on_reconcile:
             self.on_reconcile(summary)

+        self._last_run = time.time()
         return summary
```

**Test:** `tests/test_v2_reconciler.py::TestReconciler::test_reconciler_interval` — now passes.

### 2. `src/llama_orchestrator/engine/process.py`

**Bug:** `subprocess.CREATE_NEW_PROCESS_GROUP` was passed unconditionally to `subprocess.Popen()`. This constant only exists on Windows, causing `AttributeError` on Linux.

**Fix:** Wrapped the flag in a conditional that checks `hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")` before passing it.

```diff
             proc = subprocess.Popen(
                 cmd,
                 stdout=stdout_file,
                 stderr=stderr_file,
                 env=env,
                 cwd=str(get_project_root()),
-                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
+                **({
+                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
+                } if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else {}),
             )
```

**Tests:** All 10 `test_process_v2_integration.py` tests now pass.

## Test Results

| Suite | Tests | Passed | Failed |
|-------|-------|--------|--------|
| `test_v2_reconciler.py` | 13 | 13 | 0 |
| `test_process_v2_integration.py` | 10 | 10 | 0 |
| **Full suite** | **530** | **530** | **0** |

## Evidence Map

The codebase has 530 tests covering:
- **State management** (`test_v2_state.py`, `test_v2_reconciler.py`) — database state, runtime state, reconciliation
- **Process lifecycle** (`test_process_v2_integration.py`) — start, stop, restart, detached start, readiness waits
- **Configuration** (`test_config_loader.py`, `test_schema.py`) — config loading, validation, schema
- **Health checks** (`test_health_checker.py`, `test_health_ports.py`) — health checking, port validation
- **Binary management** (`test_binaries.py`) — binary discovery, versioning, registry
- **GUI** (`test_gui_*.py`) — GUI components, settings, event handling
- **Daemon** (`test_daemon.py`) — daemon lifecycle, signal handling
- **CLI** (`test_cli.py`) — command-line interface
- **Config discovery** (`test_config_discovery.py`) — instance discovery
- **Command building** (`test_command.py`) — command and environment building
- **Detach** (`test_detach.py`) — detached process launching
- **Locking** (`test_locking.py`) — instance locking
- **Logging** (`test_logging_config.py`) — log file management
- **Validator** (`test_validator.py`) — process validation, orphan detection
- **Event system** (`test_events.py`) — event logging and retrieval
- **Schema** (`test_schema.py`) — Pydantic schema validation
