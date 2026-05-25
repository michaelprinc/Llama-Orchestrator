# Implementation Plan: llama-orchestrator GPU/CPU Identification UI

**Date:** 2026-05-25  
**Methodology:** Spec Kit  
**Type:** feature  
**Status:** Draft

---

## 1. Objective

Add a narrow GUI enhancement for `infra-local/llama-orchestrator` that:

- identifies active GPUs as operator-friendly labels such as `Vulkan0`, `Vulkan1`, `Vulkan2` with adapter names when available;
- adds a collapsible, temporarily hideable summary list for the detected GPU inventory;
- shows effective GPU assignment and CPU usage per configuration in new `GPU` and `CPU` table columns without changing the `Runtime args` column or mutating runtime args;
- adds a `Model size` column rendered in `GB` using the same base-1024 unit convention already used for RAM/VRAM normalization;
- documents the detection patterns used for CPU/GPU identification;
- produces a short implementation report after the change is shipped.

## 2. Scope

### In scope

- GUI table column additions and row rendering updates.
- A small shared detection helper for effective CPU/GPU identification.
- Reuse or extraction of existing GPU label and log parsing patterns already present in benchmarking code.
- README updates describing the detection rules and fallback behavior.
- Focused tests for helper logic, GUI rendering helpers, and effective runtime resolution.
- One implementation report under `reports/implementation/infra-local/llama-orchestrator/2026/`.

### Out of scope

- Changing the meaning or contents of the `Runtime args` column.
- Rewriting instance config files just to normalize CPU/GPU flags.
- New third-party packages or vendor-specific SDK dependencies.
- Deep hardware telemetry beyond effective config display and best-effort adapter naming.
- Reworking the benchmark storage schema or adding new persistent GPU inventory state.

## 3. Repo Context

### Relevant files and reuse points

- `infra-local/llama-orchestrator/src/llama_orchestrator/gui.py` - owns Treeview columns, row values, and any new summary/toggle UI.
- `infra-local/llama-orchestrator/src/llama_orchestrator/engine/command.py` - already builds the effective command and environment; this is the correct anchor for read-only CPU/GPU resolution.
- `infra-local/llama-orchestrator/src/llama_orchestrator/benchmark.py` - already contains reusable Windows GPU detection primitives: backend label mapping, Vulkan log parsing, and `GiB/GB -> MB` normalization.
- `infra-local/llama-orchestrator/README.md` - operator-facing documentation for GUI behavior and detection rules.
- `infra-local/llama-orchestrator/tests/test_gui.py` - existing GUI helper tests and column assertions.
- `infra-local/llama-orchestrator/tests/test_engine.py` - current `build_env()` coverage and a good place to validate effective CPU/GPU resolution.
- `infra-local/llama-orchestrator/tests/test_benchmark.py` - existing coverage for Vulkan device log parsing and memory normalization.

### Recommended new file

- `infra-local/llama-orchestrator/src/llama_orchestrator/engine/detection.py` - small shared helper for effective CPU/GPU identification and model-size formatting.

## 4. Assumptions And Clarifications

### Confirmed

- The current GUI shows `backend`, `vram`, and `Runtime args`, but not a dedicated effective `GPU`, `CPU`, or `Model size` column.
- `build_env()` already resolves backend-specific device selection through environment variables such as `GGML_VULKAN_DEVICE` and `CUDA_VISIBLE_DEVICES`.
- Existing benchmark code already recognizes labels such as `Vulkan1` and adapter-name lines in stderr logs.

### Assumptions

- “Temporarily hideable” means session-local hide/collapse behavior only, matching the current session-local column visibility approach.
- The new `GPU` and `CPU` columns should show effective resolved usage, not just raw config fields. That means detection should read from the built command and merged environment so CLI/config overrides are reflected without rewriting args.
- `Model size` should use base-1024 conversion from file size in bytes to `GB`, because current memory normalization treats both `GiB` and `GB` as `* 1024 MB` for display/storage.
- Adapter names are best-effort. When no log-derived or CLI-derived name is available, the UI should still show a stable label such as `Vulkan1`.

### Open edges to handle without blocking planning

- Non-Vulkan backends may have weaker adapter-name coverage than Vulkan on Windows. The plan should prefer graceful fallback over backend-specific complexity.

## 5. Flow Notes

```text
Instance config
  -> build_command(config) + build_env(config)
  -> detection helper resolves:
       - effective CPU threads
       - effective GPU backend/device label
       - optional adapter name
       - model size in GB
  -> GUI refresh renders:
       - new GPU / CPU / Model size columns
       - existing Runtime args text unchanged
       - summary inventory panel with hide/collapse toggle

stderr benchmark/runtime logs
  -> reused Vulkan inventory + "using device" patterns
  -> best-effort adapter-name map for summary list and row labels
```

Key planning constraint: detection is read-only. The implementation should derive display metadata from effective command/env/log context and must not rewrite `config.args` or change command semantics just to make the GUI prettier.

## 6. Proposed Implementation Approach

### P1 Extract shared detection primitives

- Create a small helper module, preferably `src/llama_orchestrator/engine/detection.py`, with narrowly scoped functions such as:
  - effective CPU thread resolution from the final built command;
  - effective GPU backend/device resolution from merged config + environment;
  - best-effort adapter label/name detection from stderr log patterns;
  - model-size formatting in base-1024 `GB`.
- Move or mirror only the minimal reusable logic from `benchmark.py` so the project has one source of truth for:
  - backend label mapping (`Vulkan1`, `CUDA0`, etc.);
  - adapter-name extraction from log lines;
  - `GiB/GB` normalization rules.

### P2 Keep runtime semantics unchanged while exposing effective usage

- Extend `engine/command.py` with read-only helper usage or wrapper functions so GUI code can ask for effective CPU/GPU display values without reconstructing parsing logic itself.
- Treat the built command as authoritative for effective CPU threads when duplicate `--threads` values are present.
- Treat the merged environment as authoritative for backend-specific device overrides when `config.env` supersedes default device selection.
- Preserve existing `build_command()` output and `Runtime args` display exactly as they are.

### P3 Add GUI columns and a hideable summary inventory

- Add `gpu`, `cpu`, and `model_size` columns to the Treeview while keeping `backend` and `args` in place.
- Populate rows with:
  - `GPU`: effective label plus adapter name when known, for example `Vulkan1 - AMD Radeon RX 6800`;
  - `CPU`: effective threads or CPU-only indicator, for example `16 threads`;
  - `Model size`: file size in `GB` using the same unit convention as RAM/VRAM.
- Add a compact summary frame above or beside the table that lists detected adapters and can be collapsed/hidden during the session.
- Keep unknown states readable, for example `Vulkan1`, `CPU`, or `-` when no trustworthy signal exists.

### P4 Document detection patterns and fallback rules

- Update `README.md` with a short section that explains:
  - effective CPU detection comes from the final command, not a guessed raw field;
  - effective GPU detection comes from backend/device env plus best-effort log patterns;
  - adapter names are best-effort and may be unavailable before logs exist;
  - `Model size` uses the same base-1024 convention already used by memory reporting.
- Note explicitly that `Runtime args` are intentionally left unchanged by this feature.

### P5 Verify and report

- Add focused tests for the new helper and targeted updates to GUI/engine/benchmark tests.
- Run narrow pytest and lint checks for the touched slice.
- Write a concise implementation report with changed files, detection patterns used, validation results, and rollback notes.

## 7. Task Breakdown

| ID | Phase | Task | Type | Dependencies | Parallel | Done when |
|----|------|------|------|--------------|----------|-----------|
| P1 | Detection | Add shared CPU/GPU/model-size detection helper and reuse existing Vulkan/log parsing patterns | code/test | - | no | One helper module resolves effective CPU/GPU metadata and model size without duplicating parsing rules |
| P2 | Engine | Expose effective read-only CPU/GPU resolution through command-layer helpers without changing built args | code/test | P1 | no | GUI can obtain authoritative display metadata from command/env state while `build_command()` semantics stay unchanged |
| P3 | GUI | Add `GPU`, `CPU`, and `Model size` columns plus a collapsible/hideable adapter summary list | code/test | P1,P2 | no | GUI renders the new metadata and summary panel while `Runtime args` remains unchanged |
| P4 | Docs | Document detection patterns, unit conventions, and fallback behavior in README | docs | P1,P2,P3 | [P] | README explains how CPU/GPU labels are derived and what happens when names are unavailable |
| P5 | Verify+Report | Run focused checks and write the implementation report | test/report | P1,P2,P3,P4 | [P] | Focused tests pass and a concise report captures evidence, risks, and rollback |

## 8. Dependencies

- Existing local Python toolchain in `infra-local/llama-orchestrator` (`uv`, `pytest`, `ruff`).
- Existing benchmark log parsing already covered in `tests/test_benchmark.py`.
- No new packages or external hardware SDKs.

## 9. Acceptance Criteria

- [ ] The GUI shows a best-effort GPU summary list with labels such as `Vulkan0` / `Vulkan1` and adapter names when detectable.
- [ ] The summary list can be collapsed or temporarily hidden without deleting data or changing persisted config.
- [ ] Each row shows new `GPU` and `CPU` columns based on effective resolved usage per configuration.
- [ ] The `Runtime args` column text and runtime behavior remain unchanged by this feature.
- [ ] Each row shows a `Model size` value in `GB` using the same base-1024 unit convention as current RAM/VRAM normalization.
- [ ] Detection patterns used for CPU/GPU identification are documented in `README.md`, including fallback behavior.
- [ ] Focused tests cover duplicate `--threads`, env-based device overrides, Vulkan adapter-name parsing, and GUI rendering of unknown/fallback states.
- [ ] A short implementation report exists under `reports/implementation/infra-local/llama-orchestrator/2026/`.

## 10. Verification Strategy

### Cheap checks first

- Unit-test effective CPU detection with duplicate `--threads` values to prove the last effective command value is what the GUI displays.
- Unit-test GPU detection with merged env overrides to prove `config.env` can supersede default `config.gpu.device_id` without changing args.
- Reuse log fixtures similar to existing `Vulkan1 : AMD Radeon RX 6800 ...` tests to validate adapter-name extraction cheaply.
- Unit-test GUI helper formatting for known and unknown GPU labels before doing any manual GUI smoke pass.

### Focused commands

- `uv run pytest tests/test_gui.py tests/test_engine.py tests/test_benchmark.py -v --no-cov`
- If a new detection test file is added: `uv run pytest tests/test_detection.py tests/test_gui.py tests/test_engine.py tests/test_benchmark.py -v --no-cov`
- `uv run ruff check src/llama_orchestrator/gui.py src/llama_orchestrator/engine/command.py src/llama_orchestrator/engine/detection.py src/llama_orchestrator/benchmark.py tests/test_gui.py tests/test_engine.py tests/test_benchmark.py`

### Manual smoke validation

- Launch `llama-orch gui` and confirm:
  - the summary list can hide/collapse and re-show;
  - `GPU`, `CPU`, and `Model size` columns render for visible rows;
  - `Runtime args` values are unchanged before vs. after the feature.

## 11. Risks And Rollback

| Risk | Impact | Cheap validation | Mitigation | Rollback |
|------|--------|------------------|------------|----------|
| Effective CPU usage is misreported when `config.args` repeats `--threads` | high | Add a test with multiple `--threads` values and assert the displayed CPU value matches the final command | Derive CPU usage from the built command, not just `config.model.threads` | Revert helper/GUI CPU-column logic and keep existing columns only |
| GPU device label or adapter name is wrong when env overrides the configured device | high | Add a test where `config.env` overrides the default device and assert the resolved GPU column follows merged env | Resolve GPU display from `build_env()` and fallback safely to raw backend/device | Revert env-aware display logic and show backend/device only |
| Adapter names are unavailable for some rows because logs do not contain inventory lines yet | medium | Test both fixture-present and fixture-absent cases | Show stable labels like `Vulkan1` even when adapter names are unknown | Keep label-only rendering and disable adapter-name enrichment |
| Detection logic forks between GUI and benchmark code | medium | Run existing benchmark parsing tests after extraction | Extract shared label/normalization logic instead of copy/paste | Revert extraction and keep changes isolated while preserving current benchmark behavior |
| Summary panel adds clutter on smaller windows | low | Quick manual resize check in the GUI | Make the panel collapsible/hideable and keep hidden state session-local | Remove the panel and keep only the new columns |

## 12. Recommended Smallest Edit Order

1. Add the shared detection helper and its focused tests first, using existing Vulkan/log fixtures as the anchor.
2. Wire `engine/command.py` to expose effective CPU/GPU resolution from final command/env state without changing command construction.
3. Update `gui.py` to add `GPU`, `CPU`, and `Model size` columns and render the new helper output while keeping `Runtime args` untouched.
4. Add the collapsible/hideable summary inventory panel once row-level data is stable.
5. Refresh `README.md`, run focused validation, then write the implementation report.

## 13. Deliverables

- `infra-local/llama-orchestrator/docs/20260525_llama-orchestrator-gpu-cpu-identification-implementation-plan.md`
- `infra-local/llama-orchestrator/docs/20260525_llama-orchestrator-gpu-cpu-identification-implementation-checklist.md`
- Updated code/tests/docs in the files listed above
- `reports/implementation/infra-local/llama-orchestrator/2026/20260525-llama-orchestrator-gpu-cpu-identification-report.md`

## 14. Sources

### Repo sources

- `AGENTS.md`
- `ARCHITECTURE.md`
- `.github/copilot-instructions.md`
- `docs/reference/workspace/speckit-principles.md`
- `docs/templates/speckit-implementation-plan-template.md`
- `docs/templates/speckit-implementation-checklist-template.md`
- `infra-local/llama-orchestrator/src/llama_orchestrator/gui.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/engine/command.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/benchmark.py`
- `infra-local/llama-orchestrator/tests/test_gui.py`
- `infra-local/llama-orchestrator/tests/test_engine.py`
- `infra-local/llama-orchestrator/tests/test_benchmark.py`
- `infra-local/llama-orchestrator/README.md`

### External sources

- None required for this plan. The proposed approach is based on current in-repo detection logic and Windows-specific behavior already exercised by the existing test suite.