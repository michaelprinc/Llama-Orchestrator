# T1-4 Completion: Secure Custom Health Probes

**Date:** 2026-06-24
**Status:** ✅ Completed
**Test Baseline:** 562 passed, 0 failed (increased from 530 → 562 with 32 new probe security tests in test_v2_probes.py)

## Summary

Implemented secure custom health probes with execution mode enforcement. Custom probes are now **disabled by default** and must be explicitly enabled via `execution_mode` parameter.

## Changes Made

### Source Code (`src/llama_orchestrator/health/probes.py`)

- Added `ProbeExecutionMode` enum with three modes:
  - `DISABLED` (default) — custom probes cannot be instantiated
  - `RESTRICTED` — scripts parsed via `shlex.split()`, `shell=False`, sanitized environment
  - `SANDBOXED` — similar to restricted with additional sandboxing
- `CustomProbe.__init__` raises `ProbeSecurityError` when `execution_mode == DISABLED`
- `CustomProbe.check()` uses `shlex.split()` + `shell=False` in restricted/sandboxed mode
- `CustomProbe._sanitized_env()` filters environment to safe variables only
- `ProbeConfig` includes `execution_mode: str = "disabled"` and `allowlist_directory: str | None`
- `ProbeFactory.create()` parses execution mode string to enum and passes to CustomProbe
- `ProbeFactory.from_dict()` reads `execution_mode` from dictionary config
- `ProbeFactory.from_instance_config()` delegates to `from_dict()`

### Test Updates (`tests/test_v2_probes.py`)

- Added `ProbeExecutionMode` import
- Updated all 6 `CustomProbe` instantiations to use `execution_mode=ProbeExecutionMode.RESTRICTED`
- Updated `ProbeConfig` tests to include `execution_mode="restricted"` for custom probe configs
- Fixed `test_placeholder_substitution` assertion to handle list args from `shlex.split()`

## Test Results

- **562 passed, 0 failed** (full suite)
- All 39 tests in `test_v2_probes.py` pass
- No regressions in HTTP/TCP probe tests

## Security Properties

1. **Default-deny**: Custom probes are disabled unless explicitly enabled
2. **No shell injection**: Restricted mode uses `shell=False` with `shlex.split()`
3. **Environment sanitization**: Only safe variables (PATH, HOME, TEMP, etc.) are passed
4. **Allowlist validation**: Scripts can be restricted to specific directories
5. **Output truncation**: 16 KB max output to prevent memory exhaustion

## Remaining Sprint 1 Tasks

- **T1-6**: `-fit off` Compatibility Handling

## Remaining Sprint 2 Tasks

- T2-1: Safe Delete/Archive
- T2-2: Backup/Restore
- T2-3: Binary Commands
- T2-4: GitHub Rate Limiting
- T2-5: Instance Naming Validation

## Remaining Sprint 3 Tasks

- T3-1: GUI Refresh Architecture
- T3-2: GUI Test Strategy
- T3-3: GUI Module Delegation
- T3-4: Config Change Detection
