# Changelog

Notable changes to `llama-orchestrator`, newest version first.

## 2.2.0 - 2026-10-12

Promotion of the 2026-09 refactoring cycle from the long-lived working copy into
the tracked repository. Source of truth for this release: `REFACTORING_SPEC.md`
and `LO_IMPLEMENTATION_REPORT_2026-09-07.md`.

### Added

- `src/llama_orchestrator/naming.py` - stdlib-only leaf module holding executable
  suffix constants, `executable_name()` and `executable_candidates()`
  (native-first, then cross-platform fallbacks). Removes duplicated name
  construction scattered across the binary manager, CLI and downloader.
- `src/llama_orchestrator/py.typed` - PEP 561 marker so the package annotations
  are visible to downstream type checkers.
- `docs/validation-severity-policy.md` - documented severity policy (error blocks,
  warning does not), including the wide-binding and port-in-use cases.
- `docs/STATE_TABLE_AUTHORITY.md` - which state tables are authoritative and how the
  dual-table divergence is bounded.
- `.github/workflows/ci.yml` - hermetic CI gates `gate-311` (Python 3.11, no `bins/`,
  no GPU, no network) and `gate-312` (Python 3.12 `tarfile` version gate), plus lint
  gates that require `ruff check .` and `mypy --strict` to be clean when no
  `mypy-baseline.txt` is present.
- `scripts/check-logging-discipline.py` - guard against logging-discipline regressions.
- Nine test modules: `test_arg_override_surfacing`, `test_binary_naming`,
  `test_binary_remove`, `test_engine_metadata`, `test_loader_concurrency`,
  `test_port_collision_policy`, `test_port_degraded_retry`,
  `test_probe_status_mapping`, `test_runtime_args`.

### Fixed

- `binary remove` crashed on a bogus import; the CLI now delegates to
  `BinaryManager.uninstall()` and persists the registry before deleting files.
- `tarfile` extraction raised `TypeError` on Python 3.11 because `filter=None` was
  passed unconditionally; extraction is now version-gated on `data_filter`.
- Lock reclamation was unsafe and flaky. Staleness is now
  `(age > max_age) OR (PID dead AND age > grace)`, default grace 5.0 s.
- 18 test failures caused by real-binary resolution; an autouse hermetic fixture is
  installed at the single `get_llama_server_path` seam.
- Pathological 404-entry `KNOWN_ARGS` (265 recursive variants) and a broken boolean
  heuristic replaced with a curated catalog of roughly 150 two-dash arguments plus
  `-fit`, with explicit handling.
- Port-in-use was reported as a warning; it is now an error.
- Probe status was inferred by sniffing message substrings; probe results now carry a
  structured `error_kind` classified by `classify_probe_outcome()`.
- Port collision was a silent no-op ending in "Manual intervention may be required.";
  it now raises a structured `PortCollisionError(ProcessError)`.
- Raw `config.args` silently shadowed built-in flags; `find_raw_arg_overrides()` now
  surfaces them (last occurrence wins, mirroring llama.cpp).
- Port ownership lookup degraded silently and had a TOCTOU at spawn; `get_port_owner`
  returns `(owner, degraded)` with a one-time warning and `PortInfo.degraded`.
- `HealthCheckBackoff` raised `AttributeError` after any state reset; the lazy
  `_calc()` now self-heals lost calculator state.
- Import-time `init_db()` side effect replaced with explicit idempotent
  initialization; `MetadataCache` crash fixed.
- Module-level `_CONFIG_CACHE` was unsynchronized across GUI/monitor/CLI threads and
  pure reads rewrote configs and the catalog DB; the cache is now synchronized.
- `stop_daemon()` escalation hardened: Windows `taskkill /F` with `check=False` plus a
  post-check confirmation, so a PID dying between liveness check and signal is not
  reported as a failure.
- `validate_log_directory` rewritten side-effect-free, resolving against
  `get_project_root()` directly.
- pydantic v2 modernization in `binaries/schema.py`: deprecated `class Config` blocks
  removed in favour of explicit `@field_serializer(..., when_used="json")`.

### Removed

- `src/llama_orchestrator/config/config_validator.py` - orphaned module whose only
  importers were rewired to `runtime_args`.
- `src/llama_orchestrator/gui/health.py` - superseded by the structured probe and
  port-collision paths.

### Quality gates

Baseline before the cycle: `ruff` 1182 findings (867 auto-fixable), `mypy --strict`
195 errors across 41 files, `pytest` 19 failed / 589 passed.
Final: `ruff check .` 0, `mypy --strict src/llama_orchestrator` 0, `pytest` 746 passed.
Test suite grew from 608 to 746.

### Dependencies

- `[dependency-groups].dev` is the single source of truth for dev dependencies.
- `requirements.txt` is generated from that group; no `-e` editable self-install lines.
- `typer` pinned; the `typer[all]` extras warning eliminated.

## 2.1.0

Clarification and safety update.

- V2 SQLite state schema with runtime/events tables.
- Explicit persisted parameter mutability metadata in `config.json`.
- Warning-level validation for wide network binding on `0.0.0.0`.
- Current-state documentation separating shipped features from roadmap ideas.
