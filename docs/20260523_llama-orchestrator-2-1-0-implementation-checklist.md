# Checklist: llama-orchestrator 2.1.0 Update

**Start date:** 2026-05-23  
**Methodology:** Spec Kit  
**Status:** Draft

---

## Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Done
- `[!]` Blocked
- `[-]` Out of scope

---

## Phase P1 - Version Alignment

- [ ] P1 Update `pyproject.toml` package version to `2.1.0`
- [ ] P1 Update `src/llama_orchestrator/__init__.py` runtime version and summary text
- [ ] P1 Confirm README version references and local-version framing match `2.1.0`

**Acceptance checks**

- [ ] Authoritative package/runtime surfaces report `2.1.0`
- [ ] Version wording is consistent across package metadata, runtime string, and README

**Notes**

- Keep historical documents historical unless they are explicitly used as release-facing references.

---

## Phase P2 - Config Contract And Serialization

- [ ] P2 Define static versus dynamic parameters in `config/schema.py`
- [ ] P2 Keep `save_config()` serialization stable in `config/loader.py`
- [ ] P2 Add or update schema/loader tests for round-trip serialization compatibility

**Acceptance checks**

- [ ] The static/dynamic split is documented in config/schema/documentation surfaces
- [ ] Existing `config.json` shape remains compatible after save/load round-trip

**Notes**

- Prefer descriptive metadata or helper constants over new persisted keys.

---

## Phase P3 - Validator Warning

- [ ] P3 Add a warning-level validation issue for `server.host = 0.0.0.0`
- [ ] P3 Ensure the warning appears in non-GUI validation/lint flow without failing validation
- [ ] P3 Add focused tests for warning presence and non-blocking behavior

**Acceptance checks**

- [ ] `0.0.0.0` produces a warning and not an error
- [ ] Intentional remote-bind configurations still validate successfully

**Notes**

- Reuse the existing `ValidationIssue` pattern and keep operator intent intact.

---

## Phase P4 - Docs, Report, And Verification

- [ ] P4 Update README shipped-versus-roadmap wording for `2.1.0`
- [ ] P4 State current facts explicitly: SQLite-backed runtime state, Windows desktop GUI, available telemetry only
- [ ] P4 Call out unimplemented items explicitly: MCP gateway, llama-swap export, TTFT/cache-hit dashboards
- [ ] P4 Write `reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-2-1-0-update-report.md`
- [ ] P4 Run focused pytest and Ruff checks for the touched slice

**Acceptance checks**

- [ ] README reflects shipped `2.1.0` scope without implying future features are present
- [ ] Implementation report captures changed files, validation commands, risks, and rollback
- [ ] Focused tests and narrow lint pass

**Notes**

- Keep the report concise and evidence-based.

---

## Open Questions

- [ ] Should the static/dynamic split be exposed only via field descriptions and README, or also via a small helper/constant in `config/schema.py` for easier test assertions?

## Dependencies

- [ ] Existing `uv` test workflow is available in `infra-local/llama-orchestrator`
- [ ] No new dependencies are introduced

## Final Verification

- [ ] Plan IDs and checklist IDs stay aligned (`P1` through `P4`)
- [ ] Every acceptance criterion maps to at least one checklist step
- [ ] Warning behavior is covered by focused tests
- [ ] Risky changes include rollback guidance in the report

## Completion Notes

- Implementation owner: `Implementer`
- Recommended validation commands:
  - `uv run pytest tests/test_config.py tests/test_loader.py tests/test_validator.py -v --no-cov`
  - `uv run ruff check src/llama_orchestrator/__init__.py src/llama_orchestrator/config/schema.py src/llama_orchestrator/config/loader.py src/llama_orchestrator/config/validator.py tests/test_config.py tests/test_loader.py tests/test_validator.py`