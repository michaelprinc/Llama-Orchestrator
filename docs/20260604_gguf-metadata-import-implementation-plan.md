# Implementation Plan: GGUF Metadata Import Validation

**Date:** 2026-06-04  
**Methodology:** Spec Kit - Plan + Tasks + Analyze  
**Status:** Implemented

## Context

Existing code already has the right split:

- `memory_fit.py` owns local GGUF header parsing.
- `hf_import.py` owns Hugging Face repo/file selection and download/reuse flow.
- `model_metadata.py` builds additive instance-level metadata from local artifacts.
- `config/schema.py` keeps `model_metadata` optional and dynamic.

The implementation extends those seams without changing runtime config semantics.

## Workstreams

### A - GGUF Parser

- Extend `GgufModelMetadata` with general name, basename, quantization version, ROPE base, and NextN layer count.
- Keep invalid/unsupported GGUF behavior as soft failure returning `None`.

### B - HF Import Sidecar

- Add `ModelCardImportMetadata` and `ImportValidationResult`.
- Fetch README/model card best-effort without persisting tokens.
- Extract context, architecture, chat-template mention, MTP/NextN claim, and recommended runtime.
- Write `<model>.gguf.metadata.json` after successful download or explicit reuse.

### C - Validation

- Compare GGUF architecture/context/MTP facts with model-card claims.
- Compare filename quantization with GGUF `general.file_type` when both are known.
- Store warnings and confidence instead of blocking imports.

### D - Schema and Tests

- Expose the new GGUF fields in additive `ModelMetadataGgufExtracted`.
- Add tests for valid sidecar generation and malformed GGUF warning behavior.
- Run focused tests for HF import, model metadata, and config compatibility.

## Verification

- `python -m pytest tests/test_hf_import.py tests/test_model_metadata.py tests/test_config.py -q`

## Rollback

The change is additive. To rollback, remove sidecar-writing calls from `hf_import.py`/`gui.py`, remove the new helper dataclasses/functions, and keep the older minimal `GgufModelMetadata` fields.
