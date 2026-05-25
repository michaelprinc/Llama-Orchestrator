# Checklist: llama-orchestrator GPU/CPU Identification UI

**Start date:** 2026-05-25  
**Methodology:** Spec Kit  
**Status:** Complete

---

## Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Done
- `[!]` Blocked
- `[-]` Out of scope

---

## Phase P1 - Shared Detection Helper

- [x] P1 Create a shared helper module for effective CPU/GPU/model-size detection
- [x] P1 Reuse or extract existing backend-label, adapter-name, and `GiB/GB` normalization logic from current benchmark parsing
- [x] P1 Add focused tests for duplicate `--threads`, env-based device overrides, and Vulkan adapter-name parsing

**Acceptance checks**

- [x] Effective CPU threads are derived from the final command, not guessed from a single raw field
- [x] Effective GPU label/device resolution follows merged environment overrides when present
- [x] Adapter-name parsing works with existing Vulkan-style log fixtures and falls back cleanly when unavailable

**Notes**

- Prefer a small helper under `src/llama_orchestrator/engine/` so GUI and command logic share one source of truth.

---

## Phase P2 - Engine Integration Without Runtime Changes

- [x] P2 Expose read-only effective CPU/GPU display metadata from `engine/command.py` or a nearby command-layer wrapper
- [x] P2 Keep `build_command()` output unchanged
- [x] P2 Keep `Runtime args` storage and display unchanged

**Acceptance checks**

- [x] The feature adds display metadata without changing constructed runtime args or command semantics
- [x] Command/env-derived detection can be consumed by the GUI without duplicating parsing logic in `gui.py`

**Notes**

- The safest contract is “derive effective display values from command/env, but never rewrite config to make the display cleaner.”

---

## Phase P3 - GUI Columns And Summary Inventory

- [x] P3 Add `GPU`, `CPU`, and `Model size` columns to the main Treeview
- [x] P3 Populate row values with effective resolved CPU/GPU metadata and base-1024 `GB` model size
- [x] P3 Add a compact summary inventory list for detected GPUs and make it collapsible or temporarily hideable
- [x] P3 Preserve readable fallback text for unknown adapter names or missing signals

**Acceptance checks**

- [x] Visible rows show new `GPU`, `CPU`, and `Model size` columns
- [x] The summary list can hide/collapse and re-show during the session
- [x] `Runtime args` values are unchanged before vs. after the feature

**Notes**

- Session-local hide/collapse behavior is sufficient unless implementation discovers an existing persisted GUI preference mechanism nearby.

---

## Phase P4 - Documentation

- [x] P4 Update `README.md` with the CPU/GPU detection rules
- [x] P4 Document adapter-name fallback behavior and the unchanged `Runtime args` policy
- [x] P4 Document the `Model size` base-1024 `GB` convention so it matches RAM/VRAM expectations

**Acceptance checks**

- [x] README explains how effective CPU and GPU values are derived
- [x] README states that adapter names are best-effort and may degrade to `VulkanN`-style labels
- [x] README states that `Runtime args` remain unchanged by this GUI enhancement

**Notes**

- Keep the docs short and operator-focused; implementation details can stay in code/tests/report.

---

## Phase P5 - Verification And Report

- [x] P5 Run focused pytest for GUI, engine, benchmark, and any new detection tests
- [x] P5 Run narrow `ruff check` on the touched slice
- [x] P5 Perform a brief GUI smoke pass for column rendering and summary toggle behavior
- [x] P5 Write `reports/implementation/infra-local/llama-orchestrator/2026/20260525-llama-orchestrator-runtime-detection-display-report.md`

**Acceptance checks**

- [x] Focused tests pass for helper logic and GUI rendering helpers
- [x] Lint or editor diagnostics are clean for the touched slice
- [x] Manual GUI smoke validation confirms the summary panel and new columns behave as planned
- [x] The implementation report records changed files, validation commands, risks, and rollback guidance

**Notes**

- Start with the cheap unit checks before opening the GUI; the GUI pass should confirm layout and toggling only.

---

## Main Risks And Cheap Validation

- [x] Risk: duplicate `--threads` flags cause incorrect CPU display
  Cheap validation: add a unit test where the later `--threads` value wins
- [x] Risk: merged env overrides cause the wrong GPU label/device to display
  Cheap validation: add a unit test where `config.env` overrides the default GPU device
- [x] Risk: adapter names are not always available from logs
  Cheap validation: test both a Vulkan inventory fixture and a no-name fallback fixture
- [x] Risk: summary inventory panel makes the GUI cramped
  Cheap validation: perform one manual resize/collapse smoke check after row rendering is stable

## Recommended Smallest Edit Order

- [x] 1. Add the shared detection helper and tests
- [x] 2. Wire engine/command-level effective CPU/GPU resolution without changing command construction
- [x] 3. Update GUI row columns and formatting while preserving `Runtime args`
- [x] 4. Add the collapsible/hideable summary inventory panel
- [x] 5. Update README, run focused checks, and write the implementation report

## Dependencies

- [x] Existing `uv` or venv-based test workflow is available in `infra-local/llama-orchestrator`
- [x] No new dependencies are introduced

## Final Verification

- [x] Plan IDs and checklist IDs stay aligned (`P1` through `P5`)
- [x] Every acceptance criterion maps to at least one checklist step
- [x] Risk-heavy detection rules have cheap automated checks first
- [x] The report path and verification commands are recorded before implementation starts

## Completion Notes

- Implementation owner: `Implementer`
- Recommended validation commands:
  - `uv run pytest tests/test_detection.py tests/test_benchmark.py -v --no-cov`
  - `uv run pytest tests/test_gui.py tests/test_detection.py tests/test_benchmark.py -v --no-cov`
  - `uv run ruff check src/llama_orchestrator/gui.py src/llama_orchestrator/engine/detection.py src/llama_orchestrator/benchmark.py tests/test_gui.py tests/test_detection.py tests/test_benchmark.py`
  - `uv run python -c <Tk smoke snippet>`