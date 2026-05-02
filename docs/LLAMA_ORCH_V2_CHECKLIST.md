# Llama Orchestrator V2 - Implementation Checklist

> **Generated:** 2026-01-01  
> **Version:** 2.0.0  
> **Complexity:** Critical  
> **Total Tasks:** 35  
> **Estimated Effort:** 40-60 hours

---

## Overview

Tento checklist sleduje implementaci Llama Orchestrator V2 dle [Implementation Plan](./LLAMA_ORCH_V2_IMPLEMENTATION_PLAN.md).

### Status Legend

| Symbol | Meaning |
|--------|---------|
| [ ] | Not started |
| [~] | In progress |
| [x] | Completed |
| [!] | Blocked |
| [-] | Skipped/N/A |

---

## Pre-Flight Checks

- [x] **Backup current state database** - `state/state.sqlite`
- [x] **Run existing tests** - All pass before changes
- [x] **Create feature branch** - `feature/llama-orch-v2`
- [x] **Review dependencies** - Confirm psutil, httpx versions

---

## Phase 1: State & Process Reliability (15h) ✅ COMPLETE

### 1.1 Enhanced State Schema (3h)

- [x] Design new `runtime` table schema
- [x] Design new `events` table schema
- [x] Implement schema version detection
- [x] Implement migration from v1 to v2
- [x] Add backup-before-migrate safety
- [x] Write unit tests for migration
- [x] **Acceptance:** Migration preserves existing data

**File:** `src/llama_orchestrator/engine/state.py`

### 1.2 Process Validator (4h)

- [x] Create `ProcessValidation` dataclass
- [x] Implement `validate_process()` with cmdline check
- [x] Implement `find_orphaned_processes()`
- [x] Implement port verification
- [x] Handle edge cases (zombie, access denied)
- [x] Write unit tests with mocked psutil
- [x] **Acceptance:** 99% accuracy in process ownership detection

**File:** `src/llama_orchestrator/engine/validator.py` (new)

### 1.3 File Locking (2h)

- [x] Implement Windows file locking (`msvcrt.locking`)
- [x] Create `instance_lock()` context manager
- [x] Add timeout mechanism
- [x] Implement stale lock detection
- [x] Write unit tests (parallel access simulation)
- [x] **Acceptance:** No race conditions in concurrent CLI calls

**File:** `src/llama_orchestrator/engine/locking.py` (new)

### 1.4 Port Collision Detection (2h)

- [x] Implement `check_port_available()`
- [x] Check against existing instance states
- [x] Check actual socket binding
- [x] Update `start_instance()` to use port check
- [x] Add descriptive error messages
- [x] Write unit tests
- [x] **Acceptance:** Clear error when port already in use

**File:** `src/llama_orchestrator/engine/ports.py` (new)

### 1.5 Event Logging (2h)

- [x] Implement `log_event()` function
- [x] Implement `get_recent_events()` query
- [x] Implement `cleanup_old_events()` retention
- [x] Add events to existing operations (start, stop, health change)
- [x] Write unit tests
- [x] **Acceptance:** Events queryable by instance and time range

**File:** `src/llama_orchestrator/engine/state.py` (V2 schema)

### 1.6 State Reconciliation (2h)

- [x] Implement `reconcile_instance()` single check
- [x] Implement `reconcile_all()` batch check
- [x] Handle "process died" case
- [x] Handle "different process on port" case
- [x] Log reconciliation events
- [x] Write integration tests
- [x] **Acceptance:** Stale states auto-corrected within 30s

**File:** `src/llama_orchestrator/engine/reconciler.py` (new)

### Phase 1 Validation

- [x] All Phase 1 unit tests pass
- [x] Integration test: Instance survives CLI restart
- [x] Integration test: Orphan detection works
- [x] Integration test: Port collision detected
- [x] Code review completed
- [x] Documentation updated

---

## Phase 2: Logging & Daemon Reliability (12h) ✅ COMPLETE

### 2.1 Rotating Log Handler (3h)

- [x] Implement `setup_instance_logging()`
- [x] Configure `RotatingFileHandler` (10MB, 3 backups)
- [x] Update `start_instance()` to use rotating handler
- [x] Add log file path to runtime state
- [x] Write unit tests
- [x] **Acceptance:** Logs rotate at size limit

**File:** `src/llama_orchestrator/engine/logging_config.py` (new)

### 2.2 Log Tail Command (2h)

- [x] Add `--follow` / `-f` option to logs command
- [x] Add `--lines` / `-n` option
- [x] Add `--stream` option (stdout/stderr/both)
- [x] Implement async file tailing
- [x] Write unit tests
- [x] **Acceptance:** `logs -f` shows real-time output

**File:** `src/llama_orchestrator/engine/logging_config.py` (update)

### 2.3 Daemon V2 with Event-based Loop (5h)

- [x] Replace `time.sleep()` with `threading.Event.wait()`
- [x] Implement proper `stop()` method
- [x] Add shutdown timeout (default 10s)
- [x] Improve signal handling (SIGINT, SIGTERM)
- [x] Update `_main_loop()` to check `_stop_event`
- [x] Add graceful task cancellation
- [x] Write unit tests for stop behavior
- [x] Write integration test: stop within 5 seconds
- [x] **Acceptance:** Daemon stops reliably within timeout

**File:** `src/llama_orchestrator/daemon/service_v2.py` (new)

### 2.4 Windows Service Support (2h)

- [-] Research NSSM vs pywin32 approach (deferred to v2.1)
- [-] Implement `install_windows_service()` (deferred to v2.1)
- [-] Implement `uninstall_windows_service()` (deferred to v2.1)
- [-] Create `_service_entry.py` entry point (deferred to v2.1)
- [-] Add CLI commands: `daemon install`, `daemon uninstall` (deferred to v2.1)
- [-] Write installation documentation (deferred to v2.1)
- [-] **Acceptance:** Service visible in Windows Services (deferred to v2.1)

**File:** `src/llama_orchestrator/daemon/win_service.py` (deferred to v2.1)

### Phase 2 Validation

- [x] All Phase 2 unit tests pass
- [x] Integration test: Logs rotate correctly
- [x] Integration test: `logs -f` works
- [x] Integration test: Daemon stops in <5s
- [-] Manual test: Windows Service install/start/stop (deferred to v2.1)
- [x] Code review completed
- [x] Documentation updated

---

## Phase 3: Health Check Enhancements (8h) ✅ COMPLETE

### 3.1 Pluggable Health Check System (4h)

- [x] Create `HealthProbe` abstract base class
- [x] Implement `HTTPProbe` with configurable path
- [x] Implement `TCPProbe` (socket connect)
- [x] Implement `CustomProbe` (script execution)
- [x] Create `ProbeFactory` for config-driven instantiation
- [x] Update `HealthMonitor` to use probe system
- [x] Write unit tests for each probe type
- [x] **Acceptance:** Different probe types work correctly

**File:** `src/llama_orchestrator/health/probes.py` (new)

### 3.2 Config Schema Update (2h)

- [x] Add `healthcheck.type` field (http/tcp/custom)
- [x] Add `healthcheck.path` field (default: /health)
- [x] Add `healthcheck.expected_status` field
- [x] Add `healthcheck.custom_script` field
- [x] Add `healthcheck.warmup_enabled` field
- [x] Update schema documentation
- [x] Write config validation tests
- [x] Ensure backward compatibility (defaults match current behavior)
- [x] **Acceptance:** New configs work, old configs still work

**File:** `src/llama_orchestrator/config/schema.py` (update)

### 3.3 Backoff with Jitter (2h)

- [x] Implement `_calculate_backoff_with_jitter()`
- [x] Add configurable jitter factor
- [x] Update `_should_restart()` to use new calculation
- [x] Add jitter to initial delays too
- [x] Write property-based tests (Hypothesis)
- [x] **Acceptance:** Restarts don't synchronize across instances

**File:** `src/llama_orchestrator/health/backoff.py` (new)

### Phase 3 Validation

- [x] All Phase 3 unit tests pass
- [x] Integration test: TCP probe on non-HTTP server
- [x] Integration test: Custom health path
- [x] Property test: Jitter distribution
- [x] Code review completed
- [x] Documentation updated

---

## Phase 4: CLI & UX Improvements (5h) ✅ COMPLETE

### 4.1 Exit Code Standards (2h)

- [x] Create `ExitCode` enum
- [x] Update all commands to use standard exit codes
- [x] Document exit codes in `--help`
- [x] Write tests for exit code consistency
- [x] **Acceptance:** Exit codes match documentation

**File:** `src/llama_orchestrator/cli_exit_codes.py` (new)

### 4.2 Describe Command Enhancement (2h)

- [x] Show effective command in `describe`
- [x] Show runtime state (PID, uptime, memory)
- [x] Show recent events (last 10)
- [x] Show health check history
- [x] Format output with Rich panels
- [x] **Acceptance:** `describe` provides complete instance info

**File:** `src/llama_orchestrator/cli_describe.py` (new)

### 4.3 Recent Events in Dashboard (1h)

- [-] Add events panel to TUI dashboard (deferred to v2.1)
- [-] Implement real-time event updates (deferred to v2.1)
- [-] Add filtering by instance (deferred to v2.1)
- [-] **Acceptance:** Dashboard shows live events (deferred to v2.1)

**File:** `src/llama_orchestrator/tui/` (deferred to v2.1)

### Phase 4 Validation

- [x] All Phase 4 unit tests pass
- [x] Manual test: All CLI commands return correct exit codes
- [x] Manual test: `describe` shows all expected info
- [-] Manual test: Dashboard shows events (deferred to v2.1)
- [x] Code review completed
- [x] Documentation updated

---

## Final Validation ✅ COMPLETE

### Testing

- [x] All unit tests pass (`pytest tests/ -v`)
- [x] All integration tests pass
- [x] Property-based tests pass
- [x] Manual testing on Windows 10/11
- [x] Test with multiple concurrent instances
- [x] Test detach mode across terminal restarts
- [x] Test daemon start/stop cycle 10+ times

### Documentation

- [x] README.md updated with new features
- [x] CLI help text complete
- [x] Configuration schema documented
- [x] Migration guide for existing users
- [x] Troubleshooting guide updated

### Release Preparation

- [x] Version bumped to 2.0.0
- [x] CHANGELOG.md updated
- [x] All commits squashed/organized
- [~] PR created for review
- [~] CI/CD pipeline green

---

## Post-Release Monitoring

- [ ] Monitor GitHub issues for 1 week
- [ ] Collect user feedback
- [ ] Address any critical bugs immediately
- [ ] Plan v2.1 based on feedback

---

## Notes

### Decisions Made

| Decision | Rationale | Date |
|----------|-----------|------|
| Use SQLite for state | Already in use, proven, no new deps | 2026-01-01 |
| Use psutil for process detection | Standard library, cross-platform | 2026-01-01 |
| File-based locking over DB locking | Simpler, works even if DB corrupted | 2026-01-01 |
| NSSM over pywin32 for services | Easier setup, no compile needed | 2026-01-01 |

### Open Questions

| Question | Status | Assigned |
|----------|--------|----------|
| Should we support Linux daemon (systemd)? | Deferred to v2.1 | - |
| Max event retention period? | Default 7 days | - |
| Include Prometheus metrics? | Deferred to v2.2 | - |

### Dependencies Added

- None (using existing psutil, httpx, sqlite3)

### Dependencies Removed

- None

---

## Progress Tracker

| Phase | Tasks | Completed | Progress |
|-------|-------|-----------|----------|
| Pre-Flight | 4 | 4 | 100% |
| Phase 1 | 35 | 35 | 100% |
| Phase 2 | 25 | 18 | 72% |
| Phase 3 | 20 | 20 | 100% |
| Phase 4 | 12 | 8 | 67% |
| Final | 15 | 13 | 87% |
| **Total** | **111** | **98** | **88%** |

> **Note:** Deferred items (Windows Service, TUI Dashboard) planned for v2.1

---

*Last updated: 2025-01-XX (V2 Implementation Complete)*
