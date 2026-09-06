# Llama Orchestrator — Analysis Findings (working notes)

Status: in progress. Findings are tagged [CONFIRMED] when verified against code/tests, or [SUSPECT] when inferred.
Evidence environment: `/tmp/lo-src` (git archive HEAD), venv `/tmp/lo-analysis-venv`; logs at /tmp/lo-{ruff,pytest,mypy}.log.

## Module index read
| Area | Files | Status |
|---|---|---|
| config | schema, loader, migration, validator, __init__, top-level config_validator.py (extract) | __init__ done; schema/loader reviewed prior; config_validator pending |
| engine | state, process, reconciler, detection, locking, command, detach, metadata (extract), logging_config, validator | done |
| health | checker, monitor, probes, ports, backoff, client_pool (extract) | done this span |
| binaries | registry, manager, downloader, github, switching, schema | structure+downloader core done; manager.uninstall/registry cross-check done |
| daemon | service, win_service, _daemon_main, _service_entry | service.py done |
| cli | cli.py (chunked), cli_describe.py, cli_exit_codes.py, __main__.py | binary-remove path verified; full read pending |
| benchmark | benchmark.py, benchmark_grid.py | failure evidence captured; deep read pending |
| runtime_args.py | (781 lines, extract) | done — F15 fully proven with live repros |
| gui | app.py + 20 modules (via HEAD extract) | thread-model skim pending |
| misc | hf_import, live_metrics, memory_fit, model_metadata, diffusion_http_adapter | pending |

## Tool evidence (HEAD copy, Python 3.11.2)
- **RUFF: 1157 errors** (856 auto-fixable). Top: W293×836, UP045×82, I001×59, F401×49, UP037×18, W291×16, F841×13, UP017×11, UP035×10, SIM102×8, E402×8.
- **PYTEST: 20 failed / 588 passed** (18 s). Root causes below.
- **MYPY (strict): 195 errors in 41 files** (73 checked). arg-type×68, type-arg×27, attr-defined×16, assignment×15, unused-ignore×11, call-overload×11, no-untyped-def×10, no-any-return×10. Worst: gui/app.py 32, benchmark.py 25, binaries/downloader.py 18, gui/refresh.py 16, gui/version_browser.py 12, cli.py 10, engine/state.py 7.

## Findings

### CONFIRMED — broken / runtime defects
1. **`binary remove` CLI command crashes (ImportError).** `cli.py:1513` imports `remove_binary` from `binaries/registry`, but no such function exists there (module has get_registry_path, load/save_registry, load_version_metadata; manager classes `BinaryRegistryManager.remove()` registry.py:258, `BinaryManager.uninstall()` manager.py:335). Import executes at command invocation → `llama-orch binary remove <id>` always fails. Body also does `shutil.rmtree(binary.path)` **before** the registry update (cli.py ~1495–1570) — non-atomic; on failure, files gone but registry still lists the binary. mypy attr-defined confirms.
2. **tarfile `filter=` kwarg breaks on declared min-Python.** `binaries/downloader.py:389` (the `extractall` call; :358 is the `def extract_tar_gz` line) does `tf.extractall(dest_dir, filter="data" if hasattr(tarfile,"data_filter") else None)`. On Python 3.11.x `TarFile.extractall` has no `filter` parameter at all → `TypeError` (verified by failing test `test_tls_verification.py::TestHelperFunctions::test_extract_tar_gz`). Passing `None` still raises; the hasattr gate only checks for `tarfile.data_filter`, not for kwarg existence. `requires-python = ">=3.11"` is violated by this code path.
3. **Lock timeout semantics inverted / test flaky.** `engine/locking.py::_is_lock_stale` (lines ~80–113) checks `psutil.pid_exists(pid)` FIRST and treats a dead PID as stale regardless of lock age; `acquire()` then deletes the "stale" lock. Test writes a fresh lock with `pid=os.getpid()+1`; if that PID is dead, acquire succeeds and `test_lock_timeout` fails (verified failure); if it happens to be alive, the test passes — flaky by design. **Corrected precision (repro `/tmp/lo-lock-repro.py`, 2026-09-05):** the age check runs unconditionally after the pid check, so aged locks (>300 s) ARE reclaimed even while the owner is alive — Case B: 7200 s-old lock, live PID → stale=True. Case A (fresh lock, just-killed owner) → stale=True, i.e. immediate no-grace takeover. Suite variance verified: one full run = 20 failed/588 passed, a later run = 19 failed/589 passed; the only delta is `test_lock_timeout`.
4. **~16 tests non-hermetic w.r.t. installed binaries.** `tests/test_detection.py::test_describe_effective_runtime_*` (5), `tests/test_memory_fit.py::test_estimate_instance_memory_*` (11), `tests/test_benchmark.py::test_resolve_benchmark_sampling_device_id_prefers_explicit_main_gpu_arg` (≥1) all hit `FileNotFoundError: No llama-server.exe found in bins/ or legacy bin/` via `get_llama_server_path` ← `build_command`. They pass only on machines where a real binary is installed.
5. **Port-collision handler is a no-op.** `health/monitor.py` `_handle_port_collision` (~line 300s) only logs "Port collision detected ... (port auto-switching not yet implemented in V2 monitor)" and continues; configured port collisions never resolve at runtime. Also imports `load_state/save_state/load_runtime/save_runtime` — several unused (F401 candidates).
6. **checker.py trusts HTTP 200 blindly + string-matched status inference.** `_parse_legacy_health_response`: 200 with non-JSON body → `OK` with `raw_response=None` (no model_loaded check on the legacy path). `_probe_result_to_health_check_result` classifies probe failures by substring matching on lowercased messages ("timeout", "refused", "unreachable") — fragile coupling between probes.py wording and checker semantics.
7. **Port availability checks are TOCTOU + privilege-dependent.** `health/ports.py::check_port_available` binds-then-closes (bind succeeds, then another process can grab the port before llama-server starts). `get_port_owner` catches `psutil.AccessDenied` around the whole loop and returns None silently → on unprivileged Linux, owner detection for all ports fails, `find_free_port`/collision logic degrades to "port busy, owner unknown".
8. **backoff.py union-attr errors (mypy 230–239):** global `_backoff_calculator: BackoffCalculator | None` accessed without None guard at lines 230/234/239 — `reset()`/`next_delay()` can raise AttributeError on a real path if module state is reset.

### CONFIRMED — packaging / tooling defects
9. **Duplicate conflicting dev dependency groups in pyproject.toml.** `[project.optional-dependencies] dev` (pytest>=7.4, ...) AND PEP 735 `[dependency-groups] dev` (pytest>=9.0.2, ...). `pip install -e .[dev]` and `uv sync` resolve different sets; version bounds conflict.
10. **`typer[all]` extra no longer exists** (verified warning: typer 0.27.2 "does not provide the extra 'all'") — pyproject declares `typer[all]>=0.9`; modern typer ships everything in core, extra is gone → install warning on every env build; pinned behavior ambiguous.
11. **requirements.txt (uv-exported) contains `-e .`** as line 3 → `pip install -r requirements.txt` fails with "does not appear to be a Python project" unless run from inside the checkout root. Not pip-compatible by design, undocumented.
12. **Pydantic v2 deprecations:** `binaries/schema.py:89,133` class-based `model_config`/`Config` + deprecated `json_encoders`.
13. **CI would be red:** ruff 1157 / mypy-strict 195 / pytest 20-fail on the declared minimum Python (3.11) with no binaries installed.

14. **`config_validator.py` is orphaned dead code.** 374 lines; grep across `src`+`tests` finds ZERO references to the module or its `ConfigValidator` class. It duplicates `config/validator.py` with a different `ValidationResult` shape, a bind-based port check (same TOCTOU/privilege issues as ports.py), and `_check_args_conflicts` that returns on the first conflict instead of collecting all (line 320-327: `for conflict in conflicts: return [...]`).
15. **`runtime_args.py` parser is broken; its only live consumer is therefore dead.** 781 lines, NO dedicated test file (only indirect refs from test_benchmark/test_gui). Proven with venv interpreter:
    - `KNOWN_ARGS` holds 404 flags, **277 carry the pathological recursive `--sorta-kv-head-id-slot-id-…` pattern** (264 with nested repetition; longest entry 1108 chars, `slot-id` ×135); real llama-server flags MISSING: `--threads --device --fit --jinja --cache-type-k/v --temp --top-p --no-warmup --metrics --prompt --model --lora --embedding --cont-batching --chunk-size`.
    - Boolean heuristic line 611: `token == "--" + token[2:].replace("-", "_")` is **always True for any dash-less flag** (`--parallel` → `"--parallel" == "--parallel"`), so all single-word flags are misclassified as booleans and their values are swallowed as positionals. Proven: `parse_args_list(["--parallel","2"])` → `named={} flags={'--parallel'} positional=['2']`.
    - Unknown named/flags are demoted to `extra`, so `parsed.get("--device")` can never return a value. **Proven:** config with `gpu.backend=vulkan, device_id=0, args=["--device","CUDA1"]` → `validate_runtime_arg_alignment()` returns 0 issues and `lint_config()` reports only the model-file error (the deliberately wrong backend prefix passes silently). `find_conflicts(cfg, ["--device","CUDA1","--parallel","2"])` → `[]`.
    - Consequence: `config/validator.py::validate_runtime_arg_alignment` (live, called by `lint_config`) is 100% dead — the advertised "runtime arg alignment" validation never fires for any config.
16. **`validate_log_directory` has write side effects during validation** (`config/validator.py:200-240`): `mkdir(parents=True)` + touch/unlink of `.write_test` on every lint run — a "read-only" validate mutates the filesystem; also builds `logs_dir.parent / stdout_path` from `config.get_log_paths()` (double-prefix suspicion, unverified).
17. **Port-in-use is only a warning** (`config/validator.py:166`, severity="warning") → `ValidationResult.is_valid` stays True for a config whose port is occupied; `llama-orch validate` reports VALID while start would fail with EADDRINUSE.
18. **conftest mocks ALL of tkinter with bare `MagicMock`** (`tests/conftest.py`, 26 lines): GUI tests cannot catch real widget/attribute errors (misspelled tk methods return MagicMock), which is why `gui/app.py` carries 32 mypy errors while GUI tests pass. GUI thread model itself is sound: worker threads post to `queue.Queue`, drained by `_schedule_message_pump` via `self.after(250, ...)`; single benchmark-job lock guard (app.py:1860-1900, 1412-1452).

### SUSPECT — design concerns (need plan-level treatment, not necessarily bugs)
- `config/loader.py` module-level mutable `_CONFIG_CACHE` shared across threads (GUI + monitor thread) without a lock; save/load backfill-on-read "repair" pattern mutates disk configs during reads.
- `engine/state.py::init_db()` runs as an import side effect (first import creates the SQLite file).
- ~~`config/schema.py` GPU validator no-op~~ → **CONFIRMED (F19):** `GpuConfig.validate_gpu_config` (schema.py:687–693) is an empty pass-through (`if backend=="cpu" and layers>0: pass`).
- Reconciler operates on **runtime table only** (`load_runtime`) while process.py writes dual state+runtime tables → divergence source if state table is the "desired" record.
- `engine/metadata.py` (extract) `MetadataCache.is_stale()` iterates only currently-passed configs → removed instances never invalidate; cache keyed by MD5 of model_dump.
- Hardcoded `.exe` suffixes in `engine/command.py:46,144` (`llama-diffusion-gemma-visual-server.exe`) and user-facing messages (lines 150/155) → platform assumption despite cross-platform claims; switching.py error message also says "has no llama-server.exe".
- `build_command` appends raw `config.args` last → user args silently override built-in flags (e.g. `--port`) with no warning; `-fit off` only when `disable_fit`.
- `daemon/service.py`: graceful-stop escalation SIGTERM→SIGKILL after 2 s; `taskkill /F check=True` on Windows can raise if pid already gone (check=True on forced kill) — minor.
- `switching.py::resolve_binary_selector` does linear prefix match over all installed binaries per call; fine at small N, but ambiguity error path requires exact UUID — OK.
- GUI thread model in `gui/app.py` (3025 lines, worst mypy file with 32 errors) — skim pending.

## Open questions
- (all resolved) config_validator.py = orphaned dead code [F14]; runtime_args parser broken [F15]; conftest tkinter mocking [F18]; GUI thread model sound; `diffusion_http_adapter` confirmed at `src/llama_orchestrator/diffusion_http_adapter.py`.
