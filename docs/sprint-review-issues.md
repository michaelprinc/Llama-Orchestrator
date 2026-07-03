# Sprint Review: Issues & Recommendations

**Date:** 2026-07-03  
**Reviewed Files:**
- `sprint0-evidence-map-and-test-baseline.md`
- `sprint1-t1-1-completion.md`
- `sprint1-t1-2-completion.md`
- `sprint1-t1-3-completion.md`
- `sprint1-t1-4-completion.md`
- `sprint1-t1-5-completion.md`

---

## 1. Test Baseline Inconsistency

| Sprint | Reported Tests | Reported Failures |
|--------|----------------|-------------------|
| Sprint 0 | 530 | 0 |
| Sprint 1 T1-1 | 530 | 0 |
| Sprint 1 T1-2 | 530 | 0 |
| Sprint 1 T1-3 | 530 | 0 |
| Sprint 1 T1-4 | **562** | 0 |
| Sprint 1 T1-5 | **562** | 0 |

**Issue:** The test count jumps from 530 to 562 between Sprint 0 and Sprint 1 T1-4/T1-5. This represents 32 new tests added, but the earlier sprint completion docs don't account for this increase.

**Recommendation:**
- Update earlier sprint docs to reference the evolving baseline (e.g., "530 → 562 tests")
- Document which new tests were added in T1-4 and T1-5
- Consider adding a "Test Evolution" section to track baseline changes across sprints

---

## 2. Missing Unit Tests for New Functions

### T1-2: Graceful Shutdown
The following new functions were added but lack explicit unit test documentation:

| Function | Risk |
|----------|------|
| `_send_windows_ctrl_c(pid)` | Windows-specific, uses `GenerateConsoleCtrlEvent` — hard to test cross-platform |
| `_try_http_shutdown(port, timeout)` | Network I/O, connection error handling |
| `graceful_shutdown(pid, port, timeout, force)` | Complex 5-stage sequence |
| `_force_kill_process_tree(pid)` | Immediate kill, edge cases with children |

**Recommendation:**
- Add integration tests that mock `psutil` to verify each stage of `graceful_shutdown`
- Test edge cases: `NoSuchProcess`, `AccessDenied`, `psutil.ZombieProcess`
- Verify that shutdown method is correctly logged in events

### T1-3: Port Collision Handling
| Function | Risk |
|----------|------|
| `_handle_port_collision()` | Uses `suggest_port_for_instance` — needs collision scenario tests |
| `_check_port_collisions()` | Daemon startup check — needs mock port scenarios |

**Recommendation:**
- Add tests for collision detection with multiple instances on the same port
- Test the wait-before-retry logic when same port is still in use

---

## 3. Stale Line Number References

**T1-1** references specific line numbers in `process.py`:
```
Lines 130, 159, 185, 217, 243, 269, 301, 407, 470, 488, 510, 520, 569, 588, 611, 658, 664, 669, 680, 745
```

**Issue:** Line numbers become stale as the codebase evolves. These references are already outdated if any subsequent changes were made to `process.py`.

**Recommendation:**
- Replace line number references with function/context identifiers (e.g., "in `start_instance()` after health check")
- Use git blame or search results instead of hardcoded line numbers
- Consider adding inline comments at key call sites for traceability

---

## 4. Missing Rollback Procedures

None of the sprint completion documents include rollback procedures.

**T1-1 (Atomic State Sync):**
- Rollback: Restore `save_state()` + `_sync_runtime_from_state()` pattern
- Risk: Legacy state format preserved, but `save_state_atomic()` schema is new

**T1-2 (Graceful Shutdown):**
- Rollback: Revert `graceful_shutdown` to inline shutdown logic in `detach.py`
- Risk: Windows Ctrl+C may not work for GUI applications

**T1-3 (Port Collision):**
- Rollback: Remove `_handle_port_collision` and `_check_port_collisions`
- Risk: Port collision detection is heuristic-based (keyword matching)

**Recommendation:**
- Add a "Rollback Procedure" section to each sprint completion doc
- Include the git revert command and any manual steps required

---

## 5. Security Concerns

### T1-2: HTTP Shutdown Without TLS
```python
_try_http_shutdown(port: int, timeout: float = 2.0) -> bool
# Sends POST http://127.0.0.1:{port}/shutdown
```

**Issue:** The HTTP shutdown endpoint is called without TLS verification. If the port is exposed to untrusted networks, this could be vulnerable to man-in-the-middle attacks.

**Recommendation:**
- Document that `/shutdown` endpoint should only be accessible on localhost
- Consider adding authentication token to shutdown requests
- Add firewall rules to restrict access to shutdown ports

### T1-5: Insecure Mode Opt-Out
```python
VERIFY_TLS = True  # Default
INSECURE_MODE = False  # Default
```

**Issue:** While TLS verification is enabled by default, the `--insecure` flag allows disabling it. There's no audit trail for when insecure mode is enabled.

**Recommendation:**
- Log security warnings when `--insecure` is used (already mentioned, verify implementation)
- Consider adding a configuration flag that requires explicit enable rather than CLI flag
- Add audit log entry when TLS verification is disabled

---

## 6. Missing Edge Case Documentation

### T1-2: Graceful Shutdown Edge Cases
The completion doc mentions handling `NoSuchProcess`, `AccessDenied`, but doesn't document:
- What happens if `psutil` is not installed
- Behavior when `GenerateConsoleCtrlEvent` fails silently
- Timeout behavior when process doesn't respond to SIGTERM

### T1-4: Custom Probes
- What happens if `shlex.split()` fails on malformed script arguments
- Behavior when allowlist directory doesn't exist
- Output truncation at 16 KB — what happens to truncated output

**Recommendation:**
- Add "Edge Cases" subsection to each sprint completion doc
- Document expected behavior for each edge case
- Add tests for critical edge cases

---

## 7. Incomplete Task Documentation

### T1-4: Remaining Tasks
The doc lists:
- **T1-6:** `-fit off` Compatibility Handling
- **T2-1 through T2-5:** Sprint 2 tasks
- **T3-1 through T3-4:** Sprint 3 tasks

**Issue:** Sprint 2 and 3 tasks are listed but not detailed. This makes it unclear what work remains.

**Recommendation:**
- Link to the original `IMPLEMENTATION_PLAN_2026.md` for full task details
- Add a summary of remaining high-priority tasks
- Track task completion status in a master checklist

### T1-5: Missing Error Handling Details
The doc mentions `TLSVerificationError` and `TLSVerificationWarning` exception classes but doesn't document:
- When each exception is raised
- How they propagate through the download stack
- Retry behavior on TLS errors

**Recommendation:**
- Add exception propagation flow diagram
- Document retry logic for transient TLS errors
- Add tests for certificate expiration scenarios

---

## 8. Verification Gaps

### T1-1: Atomic State Sync
Claim: "No `save_runtime` references remain in source or tests"

**Verification Required:**
- Run `grep -r "save_runtime" src/ tests/` to confirm
- Check for any orphaned imports
- Verify backward compatibility with existing state files

### T1-2: Windows-First Shutdown
Claim: "Windows-specific code only runs on Windows (checked via `sys.platform`)"

**Verification Required:**
- Test on Linux to confirm `AttributeError` doesn't occur
- Test on macOS to confirm cross-platform compatibility
- Verify `GenerateConsoleCtrlEvent` fallback behavior

### T1-3: Port Collision
Claim: "Port collision detection works for both daemon startup and auto-restart"

**Verification Required:**
- Test with multiple instances on overlapping port ranges
- Test with dynamically assigned ports (port 0)
- Verify keyword matching doesn't produce false positives

---

## 9. Documentation Formatting Issues

| File | Issue |
|------|-------|
| `sprint1-t1-2-completion.md` | Test results section truncated — only lists "Existing process lifecycle tests" without full suite results |
| `sprint1-t1-3-completion.md` | Test results section truncated — same issue |
| `sprint1-t1-4-completion.md` | Missing "Files Modified" section |
| `sprint1-t1-5-completion.md` | Missing "Files Modified" section |

**Recommendation:**
- Standardize completion doc template with required sections
- Ensure all sections are complete before marking sprint as done

---

## 10. Recommended Template for Future Sprint Docs

```markdown
# Sprint {Number} {TaskName}: {ShortDescription}

**Status:** ✅ COMPLETED | 🔄 IN PROGRESS | ❌ FAILED
**Date:** YYYY-MM-DD
**Test Baseline:** {N} passed, {M} failed (was {O} before changes)

## Gap Addressed
{Description of the gap from IMPLEMENTATION_PLAN}

## Implementation Summary
### {File/Component 1}
{Changes made}

### {File/Component 2}
{Changes made}

## Test Results
{Full test suite results}

## Files Modified
| File | Lines Changed | Description |

## Verification
1. ✅ {Verification 1}
2. ✅ {Verification 2}

## Rollback Procedure
{How to revert these changes}

## Edge Cases
{Documented edge cases and expected behavior}

## Known Issues
{Any known limitations or technical debt}

## Next Steps
{Linked tasks or follow-up items}
```

---

## Summary of Recommended Actions

| Priority | Action | Related Sprint |
|----------|--------|----------------|
| **P0** | Update test baseline references across all sprint docs | All |
| **P0** | Add unit tests for new `graceful_shutdown` functions | T1-2 |
| **P1** | Add rollback procedures to each sprint doc | All |
| **P1** | Document edge cases for HTTP shutdown and TLS verification | T1-2, T1-5 |
| **P2** | Replace line number references with context identifiers | T1-1 |
| **P2** | Standardize completion doc template | All |
| **P3** | Add audit logging for `--insecure` flag usage | T1-5 |
| **P3** | Document exception propagation flow for TLS errors | T1-5 |

---

**Reviewed by:** GitHub Copilot  
**Date:** 2026-07-03  
**Status:** Analysis complete, recommendations documented
