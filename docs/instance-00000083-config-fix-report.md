# Instance `00000083` Config Fix Report

Date: 2026-07-04

## Summary

The instance config for `00000083_b56e891a-1af4-4f8c-af01-39f0e65dbca8` was updated to run a Qwen3.5 2B MTP-capable GGUF model with a `65536` token context window. During the first repair pass, the stable instance identifier in `config.json` was changed from `qwen3-5-2b-gguf-iq4_nl` to `qwen3-5-2b-mtp-gguf-q4_k_m`.

That change broke daemon and restart dependencies which still referenced the original instance name. The observed failure was:

```text
restart qwen3-5-2b-gguf-iq4_nl failed: [qwen3-5-2b-gguf-iq4_nl] Failed to load config: Instance 'qwen3-5-2b-gguf-iq4_nl' not found.
```

## Root Cause

In `llama-orchestrator`, the `name` field acts as the stable instance key used by CLI selectors, daemon restart actions, and runtime aliasing.

Changing only the `name` field caused a mismatch between:

- persisted and queued restart references using `qwen3-5-2b-gguf-iq4_nl`
- the updated config which declared the instance as `qwen3-5-2b-mtp-gguf-q4_k_m`

The instance folder, `instance_uid`, and `instance_no` stayed the same, but the selector key changed, which is what broke restart resolution.

## Corrective Action

The following fixes were applied:

- restored `name` to `qwen3-5-2b-gguf-iq4_nl`
- preserved the MTP-capable model path:
  `models\\unsloth_Qwen3.5-2B-MTP-GGUF\\Qwen3.5-2B-Q4_K_M.gguf`
- preserved `context_size: 65536`
- preserved MTP runtime settings:
  `--spec-type draft-mtp`
- preserved corrected Vulkan device argument:
  `--device Vulkan0`
- kept updated additive metadata showing:
  `builtin_mtp.available = true`
  `nextn_predict_layers = 1`

## Verification

After restoring the original instance name:

- `llama-orch describe qwen3-5-2b-gguf-iq4_nl` resolves successfully
- the instance starts under the original stable selector
- health reaches `HEALTHY`

## Recommendation

For existing instances, do not repurpose the `name` field to reflect model changes unless all dependent daemon state, restart references, automation hooks, and operational selectors are migrated at the same time.

For this project, treat these fields as follows:

- `name`: stable operational identifier
- `display_name`: user-facing descriptive label
- `model.path` and `model_metadata`: actual model/runtime description
