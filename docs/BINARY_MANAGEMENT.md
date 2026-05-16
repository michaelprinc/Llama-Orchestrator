# llama.cpp Binary Management

**Status:** active local guide  
**Scope:** `infra-local/llama-orchestrator`  
**Updated:** 2026-05-16

## Purpose

`llama-orchestrator` manages `llama-server` binaries as versioned local
artifacts. Each installation receives a UUID and is recorded in
`bins/registry.json`, so multiple llama.cpp versions and variants can coexist
without relying on one shared `bin/llama-server.exe`.

## Storage Layout

```text
llama-orchestrator/
├── bins/
│   ├── registry.json
│   └── <uuid>/
│       ├── llama-server.exe
│       ├── *.dll
│       └── version.json
└── bin/
    └── llama-server.exe
```

`bins/` is the primary layout. `bin/llama-server.exe` is retained as a legacy
fallback for old configs that do not define a `binary` section.

## Registry Contract

`bins/registry.json` stores:

- `schema_version`
- `default_binary_id`
- one record per installed binary with `id`, `version`, `variant`,
  `download_url`, `sha256`, `installed_at`, `path`, `size_bytes`,
  `executables`, and optional GitHub release metadata

The UUID in `id` is the primary key. Version and variant are supplementary
metadata and fallback lookup hints.

## Instance Config

Each instance can pin a binary:

```json
{
  "name": "gpt-oss",
  "binary": {
    "binary_id": "a9576b8e-4d9a-4f76-a392-8748632b35ed",
    "version": "b7572",
    "variant": "win-vulkan-x64",
    "source_url": "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
    "sha256": null
  }
}
```

Resolution order:

1. `binary.binary_id` resolves through `bins/registry.json`.
2. `binary.version` plus `binary.variant` is used when no UUID is present.
3. `bins/default_binary_id` can provide a default.
4. Legacy `bin/llama-server.exe` is used only as backward-compatible fallback.

## CLI Workflows

Install the default latest Vulkan build:

```powershell
llama-orch binary install
```

Install a specific release and variant:

```powershell
llama-orch binary install b7572 --variant win-vulkan-x64
llama-orch binary install latest --variant win-cuda-12.4-x64
```

Inspect installed binaries:

```powershell
llama-orch binary list
llama-orch binary info <uuid-or-prefix>
llama-orch binary latest --variant win-vulkan-x64
```

Remove an unused binary:

```powershell
llama-orch binary remove <uuid-or-prefix>
llama-orch binary remove <uuid-or-prefix> --force
```

`binary remove` deletes the installed package directory and unregisters the
record. Without `--force`, the CLI asks for confirmation.

## Supported Windows Variants

- `win-cpu-x64`
- `win-cpu-arm64`
- `win-vulkan-x64`
- `win-cuda-12.4-x64`
- `win-cuda-13.1-x64`
- `win-hip-radeon-x64`
- `win-sycl-x64`

The GUI installer uses the same variant set and defaults to `win-vulkan-x64`.
Its button label is `Install llama-server` because the workflow is not limited
to Vulkan builds.

## Current Gaps

The following items remain open in the version-scaling checklist:

- `scripts/migrate-bins.py` for migrating legacy `bin/` contents
- `llama-orch init --binary-version`
- `llama-orch upgrade <name> [--binary-version]`
- `llama-orch config set-binary <name> <uuid|version>`
- dedicated binary-manager unit and integration test files

Until those are implemented, update instance `config.json` manually or through
the GUI Add Model flow when pinning binaries.

## Validation

Use focused validation when editing binary-management behavior:

```powershell
uv run pytest tests/test_binaries.py tests/test_config.py
uv run pytest tests/test_cli_exit_integration.py
uv run ruff check src\llama_orchestrator\binaries src\llama_orchestrator\config tests\test_binaries.py
```

Run the full suite before release-level changes:

```powershell
uv run pytest
```
