# Llama Orchestrator — Spec-Driven Implementation Plan

**Date:** 2026-09-05 (UTC)
**Project:** `infra-local/llama-orchestrator` v2.1.0 (git submodule, HEAD `b51ab3c`)
**Basis:** Read-only deep analysis; all evidence gathered on a pristine HEAD copy (`/tmp/lo-src`) with Python 3.11.2 (the declared minimum). No files in the live tree were altered by this analysis.
**Companion artifacts:** `.agent/lo-findings.md` (full defect ledger with file:line evidence), `.agent/QUALITY_STATE.md` (state checkpoint), `tmp/lo-analysis-extract/` (HEAD blobs for the 22 unreadable-permission files).

---

## 1. Executive summary

The project is a well-structured local llama.cpp orchestrator (per-instance JSON configs → spawned servers → health monitoring with restart/backoff → binary registry → benchmarks → tkinter GUI, optional daemon/service), but its current quality gate would be **red on every check** at the declared minimum Python version:

| Gate | Result (HEAD, py3.11.2) | Verdict |
|---|---|---|
| `ruff check` | **1157 errors** (856 auto-fixable) | red |
| `pytest tests` | **20 failed / 588 passed** | red |
| `mypy --strict` | **195 errors in 41 of 73 files** | red |

Beyond the gate, the analysis confirmed **19 defects** (Section 3), including two user-facing command paths that crash outright (`binary remove`, tar extraction on Python 3.11.x) and one case where an advertised validation feature (`--device`/backend alignment in `lint_config`) is **100% dead code** because the argument parser it relies on misclassifies nearly every real llama-server flag (Section 3, F15).

This plan converts each defect into a testable requirement and sequences the work into six phases that restore a green gate first, then fix correctness, then harden, then clean up.

### Non-goals
- No redesign of the V2 engine architecture or the GUI; changes are surgical.
- No Windows service/daemon behavioral change beyond listed fixes (the daemon/service itself must not be restarted by this work).
- This document is a plan only — it changes nothing in the tree.

---

## 2. Evidence base (verified)

Reproducible evidence environment: `python3 -m venv` (3.11.2) + explicit dep installs; source = `git archive HEAD | tar -x`; logs kept at `/tmp/lo-{ruff,pytest,mypy}.log`. Re-run commands are recorded in `.agent/lo-findings.md`.

- **Ruff** top rules: W293×836 (blank-line whitespace), UP045×82 (`Optional`→`X | None`), I001×59 (imports), F401×49 (unused imports), UP037×18, W291×16, F841×13, UP017×11, UP035×10, SIM102×8, E402×8.
- **Pytest** failures (all root-caused): 5 `test_detection.py::test_describe_effective_runtime_*`, 11 `test_memory_fit.py::test_estimate_instance_memory_*`, ≥1 `test_benchmark.py` device test → `FileNotFoundError: No llama-server.exe found in bins/` via `build_command → get_llama_server_path` (tests assume a real binary is installed); 1 `test_tls_verification.py::test_extract_tar_gz` → `TypeError: TarFile.extractall() got an unexpected keyword argument 'filter'`; 1 `test_v2_locking.py::test_lock_timeout` → stale-lock logic treats any dead PID as stale regardless of lock age.
- **Mypy** taxonomy: arg-type×68, type-arg×27, attr-defined×16, assignment×15, unused-ignore×11, call-overload×11, no-untyped-def×10, no-any-return×10; worst files `gui/app.py` (32), `benchmark.py` (25), `binaries/downloader.py` (18), `gui/refresh.py` (16).

---

## 3. Defect catalog

Severity: **S1** = crash/wrong behavior on a supported path, **S2** = correctness hole with real blast radius, **S3** = robustness/maintainability defect. "Confirmed" = reproduced or directly proven against HEAD source; "Suspect" = high-confidence inference needing a focused check during implementation.

| ID | Sev | Defect | Evidence (file:line) |
|---|---|---|---|
| F1 | S1 | `llama-orch binary remove <id>` crashes with ImportError: `cli.py:1513` imports `remove_binary` from `binaries/registry`, which does not exist (module exposes only registry path/load/save functions; the real operations are `BinaryRegistryManager.remove()` `registry.py:258` and `BinaryManager.uninstall()` `manager.py:335`). Command body also `shutil.rmtree(binary.path)` **before** the registry update → non-atomic, partial state on failure. | cli.py:1495–1570, 1513; mypy attr-defined |
| F2 | S1 | tar extraction breaks on the declared minimum Python: `tf.extractall(dest_dir, filter="data" if hasattr(tarfile,"data_filter") else None)` — on 3.11.x `extractall` has no `filter` parameter at all; passing `None` still raises `TypeError`. Reproduced by failing test. | binaries/downloader.py:389 (the `extractall` call; `def extract_tar_gz` at :358); requires-python >=3.11 |
| F3 | S2 | Lock staleness is a bare OR with no grace period: stale = **owner PID dead** (any age) OR **age > 300 s** (any owner liveness). Proven both ways (`/tmp/lo-lock-repro.py`): Case A — fresh lock owned by a just-killed PID → stale=True (immediate lock takeover, no grace); Case B — 7200 s-old lock held by a **live** process → stale=True (lock theft during long operations). `test_v2_locking::TestInstanceLockManager::test_lock_timeout` writes a fresh lock with `pid=os.getpid()+1` and expects `LockTimeoutError` — it passes only when that PID happens to be alive. Suite variance verified: 20 failed/588 passed in one run, 19/589 in the next; the delta is exactly this test. | engine/locking.py:80–113 (`_is_lock_stale`), :130+ (`acquire`) |
| F4 | S2 | ~16 tests are non-hermetic: they call `build_command`/memory-fit paths that invoke `get_llama_server_path`, which requires a real binary in `bins/` and raises otherwise. Suite result depends on what is installed on the machine. | tests/test_detection.py, test_memory_fit.py, test_benchmark.py; config/loader.py `get_llama_server_path` |
| F5 | S2 | Port-collision handling is a no-op: monitor logs "port auto-switching not yet implemented in V2 monitor" and continues — configured collisions never resolve at runtime. Also carries unused state-table imports (F401 candidates). | health/monitor.py `_handle_port_collision` (~300s) |
| F6 | S3 | Health status inference is fragile: legacy path returns OK for any HTTP 200 with non-JSON body (no `model_loaded` check); probe-failure classification substring-matches lowercased messages ("timeout", "refused", …) coupling probes.py wording to checker semantics. | health/checker.py:78–104, `_parse_legacy_health_response` |
| F7 | S3 | Port checks are TOCTOU (bind-then-close in `check_port_available`) and privilege-dependent: `get_port_owner` catches `psutil.AccessDenied` around the whole scan and returns None silently → on unprivileged Linux all owner lookups fail; collision/`find_free_port` logic degrades without a log. | health/ports.py:38–90, 93–127 |
| F8 | S3 | Backoff module: global `_backoff_calculator: BackoffCalculator | None` accessed without None-guards at lines 230/234/239 (mypy union-attr) — `reset()`/`next_delay()` can raise AttributeError after any state reset path. | health/backoff.py:27, 208, 230–239 |
| F9 | S2 | `pyproject.toml` declares **two conflicting dev dependency sets**: `[project.optional-dependencies].dev` (pytest>=7.4) and PEP-735 `[dependency-groups].dev` (pytest>=9.0.2). `pip install -e .[dev]` vs `uv sync` resolve different environments; version bounds conflict. | pyproject.toml dev groups |
| F10 | S3 | `typer[all]>=0.9` — the `all` extra no longer exists in modern typer (verified warning on 0.27.2); every env build warns, and pinned behavior is ambiguous. | pyproject.toml; pip install warning |
| F11 | S3 | uv-exported `requirements.txt` contains `-e .` → `pip install -r requirements.txt` fails outside the checkout root ("does not appear to be a Python project"). Undocumented footgun. | requirements.txt:3 |
| F12 | S3 | Pydantic v2 deprecations: class-based config + `json_encoders` in binaries schema (emits deprecation warnings on every import). | binaries/schema.py:89, 133 |
| F13 | S2 | CI would be red: ruff 1157 / mypy-strict 195 / pytest 20-fail at the declared minimum Python — no gate is currently enforcing any of this. | Section 2 evidence |
| F14 | S3 | `src/llama_orchestrator/config_validator.py` (374 lines) is **orphaned dead code**: zero references in `src` or `tests`; duplicates `config/validator.py` with an incompatible result shape; its `_check_args_conflicts` returns on the first conflict instead of collecting all. | grep over src+tests (0 hits); module header "foundation for Phase 3 of the GUI redesign" |
| F15 | S1 | **The runtime-arg parser is broken, and its only live consumer is therefore dead.** `runtime_args.py` (781 lines, no dedicated test file): (a) `KNOWN_ARGS` has 404 entries, 277 of them carrying the pathological recursive `--sorta-kv-head-id-slot-id-…` pattern (264 with nested repetition; longest entry is 1108 chars repeating `slot-id` 135×), while real llama-server flags are missing (`--threads --device --fit --jinja --cache-type-k/v --temp --top-p --no-warmup --metrics --prompt --model --lora --embedding --cont-batching --chunk-size`); (b) the boolean heuristic at line 611, `token == "--" + token[2:].replace("-", "_")`, is **always true for dash-less flags** (`--parallel` → `"--parallel"`), so single-word flags are classified as booleans and their values become positionals — proven: `parse_args_list(["--parallel","2"])` → `named={} flags={'--parallel'} positional=['2']`; (c) unknown named/flags are demoted to `extra`, so `.get("--device")` can never return a value. **Proven consequence:** with `gpu.backend=vulkan, device_id=0, args=["--device","CUDA1"]`, `validate_runtime_arg_alignment()` returns 0 issues and `lint_config()` reports only the model-file error — the deliberately wrong backend prefix passes silently; `find_conflicts` likewise returns `[]`. | runtime_args.py:226–560 (catalog), 611, 636–648; config/validator.py:297–334; live repro in venv |
| F16 | S3 | "Read-only" validation mutates the filesystem: `validate_log_directory` does `mkdir(parents=True)` + touch/unlink of `.write_test` on every lint run, and builds paths as `logs_dir.parent / <log path>` (double-prefix suspicion). | config/validator.py:200–240 |
| F17 | S3 | Port-in-use is severity **warning** → `ValidationResult.is_valid` stays True while the port is occupied; `llama-orch validate` reports VALID for a config whose start would fail with EADDRINUSE. | config/validator.py:155–171 |
| F18 | S3 | Test isolation is over-mocked: `conftest.py` replaces **all** of tkinter with bare `MagicMock`s, so GUI tests cannot catch real widget/attribute errors — consistent with `gui/app.py` carrying 32 mypy errors while passing. (The GUI thread model itself is sound: worker threads → `queue.Queue` → `after(250ms)` pump.) | tests/conftest.py; gui/app.py:776, 1412–1452, 1899 |
| F19 | S3 | `GpuConfig.validate_gpu_config` is an empty pass-through (the one case it documents — cpu backend with layers>0 — does nothing). | config/schema.py:687–693 |

**Suspect design concerns** (treat in the relevant phase; confirm during implementation):
- D1 `config/loader.py` module-level mutable `_CONFIG_CACHE` shared across GUI + monitor threads without a lock; save/load backfill-on-read "repair" pattern mutates disk configs during reads.
- D2 `engine/state.py::init_db()` runs as an import side effect (first import creates the SQLite file).
- D3 Reconciler operates on the **runtime table only** while process writes dual state+runtime tables → divergence source if state is the desired record.
- D4 `MetadataCache.is_stale()` iterates only currently-passed configs → removed instances never invalidate the cache.
- D5 Hardcoded `.exe` suffixes in `engine/command.py:46,144` (diffusion-gemma runner) and user-facing messages (`command.py:150,155`, `binaries/switching.py` "has no llama-server.exe") — platform assumption despite cross-platform claims.
- D6 `build_command` appends raw `config.args` last → user args silently override built-ins (e.g. `--port`) with no warning; `-fit off` only when `disable_fit`.
- D7 `daemon/service.py`: Windows `taskkill /F check=True` on forced kill can raise if the PID is already gone; 2 s SIGTERM grace then SIGKILL.
- D8 `binaries/downloader.py:139` builds an untyped kwargs dict splatted into `httpx.Client` (source of 14 mypy arg-type errors); also int/float reassignment at :526.

---

## 4. Requirements

Each requirement is testable; acceptance criteria are written so they can be executed as checks. `[defect]` links back to Section 3.

### A. Packaging & toolchain (gate must go green)
- **R1** `[F9]` Exactly one dev dependency set exists: PEP 735 `[dependency-groups] dev` is the single source of truth; `[project.optional-dependencies].dev` is removed (or kept as a thin alias with identical pins — decision recorded in the PR). *AC:* `pip install -e .[dev]` and `uv sync` produce environments whose pytest major versions agree; no conflicting pin remains.
- **R2** `[F10]` Typer dependency declares no nonexistent extra. *AC:* fresh venv install emits zero "extra not provided" warnings.
- **R3** `[F11]` `requirements.txt` is either regenerated without `-e .` (or documented as uv-only with a README note and a CI check). *AC:* `pip install -r requirements.txt` succeeds from an arbitrary cwd in a clean venv, or the file carries a header comment + lint forbidding `-e` lines.
- **R4** `[F13]` A single CI workflow runs `ruff check`, `mypy --strict src/llama_orchestrator`, and `pytest -q` on Python 3.11 (minimum) with no binaries installed, and the workflow is green at merge time of Phase 0–2. *AC:* workflow file exists; run log shows all three green in an environment without `bins/`.

### B. Correctness (user-visible defects)
- **R5** `[F1]` `llama-orch binary remove <id>` works and is atomic: it delegates to `BinaryManager.uninstall()` (or `BinaryRegistryManager.remove()`), removes files only after the registry entry is updated-and-persisted, and fails with a clear error if the binary is in use by any instance. *AC:* new CLI test runs `binary remove` against a fixture registry + fake package dir: success path leaves no registry entry and no directory; failure path (in-use) leaves both intact; importing the command module raises nothing.
- **R6** `[F2]` tar extraction works on every Python in `requires-python`. *AC:* `extract_tar_gz` is implemented with a version gate (`if sys.version_info >= (3, 12): kwargs["filter"]="data"`), and `test_extract_tar_gz` passes on 3.11 without any conditional skip.
- **R7** `[F3]` Lock acquisition semantics: a lock is stale iff (age > max_age) OR (owner PID dead AND age > grace); fresh locks are never deleted; timeout still raises `LockTimeoutError`. *AC:* deterministic unit tests (no reliance on live PIDs — inject the pid-exists oracle): fresh foreign lock → timeout raised; dead-PID lock older than grace → acquired; alive-PID lock older than max_age → reclaimed. `test_lock_timeout` passes on repeated runs (≥20).
- **R8** `[F4]` The test suite is hermetic: no test depends on an installed binary, host GPU, or network. *AC:* full `pytest -q` in a clean container without `bins/`, GPU drivers, or internet → 0 failures; the ~16 previously failing tests are fixed by injecting a fake server path (fixture/monkeypatch of `get_llama_server_path`) rather than by weakening assertions.
- **R9** `[F15]` `parse_args_list` correctly classifies llama-server arguments and the alignment validator actually runs: real flag catalog (no pathological variants; all flags used anywhere in configs/build_command included), correct boolean set, unknown flags preserved **and still queryable**, no value-swallowing. *AC:* dedicated `tests/test_runtime_args.py` added (≥25 cases incl. `--parallel 2`, `--device Vulkan0`, `--jinja`, `--flag=value`, duplicates, trailing flag); `lint_config` on the proven repro (`vulkan` backend + `--device CUDA1`) returns a non-empty error issue; `find_conflicts` returns both device and parallel conflicts for that input.
- **R10** `[F17]` Occupied port is an **error**, not a warning, in validation results. *AC:* `validate_instance` on a config whose port is bound by a test fixture socket yields `is_valid == False`.
- **R11** `[F5]` Runtime port-collision policy is defined and implemented (or explicitly disabled with a loud CLI/GUI notice): minimum viable spec = monitor detects collision, marks instance ERROR, emits an event, and applies the configured restart policy; automatic re-porting remains opt-in. *AC:* unit test simulating two instances on one port: second instance's state transitions to ERROR + event logged; no "not yet implemented" log path remains for the default flow.
- **R12** `[F6]` Health status mapping is explicit and tested per probe type: legacy 200-without-JSON is classified LOADING (or UNKNOWN) rather than OK; probe-failure classification uses structured fields (status code, error kind) instead of message substrings. *AC:* table-driven tests cover (503, timeout, refused, bad-body-200, expected_body mismatch) per probe type; no string-sniffing branch remains in `checker.py`.

### C. Robustness
- **R13** `[F7]` Port utilities degrade loudly: owner lookups that hit `AccessDenied` log a one-time warning and report `owner=None, degraded=True`; `check_port_available` documents its TOCTOU window and the start path uses bind-at-spawn or `SO_REUSEADDR`-aware retry instead of pre-checks. *AC:* tests assert the degraded flag propagates into `PortInfo`; start path does not race on a just-freed port (retry loop covered by test).
- **R14** `[F8]` Backoff globals are None-safe: single accessor (`_calc() -> BackoffCalculator`) that lazily creates/repairs state. *AC:* mypy union-attr errors gone; unit test resets module state and calls `next_delay()` without AttributeError.
- **R15** `[D2, D3, D4]` State-engine consistency: (a) `init_db()` is not an import side effect (explicit call from CLI/GUI/daemon entrypoints, guarded idempotent); (b) reconciler and process agree on which table(s) are authoritative — documented in `docs/` and enforced by a test asserting both tables are written/compared on start/stop; (c) `MetadataCache.is_stale()` invalidates on removed instance names. *AC:* importing `engine.state` in a clean env creates no SQLite file; reconciliation test covers the runtime-only divergence case; metadata-cache test covers instance removal.
- **R16** `[D1]` Config cache is thread-safe: single lock around read/write, or per-thread cache; backfill-on-read repair becomes an explicit `repair_config()` called from write paths only. *AC:* concurrent load/save stress test (threads) with no lost updates or disk mutations during pure reads.
- **R17** `[D5]` No hardcoded `.exe` in runtime code paths: binary names derived per-platform (`shutil.which`-style resolution); user-facing messages use the resolved name. *AC:* grep gate `rg '\.exe' src/` returns hits only in Windows-only branches/tests; start path covered by a POSIX test with an extensionless binary.
- **R18** `[D6]` Explicit override policy: when raw `config.args` overrides a built-in flag, the effective command is logged at INFO and `describe` surfaces the override; no silent behavior. *AC:* describe output for a config overriding `--port` shows both values; test asserts log line emitted.
- **R19** `[F16]` Validation is side-effect-free: log-directory check uses `os.access`/`tempfile` in an existing dir without creating dirs or probe files on the validated path (probe file only in a temp dir when creation must be tested). *AC:* lint run on a read-only checkout passes/ fails identically and creates zero new files under the project.
- **R20** `[F19]` `GpuConfig.validate_gpu_config` either enforces its documented rule (cpu+layers>0 → schema-level warning channel) or is deleted in favor of the existing `config/validator.py` check that already covers it. *AC:* no duplicated GPU validation logic remains; test asserts exactly one issue raised for cpu+layers>0.

### D. Hygiene & maintainability
- **R21** `[F14]` Dead code removed: delete `src/llama_orchestrator/config_validator.py` (and any importers, of which there are none). *AC:* file gone; test suite green; no references remain.
- **R22** `[F13, ruff]` Ruff debt cleared in two commits: (1) auto-fixable (`ruff --fix`) with review of the 856 changes; (2) manual fixes for F401/F841/SIM102/E402. *AC:* `ruff check` → 0 errors, no per-file ignores added.
- **R23** `[F13, D8, mypy]` Mypy strict debt cleared by file-priority batches (gui/app.py → benchmark.py → downloader.py → gui/refresh.py → cli.py → engine/state.py → rest), with `# type: ignore` only where annotated with reason; untyped kwargs splats (downloader.py:139, the D8 source of 14 arg-type errors) rewritten as explicit typed arguments. *AC:* `mypy --strict src/llama_orchestrator` → 0 errors.
- **R24** `[F12]` Pydantic v2 modernization in binaries schema: `model_config = ConfigDict(...)` / `ConfigDict`, replace `json_encoders` with field serializers. *AC:* no deprecation warnings on import; model round-trip tests unchanged and green.
- **R25** `[F18]` GUI test strategy: keep tkinter mocks for unit tests but add a narrow integration smoke (real Tk if display available, else explicit skip) and route new GUI code through the existing queue-pump contract with typed messages; mypy batch R23 must fix app.py's 32 errors without weakening assertions. *AC:* `gui/app.py` mypy-clean; at least one non-mocked GUI smoke exists (CI may skip headless, locally it runs).
- **R26** `[D7]` Daemon stop path hardened: `taskkill /F check=False` + post-check (or `check=True` only when process confirmed alive); SIGKILL fallback already present. *AC:* unit test with a fake already-dead PID returns True without raising.

---

## 5. Design decisions

1. **Single dependency mechanism.** Keep PEP 735 `[dependency-groups]` (uv-native, matches existing `requirements.txt` generation) and drop the `optional-dependencies.dev` alias. Rationale: uv is already the export tool; two sources of truth is exactly what caused F9.
2. **Atomic registry mutation order.** For R5, persist-then-delete (write registry without the entry + fsync, then rmtree). Rationale: leaving files behind is recoverable (`binary list` still correct); leaving a registry entry pointing at nothing is not. In-use check before both steps via existing `BinaryInUseError`.
3. **Lock staleness = age OR dead-owner-with-grace.** Keep PID liveness as a *signal*, never a sole condition (F3). Default grace 5 s, max_age configurable; pid oracle injectable for tests. Rationale: makes behavior deterministic and testable without live PIDs.
4. **Argument parser rebuild over patching.** The catalog is machine-generated garbage (277 of 404 entries carry the recursive `--sorta-kv-head-id-slot-id-…` pattern) — regenerate `KNOWN_ARGS`/`BOOLEAN_FLAGS` from the actual llama-server flag surface used by this project plus a curated real-flag list; make unknown flags first-class queryable (keep them in `named` with an `unknown=True` marker rather than demoting to `extra`). Rationale: patching heuristics around a wrong catalog cannot fix F15.
5. **Validation severity policy.** Resource conflicts that block start (occupied port, missing model) = error; style/performance concerns = warning. Documented in `docs/` and enforced by the new tests (R10).
6. **CI minimum-Python pin.** Gate runs on 3.11 only at first (the declared floor); add 3.12 job once F2's version gate is in, to prove both branches of it.
7. **No daemon restarts during this work.** All fixes are code-level; the running orchestrator session is untouched until the operator chooses to redeploy (per task constraint).

---

## 6. Implementation plan (phases & tasks)

Ordering principle: Phase 0 unblocks verification; Phase 1 removes crashes/dead validation; Phase 2 makes the suite trustworthy; Phases 3–5 harden and clean. Every task lists its requirements and a verification command. Effort = engineer-days (E).

### Phase 0 — Gate & packaging (unblock everything)
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T0.1 | R1 | Remove duplicate dev group; align pins (pytest 9.x floor) | 0.5 | `pip install -e .[dev]` + `uv sync` in two clean venvs; compare pytest versions |
| T0.2 | R2 | Drop `[all]` extra from typer pin | 0.1 | fresh venv install → zero warnings |
| T0.3 | R3 | Regenerate/annotate requirements.txt | 0.2 | `pip install -r` from clean cwd succeeds (or header+lint in place) |
| T0.4 | R4, R22(1) | Add CI workflow (ruff/mypy/pytest @ py3.11, no binaries); land ruff auto-fix commit with review of 856 fixes; allowlist baseline for remaining mypy/ruff debt as a tracked `mypy-baseline.txt` until Phases 1–3 clear it | 1.0 | CI green except tracked baseline diff = 0 new findings |

### Phase 1 — Correctness (S1/S2 defects)
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T1.1 | R5 | Fix `binary remove`: delegate to `BinaryManager.uninstall()`, persist-then-delete, in-use guard; delete the bogus import | 0.5 | new CLI fixture test (success/failure/in-use); `llama-orch binary remove` smoke on scratch project |
| T1.2 | R6 | Version-gated tarfile filter in `downloader.py`; keep 3.12 security path | 0.2 | `test_extract_tar_gz` passes unskipped on 3.11; 3.12 CI job passes |
| T1.3 | R7 | Rewrite `_is_lock_stale` + injectable pid oracle; deterministic tests (≥20-run flake check) | 0.5 | `test_v2_locking` green ×20; new stale/alive/dead matrix tests |
| T1.4 | R8 | Hermeticity: monkeypatch `get_llama_server_path` in the ~16 affected tests via a shared fixture (no binary on disk); audit for other environment assumptions | 1.0 | clean-container pytest → 0 failures; rerun with/without fake `bins/` → identical results |
| T1.5 | R9 | Rebuild `runtime_args` catalog + parser (Decision 4); add `tests/test_runtime_args.py` covering unknown `--flag=value` round-trip consistency (`assignments` vs `named`/`extra`); prove alignment validator fires on the repro config | 2.0 | new test file green; repro returns non-empty lint error; full suite green |
| T1.6 | R10 | Port-in-use → error severity + tests | 0.2 | fixture-socket test asserts `is_valid == False` |

### Phase 2 — Trustworthy suite & health semantics
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T2.1 | R12 | Structured probe→status mapping (drop substring sniffing); legacy 200-without-JSON → LOADING; table-driven tests per probe type | 1.0 | checker/probes tests green; grep gate: no `in message_lower` branches left in checker.py |
| T2.2 | R11 | Implement collision policy (ERROR state + event + restart policy; opt-in re-port); remove "not yet implemented" path | 1.5 | two-instance-same-port simulation test; describe output shows the event |
| T2.3 | R18 | Override surfacing in `describe`/logs when raw args shadow built-ins | 0.5 | describe test with `--port` override |

### Phase 3 — Engine robustness
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T3.1 | R13 | Ports: degraded flag + loud logging; start-path bind/retry instead of pre-check TOCTOU | 0.5 | PortInfo degradation test; freed-port retry test |
| T3.2 | R14 | Backoff None-safe accessor | 0.2 | mypy union-attr gone; reset-state test |
| T3.3 | R15 | Remove import-time `init_db`; reconcile table-authority (docs + dual-table test); metadata cache invalidation on removal | 1.0 | clean-import creates no DB file; divergence + removal tests green |
| T3.4 | R16 | Config cache locking; move backfill repair to write paths; concurrent stress test | 0.5 | thread-stress test; no disk writes from pure reads (assert mtime unchanged) |

### Phase 4 — Hygiene sweep
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T4.1 | R21 | Delete orphaned `config_validator.py` | 0.1 | grep zero refs; suite green |
| T4.2 | R22(2) | Manual ruff fixes (F401/F841/SIM102/E402) → ruff fully clean, no ignores | 1.0 | `ruff check` = 0 |
| T4.3 | R23 | Mypy batches by file priority (app.py first); typed kwargs in downloader; remove baseline file at end | 3.0 | `mypy --strict` = 0, baseline deleted |
| T4.4 | R24 | Pydantic modernization in binaries schema | 0.5 | no deprecation warnings; round-trip tests green |
| T4.5 | R17 | Platform-neutral binary naming + messages | 0.5 | grep gate; POSIX start test with extensionless binary |
| T4.6 | R26 | Daemon stop hardening (taskkill check=False + confirm) | 0.2 | dead-PID unit test returns True |

### Phase 5 — Final verification
| Task | Req | Description | Effort | Verify |
|---|---|---|---|---|
| T5.1 | R4, all | Full gate: ruff 0 / mypy-strict 0 / pytest green in clean py3.11 container (no bins/GPU/net) + 3.12 job; run GUI smoke locally | 1.0 | archived logs attached to the closing PR |
| T5.2 | R20, R19 | Close remaining schema/validator items (GPU validator dedupe; side-effect-free validation test) | 0.5 | targeted tests green; read-only-checkout lint creates zero files |

**Total estimate: ≈ 16 E**, sequenced so Phase 0+1 (≈ 4.5 E) eliminate every crash and the dead-validation defect.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Ruff auto-fix commit touches 856 sites; hidden behavior change in `--`-adjacent string edits | Two-commit split (auto vs manual); full pytest before/after each; diff review focused on non-whitespace hunks |
| Argument-parser rebuild (T1.5) changes what `config.args` means for existing instance configs | Repro suite includes every real config in the repo (`instances/*.json`) parsed old-vs-new with a diff report before merge; unknown flags remain queryable, so no silent drops |
| Hermeticity fixture (T1.4) masks a real deployment bug (missing binary should fail loudly at start) | Fixture is test-scoped only; add one *positive* test asserting `get_llama_server_path` still raises in production paths when bins/ is absent |
| Collision policy change (T2.2) could surprise operators who rely on current no-op behavior | Default = ERROR+restart-policy only; re-porting opt-in via explicit config key, documented as new behavior in CHANGELOG |
| 22 files have restrictive permissions (0600/uid-10000) in this checkout | Implementation work happens where the repo is normally readable; analysis already proved those blobs equal HEAD (`git status` clean), so no drift risk was introduced by reading extracts |

---

## 8. Definition of done

1. All requirements R1–R26 have at least one passing acceptance-criteria test (tracked in the PR checklist).
2. `ruff check`, `mypy --strict src/llama_orchestrator`, and `pytest -q` all green on Python 3.11 in a clean environment without binaries, GPU, or network; 3.12 job green after T1.2.
3. The two previously-crashing paths (`binary remove`, tar extraction on 3.11) have regression tests that would fail against HEAD.
4. `lint_config` demonstrably catches the backend/`--device` mismatch repro (Section 3, F15).
5. No existing files were modified outside this plan's tasks; the running orchestrator daemon was not restarted or touched at any point.
