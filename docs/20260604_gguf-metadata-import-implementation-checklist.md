# Checklist: GGUF Metadata Import Validation

## Phase A - Specify

- [x] Define GGUF metadata fields needed for import validation.
- [x] Define model-card claims as secondary evidence.
- [x] Define sidecar as importer output, not GGUF replacement.

## Phase B - Implement

- [x] Extend local GGUF parser fields.
- [x] Add sidecar path and writer.
- [x] Add model-card fetch and extractor.
- [x] Add GGUF/model-card validation result.
- [x] Write sidecar after HF download.
- [x] Write sidecar when the user reuses an existing imported file.
- [x] Preserve token handling and path traversal protections.

## Phase C - Verify

- [x] Test valid GGUF + matching model card.
- [x] Test invalid GGUF soft-warning behavior.
- [x] Test existing HF import helpers still pass.
- [x] Test instance-level model metadata still builds.
- [x] Test config compatibility with optional dynamic metadata.

## Acceptance Checks

- [x] GGUF facts remain the primary technical source.
- [x] Model-card claims do not overwrite GGUF facts.
- [x] Sidecar stores validation status, confidence, and warnings.
- [x] Runtime config is not mutated by sidecar generation.
- [x] User-owned metadata is preserved by existing metadata builder behavior.
