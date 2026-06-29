# llama-orchestrator — Implementation Plan 2026

> **Version:** 1.0  
> **Date:** 2026-06-20  
> **Author:** GitHub Copilot  
> **Scope:** `infra-local/llama-orchestrator/`  
> **Current Version:** 2.1.0 (Beta)

---

## Executive Summary

`llama-orchestrator` is a Python-based control plane for managing multiple
llama.cpp server instances on Windows. The V2 implementation (phases 1–8) is
substantially complete, covering process lifecycle, SQLite state, health
monitoring, daemon, CLI, desktop GUI, benchmarking, and binary management.

This plan identifies **remaining gaps**, **technical debt**, and **prioritized
workstreams** to move the project from Beta toward a stable 2.2.0 release.
The plan is organized into **four tiers** by impact and effort:

| Tier | Name | Effort | Impact |
|------|------|--------|--------|
| **T1** | Critical Fixes & Reliability | 10–15h | High |
| **T2** | Usability & Completeness | 15–25h | High |
| **T3** | Performance & Quality | 20–40h | Medium |
| **T4** | Strategic Enhancements | 30–60h | Medium |

---

## 1. Current State Assessment

### 1.1 What Works Well

- ✅ **Modular architecture:** Clear separation across engine, health, daemon,
  gui, binaries, config (~14,000 LOC across ~55 files)
- ✅ **SQLite V2 schema:** Clean migration system with backups, WAL mode
- ✅ **Process lifecycle:** Detached mode, file locking, port collision,
  process validation via psutil
- ✅ **Health monitoring:** HTTP/TCP/custom probes, exponential backoff with
  jitter, async client pool
- ✅ **Daemon:** Event-based loop, NSSM Windows service integration
- ✅ **CLI:** Rich Typer-based interface with standardized exit codes
- ✅ **Desktop GUI:** 20+ columns, row diffing, GPU inventory, benchmark
  controls, HuggingFace import
- ✅ **Binary management:** UUID registry, versioned installs, SHA256
  verification
- ✅ **Benchmark system:** Quick + grid benchmarks with TTFT, cache reuse,
  speculative decoding metrics
- ✅ **Test coverage:** ~30 test files, ~50–70% coverage on core modules

### 1.2 Known Gaps (from README + REFACTORING_SPEC)

| # | Gap | Source | Tier |
|---|-----|--------|------|
| G1 | Manual Windows Services UI smoke testing pending | README | T1 |
| G2 | Dual state sync fragility (InstanceState + RuntimeState) | Code | T1 |
| G3 | Hardcoded `-fit off` flag may break with newer llama.cpp | Code | T1 |
| G4 | No instance deletion command (`llama-orch delete`) | Feature | T2 |
| G5 | No backup/restore CLI command | Feature | T2 |
| G6 | Missing binary convenience commands (migrate, upgrade, set-binary) | BINARY_MANAGEMENT.md | T2 |
| G7 | No GUI end-to-end tests | Testing | T2 |
| G8 | REFACTORING_SPEC.md workstreams not implemented | Spec | T3 |
| G9 | GitHub API rate limiting not handled | Code | T2 |
| G10 | No config hot-reload detection | Feature | T3 |
| G11 | Custom health probe scripts run without sandboxing | Security | T1 |
| G12 | No graceful shutdown for detached processes | Reliability | T1 |
| G13 | Port collision only checked at `up` time, not in daemon | Reliability | T1 |
| G14 | GUI performance: full Treeview rebuild every 2s | REFACTORING_SPEC | T3 |
| G15 | No rate limiting on GitHub API client | Code | T2 |
| G16 | No instance naming validation beyond path sanitization | Code | T3 |
| G17 | No TLS verification enforcement in downloader | Security | T1 |
| G18 | No dedicated TTFT/cache trend dashboards | Feature | T4 |
| G19 | No MCP gateway integration | Feature | T4 |
| G20 | No llama-swap export | Feature | T4 |

---

## 2. Tier 1 — Critical Fixes & Reliability (10–15h)

### T1-1: Fix Dual State Sync Fragility

**Problem:** `engine/process.py` maintains both legacy `InstanceState` and
V2 `RuntimeState`. If one save fails, states diverge with no transactional
guarantee.

**Solution:**
- Wrap both state writes in a single SQLite transaction
- Add atomic commit with `BEGIN TRANSACTION` / `COMMIT`
- On failure, rollback both and log the error
- Add a `state reconcile` CLI command to detect and fix divergence

**Files:** `src/llama_orchestrator/engine/process.py`, `src/llama_orchestrator/engine/state.py`

**Acceptance Criteria:**
- [ ] Both state writes succeed or both fail atomically
- [ ] `llama-orch state reconcile` detects divergence and reports it
- [ ] Unit test for atomic state write

### T1-2: Remove or Document `-fit off` Hardcoded Flag

**Problem:** `engine/command.py` always adds `-fit off` flag as a workaround
for a llama.cpp issue. This may cause problems with newer versions.

**Solution:**
- Research whether the llama.cpp issue is still relevant
- If fixed upstream: remove the flag
- If still needed: make it configurable via `server.disable_fit` and document
  the reason in code comments

**Files:** `src/llama_orchestrator/engine/command.py`

**Acceptance Criteria:**
- [ ] Flag is either removed or documented with upstream reference
- [ ] Config schema supports optional override
- [ ] No regression in benchmark results

### T1-3: Add Instance Deletion Command

**Problem:** `llama-orch down` stops an instance but doesn't remove its
config, state, or logs. No `llama-orch delete <name>` exists.

**Solution:**
- Add `llama-orch delete <name> [--force]` command
- Removes: config dir, state entries, log files, benchmark history
- `--force` skips confirmation; without it, prompts before deleting
- Updates instance catalog numbering

**Files:** `src/llama_orchestrator/cli.py`, `src/llama_orchestrator/config/loader.py`

**Acceptance Criteria:**
- [ ] `llama-orch delete gpt-oss` removes all traces
- [ ] `--force` flag for scripted use
- [ ] Confirmation prompt by default
- [ ] Unit test for delete operation

### T1-4: Add Backup/Restore CLI Command

**Problem:** State database backups are created during migration but there's
no user-facing backup/restore CLI. Manual database manipulation is needed
if corruption occurs.

**Solution:**
- Add `llama-orch backup [path]` — creates timestamped SQLite backup
- Add `llama-orch restore <path>` — restores from backup with confirmation
- Store backups in `state/backups/` with rotation (keep last 10)
- Integrate with existing migration backup logic

**Files:** `src/llama_orchestrator/cli.py`, `src/llama_orchestrator/engine/state.py`

**Acceptance Criteria:**
- [ ] `llama-orch backup` creates valid SQLite backup
- [ ] `llama-orch restore` validates backup before applying
- [ ] Backup rotation keeps last 10
- [ ] Unit test for backup/restore cycle

### T1-5: Secure Custom Health Probes

**Problem:** `CustomProbe` runs arbitrary scripts with no sandboxing or
timeout enforcement beyond the probe timeout.

**Solution:**
- Add configurable `max_execution_time` (default 30s) to healthcheck config
- Run scripts in a subprocess with timeout enforcement
- Log script output for audit trail
- Add `--allow-custom-probes` CLI flag to enable (opt-in for security)

**Files:** `src/llama_orchestrator/health/probes.py`, `src/llama_orchestrator/config/schema.py`

**Acceptance Criteria:**
- [ ] Custom scripts timeout at `max_execution_time`
- [ ] Script output logged to instance logs
- [ ] Opt-in via config or CLI flag
- [ ] Unit test for timeout enforcement

### T1-6: Add Graceful Shutdown for Detached Processes

**Problem:** `stop_detached()` sends `terminate()` but doesn't wait for
children to exit. Children may orphan.

**Solution:**
- Add `stop_instance()` with graceful shutdown sequence:
  1. Send SIGINT (or HTTP `/shutdown` if supported)
  2. Wait up to 5 seconds
  3. If still running, send SIGTERM
  4. Wait up to 10 seconds
  5. If still running, send SIGKILL/terminate()
- Log shutdown method used

**Files:** `src/llama_orchestrator/engine/process.py`

**Acceptance Criteria:**
- [ ] Graceful shutdown sequence implemented
- [ ] No orphaned processes after stop
- [ ] Shutdown method logged in events
- [ ] Unit test for shutdown sequence

### T1-7: Port Collision Check in Daemon Cycle

**Problem:** Port collision detection only runs at `up` time, not during
daemon health cycles. An instance could be restarted on a port already in
use by a new process.

**Solution:**
- Add port collision check to `DaemonService._monitor_cycle()`
- Skip restart if port is occupied by an unknown process
- Log warning and increment failure counter

**Files:** `src/llama_orchestrator/daemon/service.py`, `src/llama_orchestrator/health/ports.py`

**Acceptance Criteria:**
- [ ] Daemon checks port availability before restart
- [ ] Collision logged as warning event
- [ ] No accidental port reuse in daemon cycles

### T1-8: Enforce TLS Verification in Downloader

**Problem:** `binaries/downloader.py` may have TLS verification disabled,
allowing MITM attacks during binary downloads.

**Solution:**
- Ensure `httpx` uses default TLS verification (verify=True)
- Add explicit `verify=True` to all download requests
- Log TLS verification status for audit trail

**Files:** `src/llama_orchestrator/binaries/downloader.py`

**Acceptance Criteria:**
- [ ] All HTTP requests use TLS verification
- [ ] Explicit `verify=True` in downloader
- [ ] Unit test for TLS verification

---

## 3. Tier 2 — Usability & Completeness (15–25h)

### T2-1: Implement Missing Binary Convenience Commands

**Problem:** Per `docs/BINARY_MANAGEMENT.md`, several convenience commands
remain as future work: `migrate-bins.py`, `llama-orch init --binary-version`,
`llama-orch upgrade`, `llama-orch config set-binary`.

**Solution:**
- Add `llama-orch binary upgrade` — upgrades default binary to latest
- Add `llama-orch config set-binary <name> <uuid>` — pin instance to binary
- Add `llama-orch binary migrate` — migrate legacy `bin/` to UUID registry
- Add `llama-orch init --binary-version <version>` — specify binary on init

**Files:** `src/llama_orchestrator/cli.py`, `src/llama_orchestrator/binaries/manager.py`

**Acceptance Criteria:**
- [ ] All four convenience commands implemented
- [ ] `migrate` converts legacy `bin/` entries to UUID registry
- [ ] `set-binary` updates instance config atomically
- [ ] Unit tests for each command

### T2-2: Add GitHub API Rate Limiting

**Problem:** `GitHubClient` makes unbounded requests. GitHub rate limits at
60 requests/hour for unauthenticated API.

**Solution:**
- Add rate limit tracking with `RateLimiter` class
- Respect `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers
- Queue requests when rate limited, retry with exponential backoff
- Log rate limit warnings

**Files:** `src/llama_orchestrator/binaries/github.py`

**Acceptance Criteria:**
- [ ] Rate limiter tracks remaining requests
- [ ] Automatic retry after rate limit reset
- [ ] Warning logged when approaching limit
- [ ] Unit test for rate limit handling

### T2-3: Add Instance Naming Validation

**Problem:** Instance names are sanitized for file paths but not validated
for uniqueness across the catalog.

**Solution:**
- Add `validate_instance_name()` function
- Check against instance catalog for uniqueness
- Reject names with reserved characters, empty strings, or `.` / `..`
- Add validation to `llama-orch init` and GUI add model dialog

**Files:** `src/llama_orchestrator/config/validator.py`, `src/llama_orchestrator/cli.py`

**Acceptance Criteria:**
- [ ] Duplicate names rejected with clear error
- [ ] Reserved characters blocked
- [ ] Validation integrated into init and GUI
- [ ] Unit test for validation rules

### T2-4: Add GUI End-to-End Tests

**Problem:** No end-to-end GUI tests (Tkinter limitation). Helper functions
could be unit tested but the full GUI flow is untested.

**Solution:**
- Add `tests/test_gui_e2e.py` using `pyautogui` or `pytest-qt` for
  simulated keyboard/mouse input
- Test: add instance → start → health check → stop → delete
- Test: benchmark workflow (quick + grid)
- Test: binary install dialog flow
- Run in CI with headless mode

**Files:** `tests/test_gui_e2e.py`

**Acceptance Criteria:**
- [ ] E2E test for full instance lifecycle
- [ ] E2E test for benchmark workflow
- [ ] E2E test for binary install
- [ ] Tests run in CI (headless mode)

### T2-5: Manual Windows Services UI Smoke Test

**Problem:** NSSM service install is implemented but manual Windows Services
UI smoke testing still needs to be run on a target host.

**Solution:**
- Create `docs/WINDOWS_SERVICE_SMOKE_TEST.md` with step-by-step instructions
- Document: install NSSM, run `llama-orch daemon install`, verify in
  Windows Services UI, start/stop, check logs
- Add checklist to V2 checklist for completion

**Files:** `docs/WINDOWS_SERVICE_SMOKE_TEST.md`, `docs/LLAMA_ORCH_V2_CHECKLIST.md`

**Acceptance Criteria:**
- [ ] Smoke test document created
- [ ] Checklist updated with completion status
- [ ] Service verified on target Windows host

---

## 4. Tier 3 — Performance & Quality (20–40h)

### T3-1: Implement REFACTORING_SPEC Workstream 1 — GUI Performance

**Problem:** GUI refresh blocks the main thread every 2 seconds with
`O(N)` subprocesses, disk I/O, and Treeview rebuilds.

**Solution (per REFACTORING_SPEC.md):**
- **WS-1:** Introduce `RefreshController` — background thread produces
  `RefreshSnapshot` every 2s, main thread diffs and updates
- **WS-2:** Row-level diffing — only update changed columns instead of
  rebuilding entire Treeview
- **WS-3:** Debounce GPU inventory updates — cache for 60s, rebuild only
  when it changes
- **WS-4:** Cache `describe_effective_runtime()` — invalidate on config
  change, not on every refresh

**Files:** `src/llama_orchestrator/gui/refresh.py` (new), `src/llama_orchestrator/gui/app.py`

**Acceptance Criteria:**
- [ ] Refresh runs on background thread
- [ ] Treeview updates only changed rows/columns
- [ ] GPU inventory cached for 60s
- [ ] Runtime metadata cached, invalidated on config change
- [ ] Per-refresh latency reduced by 50%+

### T3-2: Implement REFACTORING_SPEC Workstream 2 — Health Monitor

**Problem:** `HealthMonitor` uses single-threaded blocking loop. If one
health check times out (5s), the entire cycle is delayed.

**Solution (per REFACTORING_SPEC.md):**
- **WS-1:** Convert to `asyncio` + `asyncio.gather()` for concurrent checks
- **WS-2:** Use `httpx.AsyncClient` pool (already exists in `client_pool.py`,
  integrate with monitor)
- **WS-3:** Add per-instance timeout with `asyncio.wait_for()`

**Files:** `src/llama_orchestrator/health/monitor.py`, `src/llama_orchestrator/health/client_pool.py`

**Acceptance Criteria:**
- [ ] Health checks run concurrently via asyncio
- [ ] Per-instance timeout enforced
- [ ] No blocking sleep in monitor loop
- [ ] Async client pool integrated

### T3-3: Implement REFACTORING_SPEC Workstream 4 — GUI Usability

**Problem:** GUI has 14+ action buttons per row, deep context menu, no
keyboard shortcuts, text-only status indicators.

**Solution (per REFACTORING_SPEC.md):**
- **WS-1:** Replace detail bar with contextual action bar (3–4 buttons based
  on instance state)
- **WS-2:** Add keyboard shortcuts: `Ctrl+S` (start), `Ctrl+D` (stop),
  `Ctrl+R` (restart), `Ctrl+B` (benchmark)
- **WS-3:** Add colored status/health indicators (emoji or icon-based)
- **WS-4:** Group context menu actions with separators and icons

**Files:** `src/llama_orchestrator/gui/app.py`, `src/llama_orchestrator/gui/usability.py`

**Acceptance Criteria:**
- [ ] Contextual action bar replaces 14+ buttons
- [ ] Keyboard shortcuts for start/stop/restart/benchmark
- [ ] Colored status indicators (green=ready, yellow=loading, red=error)
- [ ] Context menu grouped with separators

### T3-4: Implement REFACTORING_SPEC Workstream 5 — Module Delegation

**Problem:** `app.py` is ~3,800 LOC — the single largest file. Many constants
and functions are defined inline.

**Solution (per REFACTORING_SPEC.md):**
- Extract constants to `src/llama_orchestrator/gui/constants.py`
- Extract dialog helpers to `src/llama_orchestrator/gui/dialogs.py` (already
  partially done)
- Extract row rendering to `src/llama_orchestrator/gui/row_renderer.py`
- Extract toolbar actions to `src/llama_orchestrator/gui/actions.py`
- Target: `app.py` < 1,500 LOC

**Files:** `src/llama_orchestrator/gui/app.py`, `src/llama_orchestrator/gui/constants.py` (new)

**Acceptance Criteria:**
- [ ] `app.py` reduced to < 1,500 LOC
- [ ] Constants extracted to `constants.py`
- [ ] Row rendering extracted to `row_renderer.py`
- [ ] Toolbar actions extracted to `actions.py`
- [ ] No regression in GUI functionality

### T3-5: Add Config Hot-Reload Detection

**Problem:** Changes to `instances/<name>/config.json` are not detected
reactively. Requires GUI refresh or CLI restart.

**Solution:**
- Add `config_mtime` tracking in `config/loader.py`
- On GUI refresh, check if config mtime changed since last load
- If changed, reload config and log warning
- Add `--watch-config` CLI flag for daemon mode

**Files:** `src/llama_orchestrator/config/loader.py`, `src/llama_orchestrator/gui/refresh.py`

**Acceptance Criteria:**
- [ ] Config changes detected on refresh
- [ ] Config reloaded automatically when changed
- [ ] Warning logged on hot-reload
- [ ] `--watch-config` flag for daemon mode

### T3-6: Add Instance Naming Validation

**Problem:** Instance names are sanitized for file paths but not validated
for uniqueness across the catalog.

**Solution:**
- Add `validate_instance_name()` function
- Check against instance catalog for uniqueness
- Reject names with reserved characters, empty strings, or `.` / `..`
- Add validation to `llama-orch init` and GUI add model dialog

**Files:** `src/llama_orchestrator/config/validator.py`, `src/llama_orchestrator/cli.py`

**Acceptance Criteria:**
- [ ] Duplicate names rejected with clear error
- [ ] Reserved characters blocked
- [ ] Validation integrated into init and GUI
- [ ] Unit test for validation rules

---

## 5. Tier 4 — Strategic Enhancements (30–60h)

### T4-1: Dedicated TTFT / Cache Trend Dashboard

**Problem:** No dedicated TTFT or cache trend dashboards. Benchmark data
is stored in SQLite but not visualized over time.

**Solution:**
- Add `llama-orch benchmarks plot --instance <name> --metric ttft|tps|cache_hit_rate`
- Use `matplotlib` or `plotly` for chart generation
- Support time-range filters (last 7 days, last 30 days, all time)
- Export charts as PNG or HTML

**Files:** `src/llama_orchestrator/cli_benchmarks.py` (new), `src/llama_orchestrator/benchmark.py`

**Acceptance Criteria:**
- [ ] `llama-orch benchmarks plot` generates charts
- [ ] Supports TTFT, TPS, cache hit rate metrics
- [ ] Time-range filters work
- [ ] Charts exported as PNG/HTML
- [ ] Optional dependency (`matplotlib` or `plotly`)

### T4-2: MCP Gateway Integration

**Problem:** No MCP gateway integration. The project could expose instance
management via MCP for AI agent automation.

**Solution:**
- Add `src/llama_orchestrator/mcp_server.py` — MCP server for instance
  management
- Expose: list instances, start/stop/restart, health check, benchmark
- Use `mcp` Python SDK for server implementation
- Run as separate process or embedded in daemon

**Files:** `src/llama_orchestrator/mcp_server.py` (new)

**Acceptance Criteria:**
- [ ] MCP server exposes instance management operations
- [ ] AI agents can manage instances via MCP
- [ ] Documented in README
- [ ] Optional dependency (`mcp` SDK)

### T4-3: llama-swap Export

**Problem:** No llama-swap export. Users can't easily share or version
model configurations.

**Solution:**
- Add `llama-orch export <name> --format llama-swap`
- Export config to llama-swap compatible JSON format
- Support import from llama-swap format
- Add `llama-orch import <file>` command

**Files:** `src/llama_orchestrator/cli.py`, `src/llama_orchestrator/config/loader.py`

**Acceptance Criteria:**
- [ ] `llama-orch export` produces llama-swap compatible JSON
- [ ] `llama-orch import` accepts llama-swap format
- [ ] Round-trip export/import preserves config
- [ ] Documented in README

### T4-4: Multi-GPU Awareness Enhancement

**Problem:** GPU detection uses `vulkaninfo` subprocess which may not work
on all systems. No fallback to `nvidia-smi` or `dxdiag`.

**Solution:**
- Add fallback detection: `nvidia-smi` for NVIDIA, `dxdiag` for Windows
- Support multi-GPU configs with `--device-draft` for additional adapters
- Improve GPU alias persistence and display
- Add `llama-orch gpu list` command for inventory

**Files:** `src/llama_orchestrator/engine/detection.py`, `src/llama_orchestrator/cli.py`

**Acceptance Criteria:**
- [ ] Fallback detection works on systems without `vulkaninfo`
- [ ] `llama-orch gpu list` shows all detected GPUs
- [ ] Multi-GPU configs supported
- [ ] GPU aliases persist across restarts

### T4-5: Benchmark Comparison UI

**Problem:** No benchmark comparison/visualization beyond SQLite storage.
Users can't easily compare runs across different configs.

**Solution:**
- Add `llama-orch benchmarks compare <instance1> <instance2>`
- Generate side-by-side comparison table
- Add GUI panel for benchmark comparison
- Support statistical analysis (mean, std dev, p-value)

**Files:** `src/llama_orchestrator/cli.py`, `src/llama_orchestrator/gui/benchmark_compare.py` (new)

**Acceptance Criteria:**
- [ ] `llama-orch benchmarks compare` generates comparison table
- [ ] GUI panel for visual comparison
- [ ] Statistical analysis included
- [ ] Export comparison as CSV or Markdown

---

## 6. Implementation Order & Dependencies

```
T1-1 (Dual State) ──┐
T1-2 (-fit off) ────┤
T1-3 (Delete) ──────┼──→ T2-1 (Binary Commands) ──→ T3-1 (GUI Perf)
T1-4 (Backup) ──────┤                              T3-2 (Health Async)
T1-5 (Secure Probes)┤                              T3-3 (GUI Usability)
T1-6 (Graceful Stop)┤                              T3-4 (Module Delegation)
T1-7 (Port in Daemon)┤                             T3-5 (Config Hot-Reload)
T1-8 (TLS Verify) ──┘                              T3-6 (Naming Validation)

T2-2 (Rate Limit) ──┐
T2-3 (Naming) ──────┤
T2-4 (GUI E2E) ─────┼──→ T4-1 (TTFT Dashboard)
T2-5 (Smoke Test) ──┤              T4-2 (MCP Gateway)
                     └──→ T4-3 (llama-swap)
                                 T4-4 (Multi-GPU)
                                 T4-5 (Benchmark Compare)
```

**Recommended order:**
1. **Sprint 1 (Week 1–2):** T1-1 through T1-8 (Critical fixes)
2. **Sprint 2 (Week 3–4):** T2-1 through T2-5 (Usability)
3. **Sprint 3 (Week 5–8):** T3-1 through T3-6 (Performance)
4. **Sprint 4+ (Week 9+):** T4-1 through T4-5 (Strategic)

---

## 7. Risk Register

| ID | Risk | Probability | Impact | Mitigation |
|----|------|------------|--------|------------|
| R1 | Dual state migration breaks existing data | Low | High | Backup before migration, atomic transactions |
| R2 | `-fit off` removal breaks llama.cpp compatibility | Medium | Medium | Make configurable, test with multiple versions |
| R3 | GUI refactoring introduces regressions | High | Medium | Incremental changes, E2E tests, feature flags |
| R4 | Async health monitor breaks existing behavior | Medium | Medium | Parallel run during transition, feature flag |
| R5 | New dependencies (matplotlib, mcp) increase install size | Low | Low | Optional extras, document in pyproject.toml |
| R6 | NSSM availability varies across Windows hosts | Medium | Medium | Auto-download NSSM, document requirement |
| R7 | GitHub API rate limits block binary installs | Medium | Low | Rate limiter, cache responses, retry logic |
| R8 | Config hot-reload causes race conditions | Low | Medium | File locking, atomic writes, mtime + hash |

---

## 8. Testing Strategy

### 8.1 Coverage Targets

| Area | Current | Target | Notes |
|------|---------|--------|-------|
| Config/Schema | ~70% | ~85% | Add migration edge cases |
| Engine/Process | ~60% | ~80% | Add atomic state tests |
| Health | ~65% | ~80% | Add async monitor tests |
| CLI | ~50% | ~70% | Add delete, backup, restore tests |
| GUI | ~30% | ~50% | Add E2E tests, unit test helpers |
| Daemon | ~40% | ~60% | Add lifecycle tests |
| Binaries | ~50% | ~70% | Add rate limit, migrate tests |
| Benchmark | ~50% | ~70% | Add comparison tests |
| Memory Fit | ~60% | ~75% | Add multi-GPU tests |
| Diffusion | ~40% | ~60% | Add adapter tests |

### 8.2 New Test Files Needed

```
tests/
├── test_gui_e2e.py              # E2E GUI tests (pyautogui)
├── test_backup_restore.py       # Backup/restore CLI
├── test_delete.py               # Instance deletion
├── test_binary_upgrade.py       # Binary upgrade command
├── test_rate_limiter.py         # GitHub API rate limiting
├── test_graceful_shutdown.py    # Process shutdown sequence
├── test_port_collision_daemon.py # Port check in daemon
├── test_config_hot_reload.py    # Config change detection
├── test_state_reconcile.py      # State divergence detection
└── test_mcp_server.py           # MCP gateway integration (T4)
```

---

## 9. Documentation Updates Needed

| Document | Update | Priority |
|----------|--------|----------|
| `README.md` | Add delete, backup, restore commands | High |
| `README.md` | Document `-fit off` configurability | High |
| `README.md` | Add GPU fallback detection info | Medium |
| `README.md` | Add MCP gateway section (T4) | Low |
| `docs/LLAMA_ORCH_V2_CHECKLIST.md` | Mark T1–T3 completion status | High |
| `docs/BINARY_MANAGEMENT.md` | Add upgrade, set-binary commands | High |
| `docs/WINDOWS_SERVICE_SMOKE_TEST.md` | Create new document | High |
| `CHANGELOG.md` | Document 2.2.0 changes | High |
| `docs/SECURITY.md` | Add security model doc | Medium |

---

## 10. Release Plan

### 2.2.0 Milestone

**Target:** 2026-08-01 (8 weeks from plan approval)

**Scope:**
- All T1 (Critical Fixes)
- All T2 (Usability & Completeness)
- T3-1, T3-2, T3-4 (GUI Performance + Module Delegation)

**Not in 2.2.0:**
- T3-3 (GUI Usability) — deferred to 2.3.0 (UX polish)
- T3-5 (Config Hot-Reload) — deferred to 2.3.0 (complexity)
- T3-6 (Naming Validation) — deferred to 2.3.0 (low impact)
- All T4 (Strategic) — deferred to 2.4.0+ roadmap

**Pre-release Checklist:**
- [ ] All T1–T3 tasks completed
- [ ] Test coverage ≥ 70% across all modules
- [ ] E2E GUI tests passing
- [ ] Windows Services UI smoke test completed
- [ ] Migration tested on fresh + upgraded databases
- [ ] Documentation updated
- [ ] Changelog written
- [ ] Release notes published

---

## 11. Rollback Plan

If a release introduces regressions:

1. **Database:** Restore from `state/backups/` backup
2. **Code:** Revert to previous Git tag
3. **Config:** Restore `instances/*/config.json` from backup
4. **Binaries:** No rollback needed (UUID registry is additive)
5. **GUI:** No rollback needed (state files are session-local)

**Rollback command:**
```powershell
llama-orch restore state/backups/backup-YYYYMMDD-HHMMSS.sqlite
git checkout v2.1.0
```

---

## 12. Success Metrics

| Metric | Current | Target (2.2.0) |
|--------|---------|---------------|
| Test coverage | ~50% avg | ≥ 70% avg |
| GUI refresh latency | ~500ms full rebuild | < 100ms diff update |
| Health check cycle time | Sequential (N × 5s worst) | Concurrent (max 5s) |
| Documentation coverage | Partial | Complete |
| E2E tests | 0 | ≥ 3 scenarios |
| Security issues | 2 (probes, TLS) | 0 |
| Known gaps (README) | 4 | 0 |

---

## Appendix A: File Change Estimates

| File | Estimated Changes | Effort |
|------|------------------|--------|
| `engine/process.py` | Atomic state, graceful shutdown | 4h |
| `engine/state.py` | State reconcile, backup/restore | 3h |
| `engine/command.py` | Remove `-fit off` or make configurable | 1h |
| `cli.py` | Delete, backup, restore, upgrade, set-binary | 6h |
| `health/monitor.py` | Async conversion | 4h |
| `health/probes.py` | Secure custom probes | 2h |
| `binaries/github.py` | Rate limiting | 2h |
| `binaries/manager.py` | Upgrade, migrate commands | 3h |
| `gui/app.py` | RefreshController, row diffing, usability | 12h |
| `gui/refresh.py` | New module | 4h |
| `gui/constants.py` | New module | 1h |
| `tests/` | 10 new test files | 8h |
| `docs/` | Multiple updates | 3h |
| **Total** | | **~50h** |

## Appendix B: Dependency Changes

| Package | Change | Reason |
|---------|--------|--------|
| `matplotlib` or `plotly` | Optional `[dashboard]` | TTFT/cache trend charts (T4) |
| `mcp` | Optional `[mcp]` | MCP gateway integration (T4) |
| `pyautogui` | Dev `[test]` | GUI E2E tests (T2) |

No breaking changes to existing dependencies.

## Appendix C: Glossary

| Term | Definition |
|------|-----------|
| **Instance** | A single llama.cpp server configuration with its own port, model, and binary |
| **Runtime state** | Live process state (PID, port, status, health) stored in SQLite |
| **Desired state** | User-configured settings stored in `instances/<name>/config.json` |
| **Daemon** | Background service that monitors instances and triggers restarts |
| **Probe** | Health check method (HTTP, TCP, custom script) |
| **Binary** | Versioned `llama-server.exe` package identified by UUID |
| **Benchmark** | Performance measurement (TTFT, TPS, VRAM) against a model |
| **Grid benchmark** | Parameter sweep across multiple combinations |
| **RefreshSnapshot** | Immutable snapshot of all instance data for one GUI refresh cycle |
| **Row diffing** | Incremental Treeview update — only changed columns per row |
