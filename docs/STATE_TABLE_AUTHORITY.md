# State Table Authority (V2)

Status: normative — defines which table in `state/state.sqlite` is authoritative for
which data, who writes it, and how divergence is detected and repaired.

Introduced with R15/D3 (implementation plan 2026-09-05). The V2 state engine keeps
two instance tables that hold *different* views of the same fact:

| Table      | Role                                            | Written by                                                                                          |
|------------|-------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `instances`| **Managed/desired state** — what the orchestrator intends (status, pid, health, restart count, config hash) | Every CLI-facing transition in `engine/process.py`, including `stop_instance`, which writes this table *only* |
| `runtime`  | **Observed process truth** (V2) — pid, port, cmdline, binary version, timestamps, last exit code/error       | `save_state_atomic(state, runtime)` on start/restart/health events; `engine/reconciler.py` via `save_runtime` (liveness corrections only) |

Supporting tables: `events` (append-only audit log), `health_history`,
`schema_info`. Neither is an authority for instance status.

## Write paths

- **Start / restart** — `start_instance` and `restart_instance` call
  `save_state_atomic(state, runtime)` with *both* objects; both tables are updated in a
  single transaction, so they always agree immediately after a start.
- **Stop** — `stop_instance` calls `save_state_atomic(state)` with `runtime=None`; only the
  `instances` table is updated (status → `stopped`, pid → NULL). The `runtime` row is
  deliberately left untouched: at stop time the process truth is "no process", and the
  stale `running` runtime row is reaped by the reconciliation cycle below, which probes
  PID liveness rather than trusting either table.
- **Reconciler** — operates on the `runtime` table only (`load_runtime` / `save_runtime`).
  It never writes `instances`. Its actions (MARKED_STOPPED, MARKED_ERROR, CLEANED_UP,
  ORPHAN_DETECTED, PID_CORRECTED) converge the runtime row toward observed reality.

## Divergence window after stop

Between `stop_instance` and the next reconciler pass, the two tables legitimately
disagree: `instances` says `stopped`/pid NULL while `runtime` still holds the last
observed `running`/pid. This is the *runtime-only divergence* case — expected, bounded
by `DEFAULT_RECONCILE_INTERVAL`, and converges automatically.

## Detection and repair

- `check_state_divergence(name)` compares `status`, `pid`, and `health` across both
  tables for one instance and returns the divergent fields (or `None`).
- `list_divergent_instances()` scans every name present in either table.
- `reconcile_state(name, prefer=...)` is a **manual** blind-copy repair tool:
  - `prefer="runtime"` — trust the observed process truth; copy runtime values into
    `instances`. Use when an out-of-band writer corrupted the legacy row while the
    process demonstrably exists.
  - `prefer="state"` — trust the managed state; copy instances values into `runtime`.
    Use in the post-stop window, where "no process" is already known to be true.
- The **automatic** path is the Reconciler (liveness-probe based), not blind copying:
  prefer it for routine convergence and reserve `reconcile_state` for manual repair.

## Database initialization (R15/D2)

`init_db()` is **not** an import side effect of `llama_orchestrator.engine.state`:
importing the module creates no files. It is idempotent (`CREATE TABLE IF NOT EXISTS` +
guarded migrations), so every entry point calls it explicitly exactly once at startup:

| Entrypoint                          | Location                                    |
|-------------------------------------|---------------------------------------------|
| CLI (all commands)                  | `cli.py` `@app.callback()` `main()`         |
| GUI                                 | `gui/app.py` `launch_gui()`                 |
| Daemon (foreground and daemonized)  | `daemon/service.py` `DaemonService.__init__`|

Library consumers that call the state functions directly must invoke `init_db()` once
before first use. The test suite mirrors this with a session-scoped autouse fixture in
`tests/conftest.py`.

## Tests

- `tests/test_v2_state.py::TestImportSideEffects` — clean import creates no SQLite file.
- `tests/test_v2_state.py::TestDualTableConsistency` — start writes both tables
  atomically; the post-stop runtime-only divergence is detected by
  `check_state_divergence` / `list_divergent_instances` and repaired by
  `reconcile_state` in both preferred directions.
- `tests/test_engine_metadata.py` — `MetadataCache.is_stale()` invalidates on removed,
  added, and mutated instance configs (R15/D4).
