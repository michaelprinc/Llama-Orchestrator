# Validation Severity Policy

**Date:** 2026-07-08
**Scope:** `src/llama_orchestrator/config/validator.py` (`ValidationIssue.severity`) and the
`lint` / `validate` CLI commands that consume it.

## Rule

Severity is assigned by *consequence*, not by how the check is implemented:

| Severity | Meaning | Examples |
|---|---|---|
| `error` | Blocks instance start or corrupts state. `ValidationResult.is_valid` is `False`; CLI lint reports "Lint failed". | Port occupied at validation time (R10/F17); model file missing or empty; GPU backend/binary mismatch; port collision between instances; duplicate runtime args with different values |
| `warning` | Does not block start; style, performance, or configuration hygiene. `is_valid` stays `True`. | Context size > 32768; thread count > CPU count; host exposure on non-loopback interfaces; device-id mismatch when the backend prefix already matches |

## Consequences

- `ValidationResult.is_valid` is `not any(severity == "error")`. Warnings never flip it.
- CLI `lint` exits with failure only on errors; warnings are printed in yellow alongside a
  successful lint (exit code 0) or inside the failure summary.
- GUI/daemon paths that call `validate_instance(..., check_runtime=True)` therefore treat an
  occupied port as a start-blocking condition.

## History

- 2026-07-08 — R10/F15+R10 implementation: `validate_port_available` changed from `warning` to
  `error` (occupied port blocks start). Covered by
  `tests/test_validator.py::TestValidatePortAvailable` (mocked and real-fixture-socket cases).
