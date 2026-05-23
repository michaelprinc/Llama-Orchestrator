# Checklist: llama-orchestrator 2.1.0 Update

**Start date:** 2026-05-23  
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

## Phase P1 - Version Alignment

- [x] P1 Update `pyproject.toml` package version to `2.1.0`
- [x] P1 Update `src/llama_orchestrator/__init__.py` runtime version and summary text
- [x] P1 Confirm README version references and local-version framing match `2.1.0`

**Acceptance checks**

- [x] Authoritative package/runtime surfaces report `2.1.0`
- [x] Version wording is consistent across package metadata, runtime string, and README

**Notes**

- Keep historical documents historical unless they are explicitly used as release-facing references.

---

## Phase P2 - Config Contract And Serialization

- [x] P2 Define static versus dynamic parameters in `config/schema.py`
- [x] P2 Keep `save_config()` serialization stable in `config/loader.py`
- [x] P2 Add or update schema/loader tests for round-trip serialization compatibility

**Acceptance checks**

- [x] The static/dynamic split is persisted through `parameter_mutability` in config/schema/documentation surfaces
- [x] Existing configs remain compatible after save/load round-trip, even when the new persisted key is absent

**Notes**

- Use the additive `parameter_mutability` section plus schema defaults so older configs still load.

---

## Phase P3 - Validator Warning

- [x] P3 Add a warning-level validation issue for `server.host = 0.0.0.0`
- [x] P3 Ensure the warning appears in non-GUI validation/lint flow without failing validation
- [x] P3 Add focused tests for warning presence and non-blocking behavior

**Acceptance checks**

- [x] `0.0.0.0` produces a warning and not an error
- [x] Intentional remote-bind configurations still validate successfully

**Notes**

- Reuse the existing `ValidationIssue` pattern and keep operator intent intact.

---

## Phase P4 - Docs, Report, And Verification

- [x] P4 Update README shipped-versus-roadmap wording for `2.1.0`
- [x] P4 State current facts explicitly: SQLite-backed runtime state, Windows desktop GUI, available telemetry only
- [x] P4 Call out unimplemented items explicitly: MCP gateway, llama-swap export, TTFT/cache-hit dashboards
- [x] P4 Write `reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-2-1-0-update-report.md`
- [x] P4 Run focused pytest and narrow lint or diagnostics for the touched slice

**Acceptance checks**

- [x] README reflects shipped `2.1.0` scope without implying future features are present
- [x] Implementation report captures changed files, validation commands, risks, and rollback
- [x] Focused tests and narrow lint or diagnostics pass

**Notes**

- Keep the report concise and evidence-based.

---

## Resolved Decisions

- [x] The static/dynamic split is exposed through the persisted `parameter_mutability` section and tested in `config/schema.py`.

## Dependencies

- [x] Existing `uv` or venv-based test workflow is available in `infra-local/llama-orchestrator`
- [x] No new dependencies are introduced

## Final Verification

- [x] Plan IDs and checklist IDs stay aligned (`P1` through `P4`)
- [x] Every acceptance criterion maps to at least one checklist step
- [x] Warning behavior is covered by focused tests
- [x] Risky changes include rollback guidance in the report

## Completion Notes

- Implementation owner: `Implementer`
- Recommended validation commands:
  - `uv run pytest tests/test_config.py tests/test_loader.py tests/test_validator.py -v --no-cov`
  - `uv run ruff check src/llama_orchestrator/__init__.py src/llama_orchestrator/config/schema.py src/llama_orchestrator/config/loader.py src/llama_orchestrator/config/validator.py tests/test_config.py tests/test_loader.py tests/test_validator.py`
  - Fallback when `ruff` is unavailable in the active environment: use editor diagnostics on the touched slice