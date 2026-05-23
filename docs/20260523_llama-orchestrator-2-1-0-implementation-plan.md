# Implementation Plan: llama-orchestrator 2.1.0 Update

**Date:** 2026-05-23  
**Methodology:** Spec Kit  
**Type:** release clarification + safety update  
**Status:** Draft

---

## 1. Objective

Ship a narrow `2.1.0` update for `infra-local/llama-orchestrator` that:

- aligns authoritative version metadata and release-facing docs to `2.1.0`;
- documents the static versus dynamic config split without changing GUI behavior;
- adds a non-blocking validation warning for `server.host = 0.0.0.0`;
- preserves current config serialization and operator workflows;
- records the work in a short implementation report.

## 2. Scope

### In scope

- Version bump in authoritative package/runtime surfaces.
- Config contract clarification in schema and serialization-adjacent code paths.
- Warning-level validator coverage for wide bind addresses.
- Focused test updates for schema, loader/serialization, and validator behavior.
- README and release-facing documentation refresh for shipped `2.1.0` behavior.
- One implementation report under `reports/implementation/infra-local/llama-orchestrator/2026/`.

### Out of scope

- GUI behavior or layout changes.
- Runtime-state storage changes away from SQLite.
- New telemetry, MCP gateway, llama-swap export, or Docker runtime integration.
- Breaking JSON config shape changes or forced migration of existing instance configs.

## 3. Repo Context

### Relevant files and modules

- `infra-local/llama-orchestrator/pyproject.toml` - authoritative package version.
- `infra-local/llama-orchestrator/src/llama_orchestrator/__init__.py` - runtime version string and package summary.
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/schema.py` - config contract and field descriptions.
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/loader.py` - `load_config` / `save_config`, including `model_dump(mode="json")` serialization.
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/validator.py` - non-GUI warnings and lint surface.
- `infra-local/llama-orchestrator/tests/test_config.py` - schema expectations.
- `infra-local/llama-orchestrator/tests/test_loader.py` - config save/load serialization coverage.
- `infra-local/llama-orchestrator/tests/test_validator.py` - validation warning coverage.
- `infra-local/llama-orchestrator/README.md` - operator-facing current-state description.

### Reuse opportunities

- Reuse existing Pydantic schema descriptions instead of introducing new persisted keys.
- Reuse `save_config()` as the canonical serialization path.
- Reuse existing validator warning pattern (`ValidationIssue` with `severity="warning"`).
- Reuse the current report convention from `reports/implementation/infra-local/llama-orchestrator/2026/`.

## 4. Assumptions And Clarifications

### Confirmed

- The source specification is `docs/20260523_llama-orchestrator-2-1-0-specification.md`.
- Runtime state is SQLite-backed and the GUI is a Windows desktop management UI.
- `0.0.0.0` must remain allowed, but visibly discouraged through a non-GUI warning.

### Assumptions

- The static/dynamic distinction can remain additive and descriptive, primarily through schema descriptions, helper text, and serialization tests.
- Historical planning artifacts in `infra-local/llama-orchestrator/docs/` stay historical unless the implementation explicitly chooses to refresh them.
- A focused README refresh is sufficient for release-facing documentation unless a second authoritative doc is identified during implementation.

### Open point

- Decide during implementation whether the static/dynamic split is documented only in field descriptions and README tables, or also exposed through a small schema helper/constant for testable categorization.

## 5. Flow Notes

```text
config.json
  -> InstanceConfig / submodels in config/schema.py
  -> save_config() in config/loader.py serializes via model_dump(mode="json")
  -> validate_instance() / lint_config() in config/validator.py emit warnings
  -> README and report describe shipped 2.1.0 behavior and exclusions
```

Key planning constraint: the config contract may become clearer, but persisted JSON should remain compatible with existing `instances/*/config.json` files.

## 6. Proposed Implementation Approach

### P1 Align version and release framing

- Update authoritative version strings from `2.0.0` to `2.1.0`.
- Tighten package summary text so it describes shipped behavior without implying roadmap features.

Likely files to touch:

- `infra-local/llama-orchestrator/pyproject.toml`
- `infra-local/llama-orchestrator/src/llama_orchestrator/__init__.py`
- `infra-local/llama-orchestrator/README.md`

### P2 Clarify config schema and preserve serialization

- Define the static versus dynamic parameter split in `config/schema.py` descriptions or a small helper surface.
- Keep `save_config()` output stable; avoid breaking or renaming keys in persisted JSON.
- Add or update loader/schema tests to prove serialization still round-trips cleanly.

Likely files to touch:

- `infra-local/llama-orchestrator/src/llama_orchestrator/config/schema.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/loader.py`
- `infra-local/llama-orchestrator/tests/test_config.py`
- `infra-local/llama-orchestrator/tests/test_loader.py`

### P3 Add wide-bind safety warning and focused tests

- Emit a warning-level `ValidationIssue` when `server.host` is `0.0.0.0`.
- Keep validation non-blocking so intentional remote-access configs still pass.
- Cover the warning with focused validator tests and confirm no false-positive error behavior.

Likely files to touch:

- `infra-local/llama-orchestrator/src/llama_orchestrator/config/validator.py`
- `infra-local/llama-orchestrator/tests/test_validator.py`

### P4 Refresh docs and write implementation report

- Update README language to separate shipped `2.1.0` behavior from roadmap items.
- State explicitly: SQLite-backed runtime state, Windows desktop GUI, available telemetry (`TPS`, latency, memory), and unimplemented items (`MCP gateway`, llama-swap export, TTFT/cache-hit dashboards).
- Write a concise implementation report with changed files, validation commands, risks, and rollback notes.

Likely files to touch:

- `infra-local/llama-orchestrator/README.md`
- `reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-2-1-0-update-report.md`

## 7. Task Breakdown

| ID | Phase | Task | Type | Dependencies | Parallel | Done when |
|----|------|------|------|--------------|----------|-----------|
| P1 | Version | Bump authoritative `2.1.0` version surfaces | code/docs | - | no | Package metadata and runtime version match `2.1.0` |
| P2 | Config | Document static vs dynamic parameters without breaking JSON shape | code/docs | P1 | no | Schema/docs express the split and `save_config()` remains compatible |
| P3 | Safety | Add `0.0.0.0` warning to non-GUI validation flow | code/test | P2 | no | Validator emits a warning, not an error, for wide bind |
| P4 | Verify+Docs | Update README/release notes and write implementation report | docs/report | P1,P2,P3 | [P] | Docs match shipped `2.1.0` scope and report captures evidence |

## 8. Dependencies

- No new packages or external APIs.
- Existing local test toolchain in `infra-local/llama-orchestrator` (`uv`, `pytest`, `ruff`).
- Existing config fixtures and validation helpers in current test suite.

## 9. Acceptance Criteria

- [ ] `pyproject.toml` and `src/llama_orchestrator/__init__.py` report `2.1.0`.
- [ ] The config contract distinguishes static versus dynamic parameters in schema/documentation surfaces without breaking saved `config.json` shape.
- [ ] `save_config()` serialization remains compatible with existing instance configs, covered by loader/schema tests.
- [ ] Validation emits a warning-level issue for `server.host = 0.0.0.0` and still allows the config.
- [ ] Focused tests cover the schema/serialization and validator warning changes.
- [ ] README clearly separates shipped `2.1.0` behavior from roadmap items and accurately states SQLite state, Windows desktop GUI, and current telemetry limits.
- [ ] A short implementation report exists under `reports/implementation/infra-local/llama-orchestrator/2026/`.

## 10. Verification Strategy

### Focused checks

- `uv run pytest tests/test_config.py tests/test_loader.py tests/test_validator.py -v --no-cov`
- `uv run ruff check src/llama_orchestrator/__init__.py src/llama_orchestrator/config/schema.py src/llama_orchestrator/config/loader.py src/llama_orchestrator/config/validator.py tests/test_config.py tests/test_loader.py tests/test_validator.py`
- Optional version smoke check: `uv run python -c "import llama_orchestrator; print(llama_orchestrator.__version__)"`

### Artifacts

- Updated README and version surfaces.
- Passing focused test output.
- Implementation report with changed files, validation commands, risks, and rollback notes.

## 11. Risks And Rollback

| Risk | Impact | Mitigation | Rollback |
|------|--------|------------|----------|
| Static/dynamic split drifts into a breaking config redesign | medium | Keep the change descriptive and additive; verify `save_config()` output | Revert schema/loader edits and keep current JSON contract |
| `0.0.0.0` warning becomes an error or blocks existing workflows | high | Implement only as `severity="warning"`; cover with tests | Revert validator change and restore prior warning set |
| Docs still imply roadmap features are shipped | high | Consolidate shipped-vs-roadmap wording in README and report | Revert doc slice and re-apply narrower factual wording |
| Version strings drift across files | medium | Limit authoritative bump to package/runtime surfaces and test the runtime version | Revert incomplete bump and reapply consistently |

## 12. Deliverables

- `infra-local/llama-orchestrator/docs/20260523_llama-orchestrator-2-1-0-implementation-plan.md`
- `infra-local/llama-orchestrator/docs/20260523_llama-orchestrator-2-1-0-implementation-checklist.md`
- Updated code/tests/docs listed above
- `reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-2-1-0-update-report.md`

## 13. Sources

### Repo sources

- `docs/20260523_llama-orchestrator-2-1-0-specification.md`
- `docs/reference/workspace/speckit-principles.md`
- `docs/templates/speckit-implementation-plan-template.md`
- `docs/templates/speckit-implementation-checklist-template.md`
- `infra-local/llama-orchestrator/pyproject.toml`
- `infra-local/llama-orchestrator/src/llama_orchestrator/__init__.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/schema.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/loader.py`
- `infra-local/llama-orchestrator/src/llama_orchestrator/config/validator.py`
- `infra-local/llama-orchestrator/tests/test_config.py`
- `infra-local/llama-orchestrator/tests/test_loader.py`
- `infra-local/llama-orchestrator/tests/test_validator.py`
- `infra-local/llama-orchestrator/README.md`