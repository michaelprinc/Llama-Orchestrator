# Specification: GGUF Metadata Import Validation

**Date:** 2026-06-04  
**Methodology:** Spec Kit - Specify + Clarify  
**Status:** Implemented  
**Feature type:** Hugging Face import / local model metadata

## Goal

When a GGUF model is imported from Hugging Face, llama-orchestrator should inspect the local GGUF metadata, compare it with filename/repository/model-card claims, and write an additive sidecar metadata file for reliable cataloging and later model-aware workflows.

## Requirements

- GGUF metadata is the primary technical source for artifact facts.
- Model-card data is treated as author-declared intent, not as an override for GGUF facts.
- Sidecar metadata stores the importer validation result and must not replace embedded GGUF metadata.
- Import remains usable for local/offline/malformed files; invalid GGUF metadata produces warnings instead of crashing.
- Hugging Face tokens remain keyring/session-only and are never written to sidecar/config files.
- Runtime configuration fields are not changed by metadata validation.

## Metadata Surface

The importer reads these GGUF keys when present:

- `general.architecture`
- `general.name`
- `general.basename`
- `general.file_type`
- `general.quantization_version`
- `tokenizer.chat_template`
- `<arch>.context_length`
- `<arch>.embedding_length`
- `<arch>.block_count`
- `<arch>.attention.head_count`
- `<arch>.attention.head_count_kv`
- `<arch>.rope.freq_base`
- `<arch>.nextn_predict_layers`

## Sidecar Shape

Sidecars are written next to the local model as `<model>.gguf.metadata.json` with:

- `source`: provider, repo ID, filename, revision, fetched timestamp.
- `gguf_metadata`: normalized technical facts from the local GGUF header.
- `model_card_metadata`: best-effort claims extracted from README/model card text.
- `validation`: `ok`/`warning`, confidence, and warning list.

## Acceptance Criteria

- A valid GGUF with matching model-card claims produces `validation.status = ok`.
- Missing or unreadable GGUF metadata produces `validation.status = warning`.
- MTP/NextN mismatch between model card and GGUF emits a warning.
- Context-length mismatch between model card and GGUF emits a warning.
- Existing user-owned `model_metadata.user_metadata` remains untouched.
- Existing HF import paths for download, reuse, cancel, token handling, and path traversal protections remain intact.
