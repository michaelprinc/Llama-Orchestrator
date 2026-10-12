# Llama Orchestrator Refactoring — Implementation Report

**Date:** 2026-09-07 (UTC)
**Plan:** `LO_IMPLEMENTATION_PLAN_2026-09-05.md` (defects F1–F19 → requirements R1–R26 → tasks T0.1–T5.2, Phases 0–5)
**Scope:** `infra-local/llama-orchestrator-refactoring/` only. The sibling folder
`infra-local/llama-orchestrator/` was **not modified** by this work (verified by full-tree diff, §10).
**Constraints honored:** no LLM / llama-server instances started (GPU in use), no daemon restarts or
daemon-side state writes, no git commits (shared submodule gitdir — see §2.3), all verification on
Python 3.11 without binaries, GPU, or network dependencies.

---

## 1. Executive summary

All five phases and all 26 requirements were implemented in the refactoring copy. The final gate is
fully green:

| Gate | Baseline (pre-change) | Final |
|---|---|---|
| `ruff check .` | **1182 errors** (867 auto-fixable) | **0 — "All checks passed!"** |
| `mypy --strict src/llama_orchestrator` | **195 errors in 41 of 73 files** | **0 errors in 72 source files** |
| `pytest -q --no-cov` | **19 failed / 589 passed** | **746 passed / 0 failed** (161.3 s) |

The suite grew from 608 to 746 tests with zero regressions at any step; every phase ended on a full
green run. Two previously-crashing production paths (`binary remove`, tar extraction on Python 3.11)
now have regression tests that fail against the original baseline. Five unplanned latent defects were
found and fixed along the way (§8). One documented deviation remains (T3.4, §9.1).

Test-count progression (passed+failed of each full-suite run, in task-completion order):
589+19 → 594+19 (T1.1) → 595+18 (T1.2) → 602+18 (T1.3) → **620+0** (T1.4 — first fully green run;
the last 18 failures were binary-resolution artifacts fixed by the hermetic fixture) → 652 (T1.5) →
653 (T1.6) → 687 (T2.1) → 693 (T2.2) → 701 (T2.3) → 718 (T3.2) → 717 (T3.1) → 728 (T3.3) →
733 (T3.4; re-confirmed by T4.1/T4.3/T4.4) → 740 (T4.5) → 742 (T4.6) → **746** (T5.2, final gate).
Counts are the recorded full-suite runs in `.agent/QUALITY_STATE.md`; every run from T1.4 onward
finished with zero failures.

---

## 2. Environment & method

### 2.1 Toolchain
- Python **3.11.2** (`/usr/bin/python3.11`), fresh venv at `llama-orchestrator-refactoring/.venv`.
- ruff **0.16.6**, mypy **2.3.1**, pydantic **2.13.5**, pytest **9.1.1**, types-psutil 7.2.2 — all
  recorded in `pyproject.toml` dev extras and mirrored in `requirements.txt`.
- No `python3.12` interpreter exists locally; the 3.12 CI job (T0.4) is defined but can only be
  exercised in CI (documented deviation, §9.3).

### 2.2 Hermeticity
The suite runs with **no llama binaries installed**: an autouse fixture (`tests/conftest.py::
hermetic_llama_server_path`, T1.4) patches the single binary-resolution seam
(`engine.command.get_llama_server_path`) for every test; registry/resolution-order behavior is covered
by dedicated binaries tests with temp-root redirection. No GPU, no network, no daemon required.

### 2.3 Git situation (why there are no commits)
The refactoring folder is a **git submodule worktree sharing its gitdir** with the protected sibling.
Per task constraints, nothing was committed. Change tracking therefore uses:
- full-tree `diff -rq` against the untouched sibling folder (§6 footprint), and
- per-task records in `.agent/QUALITY_STATE.md`.

Note: during the session, HEAD of the shared gitdir moved from baseline `b51ab3c` to `1b81141`
("Update Qwen3 instance configuration and add deep analysis artifacts", authored by the repository
owner on 2026-09-06) — that commit added pre-existing WIP/analysis files (incl. `runtime_args.py`,
`health/client_pool.py`, `.agent/`, this plan). It was not created by this work and does not affect
the footprint below, which is measured against the sibling working tree — identical to this copy at
implementation start except for `instances/00000097…/config.json`, where the owner's own Qwen3 edit
(sibling mtime 2026-09-06 13:20, commit `1b81141`) postdates the copy (§10).

---

## 3. Phase 0 — Foundations (T0.1–T0.4)

| Task | Change | Evidence |
|---|---|---|
| T0.1 R1 | `[dependency-groups].dev` as single source of truth (holds `types-psutil`, fixes F10 `typer[all]` warning) | fresh venvs: `pip install -e .[dev]` and `pip install -r requirements.txt` both clean, no warnings |
| T0.2 R2 | typer pinned; extras warning eliminated | same venv verification |
| T0.3 R3 | `requirements.txt` generated from the dev group | install-clean in fresh venv |
| T0.4 R4 | `.github/workflows/ci.yml` (gate-311 + gate-312) added; ruff 1182 → **0** via safe autofix (907), audited unsafe CRLF/whitespace pass (277, whitespace-only diff review), ~26 manual fixes; `[tool.ruff.lint]` config migration | `ruff check .` = "All checks passed!"; unsafe-fix regression caught and fixed (`save_state` re-export restored with noqa+comment); mypy baseline mechanism built code-only/line-independent (81 entries) with local CI self-test → "OK: no new lint findings" |

---

## 4. Phase 1 — Crash elimination & dead-validation defect (T1.1–T1.6)

| Task | Defect | Change | Evidence |
|---|---|---|---|
| T1.1 R5/F1 | `binary remove` crashed on bogus import | CLI delegates to `BinaryManager.uninstall()` (registry persisted **before** file deletion); new public `instances_using()` in-use guard with fail-fast pre-check, `--force` override | NEW `tests/test_binary_remove.py` (5 tests incl. persist-before-delete proven by simulated rmtree failure) — regression test that fails against HEAD |
| T1.2 R6/F2 | tar extraction `TypeError` on 3.11 (`filter=None` unconditional) | version gate in `downloader.extract_tar_gz`: `data_filter` branch (3.12+) vs plain `extractall` (<3.12, manual member check as guard) | `test_extract_tar_gz` passes **unskipped** on 3.11; ≥3.12 branch owned by CI gate-312 |
| T1.3 R7/F3 | lock staleness flake + unsafe reclamation | policy: stale iff `(age > max_age) OR (PID dead AND age > grace)`; defaults grace 5.0 s / max_age 300.0 s; injectable `PidOracle` failing **closed** (alive on any error) | deterministic `test_lock_timeout` + NEW `TestLockStalenessMatrix` (7 oracle-injected tests); **20/20 repeated runs green** (flake AC) |
| T1.4 R8/F4 | 18 test failures from binary resolution | autouse hermetic fixture at the single `get_llama_server_path` seam | **620 passed / 0 failed — first fully green run**; AC: affected files pass with and without a temporary fake binary, tree verified clean after |
| T1.5 R9/F15 | pathological 404-entry `KNOWN_ARGS` (265 recursive variants), broken boolean heuristic | curated catalog (~150 two-dash + `-fit`); explicit boolean membership; unknown flags first-class in `named` via new `unknown: frozenset[str]`; true immutability (`dataclasses.replace`); backend-aware `GPU_DEVICE_CONFLICT` in `find_conflicts` | NEW `tests/test_runtime_args.py` (32 tests ≥ 25 required) incl. F15 repro AC (vulkan + `--device CUDA1` → non-empty ERROR from `lint_config`) and **old-vs-new parse diff over all 90 real configs**: 89/89 flag-bearing changed; transitions 204 flag→value, 173 unclassified→flag, 694 unclassified→value; zero positional strays. Diff also corrected the catalog (`--reasoning`, `--flash-attn` take values) |
| T1.6 R10/F17 | port-in-use reported as warning | severity → **error**; policy documented in NEW `docs/validation-severity-policy.md` (error = blocks start, warning = hygiene) | updated mock test + NEW fixture-socket test (real bound socket → exactly one error on `server.port`) |

---

## 5. Phase 2–3 — Runtime robustness (T2.x, T3.x)

| Task | Defect | Change | Evidence |
|---|---|---|---|
| T2.1 R12/F6 | probe status from message substring sniffing | structured `error_kind` on probe results; pure `classify_probe_outcome()` (success > 503-loading > kind, fail-closed ERROR); all `message_lower` branches deleted (grep gate: zero left) | NEW `tests/test_probe_status_mapping.py` (34 tests: classifier table, real loopback HTTP server, httpx-mocked timeouts, legacy rows, TCP + custom probes, async bridge). Bonus defect fixed: missing `httpx.TimeoutException` handler in `client_pool._probe_http_async`; legacy 200-without-JSON → LOADING |
| T2.2 R11/F5 | port collision = silent no-op with "Manual intervention may be required." dead end | structured `PortCollisionError(ProcessError)`; type-based detection (all sniffing deleted); default = ERROR state + error-level `port_collision` event + attempt counted by restart policy; **opt-in** re-porting (`reassign_ports_on_collision=True`) via non-persisted one-shot override + `port_reassigned` warning | NEW `tests/test_port_collision_policy.py` (6 tests: real two-instance same-port through the real start path, default vs opt-in, max-retries exhaustion, source-scan AC). Bonus defect fixed: `cli_describe` recent-events read key `timestamp`, schema column is `ts` → "None" rendering |
| T2.3 R18/F6-override | raw `config.args` silently shadowing built-in flags | `find_raw_arg_overrides()` (last occurrence wins, mirrors llama.cpp); INFO log at start with both values; describe rich section + JSON `arg_overrides` | NEW `tests/test_arg_override_surfacing.py` (8 tests incl. AC log-line via caplog on the real start path) |
| T3.1 R13/F11 | port ownership lookup degraded silently; TOCTOU at spawn | `get_port_owner → (owner, degraded)` with one-time warning + `PortInfo.degraded`; new bounded SO_REUSEADDR-aware `acquire_port_for_spawn()`; fail-closed `PortCollisionError` when pre-check passed but bind failed; post-spawn re-probe classifies TOCTOU collisions **without masking** the original failure | NEW `tests/test_port_degraded_retry.py` (16 tests incl. live-listener degradation rows, real thread releasing a port at ~0.4 s, spawn wiring). Bound-but-not-listening Linux blind spot empirically verified and documented (harmless for spawn) rather than falsely "fixed" |
| T3.2 R14/F8 | `HealthCheckBackoff` AttributeError after any state reset | lazy `_calc()` self-heals lost calculator state; both public paths go through it | NEW AC test in `test_v2_backoff.py` (simulate `_calculator = None`, assert repair) |
| T3.3 R15/F9-state | import-time `init_db()` side effect; dual-table divergence unbounded/undocumented; MetadataCache crash | explicit idempotent `init_db()` at all three entry points (CLI callback, GUI launch, daemon service); NEW `docs/STATE_TABLE_AUTHORITY.md` (instances = managed/desired, runtime = liveness, atomic dual-write on start, reconcile-bounded divergence window); session-scoped `ensure_state_db` test fixture; MetadataCache invalidation on removed instances | NEW `tests/test_engine_metadata.py` (6 is_stale tests) + import-side-effect subprocess test + 3 dual-table consistency tests. **Bonus crash fixed:** `model_dump()` default mode left `Path` fields → `json.dumps` TypeError in `is_stale`; new shared `_config_hash` with `mode="json"`. Live AC: fresh-cwd `llama-orchestrator ps` creates+migrates DB; `--help/--version` exit before it |
| T3.4 R16/F16 | module-level `_CONFIG_CACHE` unsynchronized across GUI/monitor/CLI threads; pure reads rewrote configs + catalog DB | module `_CONFIG_LOCK` guarding cache mutations, file reads (stat+read atomic) and persist section; `load_config()` now a **pure read** (identity backfill in-memory only); new public `repair_config()` as the explicit write op; legacy self-heal preserved at the 3 discovery entry points only | old backfill test replaced by 3 tests (non-mutation incl. mtime_ns+catalog, explicit repair, discovery normalization) + NEW `tests/test_loader_concurrency.py` (4×25-thread pure-read stress → byte-identical; 6-thread distinct-file; 8-thread same-file → no torn writes). Documented deviation: `_repair_duplicate_instance_numbers()` still persists (§9.1) |

---

## 6. Phase 4–5 — Quality gates & final items (T4.x, T5.x)

| Task | Change | Evidence |
|---|---|---|
| T4.1 R21/F19 | deleted orphaned `config/config_validator.py` (its only importers — `config/validator.py`, tests — rewired to `runtime_args`); grep gate: zero references remain | full suite green after deletion |
| T4.2 R22 | ruff fully clean, no ignores needed (5 dead noqa removed; 22 remaining justified) | `ruff check .` = 0, held for every subsequent task |
| T4.3 R23 | **mypy --strict full tree: 195 → 0 errors** (batches: app.py+companions, gui package, 13 non-GUI files); `mypy-baseline.txt` deleted; ci.yml auto-switches to full-clean mode | 0 errors in 72 files. Patterns: explicit dict annotations, isinstance-narrowed HTTP response handling (`_check_status`/`_handle_response`/`_handle_list_response`), tkinter parent union typing `tk.Tk \| tk.Toplevel`, re-exports from true leaf modules. Dead code removed (grep-verified zero importers): `gui/health.py` deleted; 3 dead dialog classes removed from `gui/dialogs.py`. **Latent crash fixed:** POSIX NameError in `daemon/service.py` (`subprocess` imported only in win32 branch but referenced by shared except clause). Regression caught+fixed: explicit GUI base class broke MagicMock-tkinter metaclass → conditional-base pattern restored. psutil `laddr` tuple-union stub over-approximation → scoped `# type: ignore[union-attr]` + comment (runtime identical) |
| T4.4 R24 | pydantic v2 modernization in `binaries/schema.py`: deprecated `class Config` blocks removed; explicit `@field_serializer(..., when_used="json")` preserve legacy byte format | schema dumps **byte-identical** to baseline; 0 deprecation warnings. API trap documented: `mode="json"` is rejected (AssertionError) — `when_used="json"` is the selector; native JSON datetimes emit `Z` vs old `+00:00`, so explicit isoformat serializers kept |
| T4.5 R17/F18 | NEW leaf module `naming.py` (stdlib-only): suffix constants, `executable_name()`, `executable_candidates()` (native-first + cross-platform fallback), `locate_executable()`; all 35 `.exe\b` sites in 9 src files replaced | gate `\.exe\b` on src/ → exactly **2 legitimate hits** (naming.py constant; downloader Windows-only branch). POSIX resolution tries extensionless first, then the Windows-named file of a package created elsewhere. NEW `tests/test_binary_naming.py` (7 tests incl. R17 AC: start resolves native-named bin file on POSIX via isolated project root) |
| T4.6 R26/F20 | `stop_daemon()` escalation hardened: Windows `taskkill /F` with **check=False** + post-check confirmation (a PID dying between liveness check and signal reads as success, not a raised `CalledProcessError`); POSIX SIGTERM/SIGKILL guarded with `except ProcessLookupError: return True`; post-SIGKILL "alive" treated as likely-unreaped zombie → warning + True | 2 NEW tests in `test_v2_daemon.py`: dead-PID returns True (POSIX, real dead PID via Popen) and Windows fake-platform test asserting exactly one `/F` call with `check is False`. File now 15 passed; full suite 742 green |
| T5.2 R19+F16 / R20+F19 | **(R19)** `validate_log_directory` rewritten **side-effect-free**: resolves against `get_project_root()` directly (dropped `get_logs_dir()` — whose `mkdir(exist_ok=True)` was itself a lint-time side effect — plus the mkdir + `.write_test` probe); writability judged at the nearest existing ancestor via `os.access(W_OK)`: missing dir passes when its ancestor is writable (runtime start paths `engine/process.py:325`, `engine/detach.py:68/106`, `engine/logging_config.py:69` all mkdir at start/attach), existing non-writable dir → error — mirroring the old pass/fail outcome with **zero mutation**. **(R20)** deleted the no-op `GpuConfig.validate_gpu_config` model_validator from `schema.py`; `config/validator.py::validate_gpu_config` is now the single implementation | TestValidateLogDirectory reworked (3 tests) + NEW R19 acceptance test `test_lint_read_only_checkout_creates_zero_files`: chmod-555/444 simulated read-only checkout, rglob before/after diff **== ∅**, and pass/fail parity with the old mkdir-probe (both log paths error identically). NEW R20 test asserts schema construction stays permissive and `validate_instance` yields exactly **one** warning on `gpu.layers`. Targeted: 24 passed |
| T5.1 all | full gate per DoD §8 | see §7 |

---

## 7. Final gate (T5.1, Definition of Done §8)

Run in the project venv (Python 3.11.2), no llama binaries installed, no GPU access required,
no network dependency by design:

```
$ ./.venv/bin/python -m ruff check .
All checks passed!

$ ./.venv/bin/python -m mypy --strict src/llama_orchestrator
Success: no issues found in 72 source files

$ ./.venv/bin/python -m pytest -q --no-cov
======================= 746 passed in 161.29s (0:02:41) ========================
```

DoD checklist:

| # | Criterion | Status |
|---|---|---|
| 1 | Every R1–R26 has ≥ 1 passing acceptance-criteria test | ✅ per-task evidence above; AC tests named in §3–§6 and `.agent/QUALITY_STATE.md` |
| 2 | ruff / mypy --strict / pytest green on 3.11, no bins/GPU/network; 3.12 job defined | ✅ gate above; 3.12 job in ci.yml (local interpreter unavailable — §9.3) |
| 3 | Regression tests for the two previously-crashing paths | ✅ `test_binary_remove.py` (F1), `test_tls_verification::test_extract_tar_gz` unskipped on 3.11 (F2); both fail against the original baseline |
| 4 | `lint_config` catches the F15 backend/`--device` mismatch repro | ✅ vulkan + `--device CUDA1` → non-empty ERROR issue (field `args`), plus module-level `find_conflicts` returns both GPU_DEVICE_CONFLICT and PARALLEL_CONFLICT |
| 5 | No files modified outside plan tasks; daemon untouched | ✅ source footprint in §10 is exactly the task set; no daemon started/restarted at any point. Runtime state under `state/`/`logs/` changes on both copies by design (sibling: live daemon + owner edits; refactoring copy: test runs — the session-scoped `ensure_state_db` fixture mirrors production entry points, and T3.3's live AC check ran the CLI there) — no source file under `instances/`, `state/`, or `logs/` was modified by this work |

---

## 8. Unplanned defects found & fixed along the way

1. **POSIX NameError in `daemon/service.py`** — `import subprocess` lived only in the win32 branch
   while the shared `except (OSError, subprocess.SubprocessError)` clause evaluated on POSIX too →
   crash whenever a dead PID made `os.kill(pid, 0)` raise. Import moved to module top (T4.3);
   escalation hardened further in T4.6.
2. **MetadataCache hashing crash** — `model_dump()` default mode left `Path` fields as `PosixPath` →
   `json.dumps` TypeError in `load_all`/`is_stale` on any real config (T3.3; fixed with shared
   `_config_hash`, `mode="json"`).
3. **cli_describe event timestamps rendered "None"** — recent-events normalization read dict key
   `timestamp`; the schema column is `ts` (T2.2).
4. **Missing httpx timeout handling** — `client_pool._probe_http_async` caught only builtin
   `TimeoutError`; `httpx.ReadTimeout/ConnectTimeout` fell into the generic error branch (T2.1).
5. **Catalog misclassification in real configs** — old-vs-new parse diff revealed `--reasoning` and
   `--flash-attn` take values (`on/off/auto`, ×72/×73 occurrences); reclassified from booleans (T1.5).

---

## 9. Documented deviations & design decisions

1. **T3.4 deviation** — `_repair_duplicate_instance_numbers()` still persists renumbered duplicates
   via `save_config` at the discovery entry points (a named repair op, not a pure read); required to
   preserve the "failed prior add must not block GUI load" behavior. `load_config()` itself is a pure
   read; all other self-heal is confined to the three discovery entry points and only fires for
   legacy/renumbered files (steady-state reads on a normalized tree write nothing — verified).
2. **naming.py cross-platform fallback** — POSIX resolution deliberately tries the Windows-named file
   of a package created on another platform, so Windows-downloaded packages remain discoverable on
   POSIX (exercised by unmodified test fixtures still using `.exe` filenames).
3. **Python 3.12 job** — defined in `ci.yml` but not locally verifiable (no python3.12 interpreter in
   this container); the declared floor is 3.11, where everything runs green.
4. **R13 bound-but-not-listening blind spot** — on Linux a SO_REUSEADDR socket that bound without
   `listen()` is invisible to `/proc/net/tcp` and psutil; empirically verified harmless for spawn
   (second bind+listen succeeds and receives all connections), so it is documented in the
   `check_port_available` docstring rather than falsely "fixed".
5. **Git** — no commits possible (shared submodule gitdir, protected sibling); footprint tracked via
   sibling diff + `.agent/QUALITY_STATE.md`. The owner's own commit `1b81141` landed mid-session in
   the shared gitdir; it added pre-existing WIP/analysis files and does not intersect this work's
   file set.

---

## 10. Change footprint (verified by full-tree diff vs. untouched sibling)

| Area | Modified | New | Deleted |
|---|---|---|---|
| `src/llama_orchestrator/**` | 58 files (functional rewrites + ruff/mypy passes; CRLF normalization included in T0.4's audited whitespace-only pass) | `naming.py` | `config_validator.py`, `gui/health.py` (dead, grep-verified zero importers); 3 dead dialog classes removed from `gui/dialogs.py` |
| `tests/**` | 24 files | 9 new: `test_binary_remove.py`, `test_runtime_args.py`, `test_probe_status_mapping.py`, `test_port_collision_policy.py`, `test_arg_override_surfacing.py`, `test_port_degraded_retry.py`, `test_engine_metadata.py`, `test_loader_concurrency.py`, `test_binary_naming.py` | — |
| `docs/` | 0 | `STATE_TABLE_AUTHORITY.md`, `validation-severity-policy.md` | — |
| CI / packaging | `pyproject.toml`, `requirements.txt` | `.github/workflows/ci.yml` | — |
| `instances/` | **0 by this work** (no refactoring-side instance file touched since copy creation; the single diff vs sibling = the owner's Qwen3 ngram-mod edit on the sibling side, mtime 2026-09-06 13:20, matching commit `1b81141`) | — | — |
| `state/`, `logs/` | runtime artifacts only: 3 sqlite files differ (sibling = live daemon + owner activity, dir mtime Sep 7 07:12; refactoring copy = test runs via the session-scoped DB-init fixture — empirically confirmed: a single small pytest run updates `state.sqlite` mtime); logs/ differs by exactly **one** file — a benchmark report on the sibling side from Sep 6 15:22 (owner daemon activity); zero files in the refactoring-side `logs/` were created during this work | — | — |

Supporting session records: `.agent/QUALITY_STATE.md` (per-task evidence, gate progression),
`.agent/DECISIONS.md`, `.agent/LESSONS.md`, `.agent/lo-findings.md` (analysis-phase findings with
file:line references).

---

## 11. Verification commands (reproducible)

```bash
cd infra-local/llama-orchestrator-refactoring
./.venv/bin/python -m ruff check .                                   # → All checks passed!
./.venv/bin/python -m mypy --strict src/llama_orchestrator           # → 0 errors / 72 files
./.venv/bin/python -m pytest -q --no-cov                             # → 746 passed (~161 s)
# R17 naming gate (expect exactly 2 legitimate hits):
grep -rn '\.exe\b' src/llama_orchestrator --include='*.py' | grep -v pycache
```

**Result: implementation complete — all phases done, full gate green.**
