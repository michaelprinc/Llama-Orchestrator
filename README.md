# llama-orchestrator

> Docker-like CLI orchestration for llama.cpp server instances on Windows

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Beta 2.1.0](https://img.shields.io/badge/status-beta%202.1.0-orange.svg)](https://github.com/MichaelPrinc/llama-orchestrator)

---

## Overview

**llama-orchestrator** is a Python-based control plane for managing multiple
[`llama.cpp`](https://github.com/ggml-org/llama.cpp) server instances on
Windows. It provides a `docker-compose`-style operator experience — `init`,
`up`, `down`, `ps`, `logs`, `dashboard`, and `gui` — backed by native
Windows process management, SQLite state persistence, and a rich desktop GUI.

### Key Capabilities

| Capability | Description |
|---|---|
| 🚀 **Multi-instance orchestration** | Run multiple `llama-server` processes on different ports, each with its own model, GPU binding, and runtime args |
| 🔄 **Health monitoring** | Configurable HTTP/TCP/custom probes with retry/backoff and jittered restart delays |
| ♻️ **Auto-restart** | Background daemon with exponential-backoff restart on failure |
| 📊 **TUI Dashboard** | Live terminal dashboard with recent events panel and instance filtering |
| 🪟 **Desktop GUI** | Windows Tkinter management panel — model table, tag filtering, batch actions, inline runtime args editing, benchmark controls |
| 🔧 **Binary management** | Versioned `llama-server` packages under `bins/`, UUID registry, per-instance binary pinning |
| 🛡️ **Windows service** | NSSM-backed daemon install/uninstall for persistent background operation |
| 📈 **Benchmark harness** | Quick, serial, grid, and prompt-based benchmark workflows with TTFT / throughput / cache-reuse telemetry |
| 📝 **Audit logging** | File-based rotating logs, `logs -f` streaming, event log in SQLite |

---

## Local Version Status

**Primary supported tool version:** `llama-orchestrator` `2.1.0`

Use this local checkout at `infra-local/llama-orchestrator/` as the main
version of the tool for this workspace. It is the preferred implementation for
local llama.cpp orchestration, Windows autostart, GUI usage, daemon operation,
health checks, and versioned llama.cpp binary management.

When this workspace also contains older planning notes or upstream package
copies, treat this directory and its `pyproject.toml` version as authoritative
unless a newer migration document explicitly supersedes it.

---

## Current V2 Status

The V2 proposal has been implemented in this local checkout for the core
orchestration surface:

- **Process and state reliability:** SQLite V2 schema, runtime state, event log,
  process validation, per-instance locking, stale-state reconciliation, and
  port collision checks.
- **Daemon and logging reliability:** file-based rotating logs, `logs -f`, an
  interruptible daemon loop, cooperative daemon stop, and NSSM-backed Windows
  service install/uninstall commands.
- **Health and restart behavior:** configurable HTTP/TCP/custom probes, custom
  health paths, retry/backoff settings, and jittered restart delays.
- **CLI and dashboard UX:** standard exit codes, richer `describe` output, recent
  events in the dashboard, and explicit detached/attached start behavior.
- **Desktop GUI and benchmark workflows:** model table management, tag filtering,
  batch actions, inline runtime args editing, quick benchmark history, prompt
  selection, `loading` versus `ready` display semantics, and best-effort VRAM
  reporting.
- **Binary management:** versioned `llama-server` packages under `bins/`, a UUID
  registry in `bins/registry.json`, per-instance binary pinning, and GUI/CLI
  install/list/info/remove/latest workflows.

**Known remaining gaps** are operational or follow-up items, not blockers for the
local V2 command surface: manual Windows Services UI smoke testing still needs
to be run on a target host with `nssm.exe` in `PATH`; several binary-management
convenience commands remain listed as future work in [`docs/BINARY_MANAGEMENT.md`](docs/BINARY_MANAGEMENT.md);
and GUI column visibility, tag filter, and window geometry are intentionally
session-local today.

---

## 2.1.0 Scope Clarifications

- Runtime state is stored in `state/state.sqlite`; `instances/<name>/config.json`
  remains the persisted desired-state input, not a live runtime database.
- The desktop GUI is a Windows management panel for start/stop/health/benchmark
  workflows. It does not watch JSON files reactively and it does not assume any
  autonomous agent is mutating configs in the background.
- "Docker-like" describes the operator workflow (`init`, `up`, `down`, `ps`,
  `logs`, detached execution, restart policy). The shipped runtime remains
  Windows-native with Task Scheduler and NSSM integration, not a Docker
  deployment target.
- Telemetry shipped today is still partial: Quick benchmark artifacts now
  include TTFT, generation and end-to-end throughput, prompt and cache reuse
  metrics when llama.cpp returns the needed counters, speculative or draft
  acceptance metrics when available, and best-effort memory or VRAM reporting.
  Dedicated TTFT or cache trend dashboards, MCP gateway integration, and
  llama-swap export are not part of `2.1.0`.

---

## Quick Start

```powershell
# Install
pip install -e .

# Create instance config
llama-orch init gpt-oss --model ../models/gpt-oss-20b-Q4_K_S.gguf --port 8001

# Start instance
llama-orch up gpt-oss

# Check status
llama-orch ps

# View dashboard
llama-orch dashboard

# Open desktop GUI
llama-orch gui

# Stop instance
llama-orch down gpt-oss
```

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                       CONTROL PLANE (Python)                      │
│                                                                   │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │   CLI    │──▶│  Daemon  │──▶│   TUI    │──▶│   GUI    │     │
│  │ (Typer)  │   │ (Monitor│   │ (Rich/Tk)│   │ (Tkinter)│     │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
│       │              │              │              │              │
│       └──────────────┼──────────────┼──────────────┘              │
│                      ▼              ▼                             │
│            ┌─────────────────┐  ┌─────────────────┐              │
│            │  State (SQLite) │  │  Logs (Rotating) │              │
│            │  state.sqlite   │  │  daemon.log      │              │
│            └─────────────────┘  └─────────────────┘              │
└───────────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┬──────────────────┐
          ▼            ▼            ▼                    ▼
┌───────────────────────────────────────────────────────────────────┐
│                        DATA PLANE (llama.cpp)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │ llama-server│  │ llama-server│  │ llama-server│   ...         │
│  │ :8001 (A)   │  │ :8002 (B)   │  │ :8003 (C)   │               │
│  │ Vulkan/GPU0 │  │ CUDA/GPU1   │  │ Vulkan/GPU2 │               │
│  └─────────────┘  └─────────────┘  └─────────────┘               │
└───────────────────────────────────────────────────────────────────┘
```

### Component Map

| Layer | Module | Responsibility |
|---|---|---|
| **CLI** | `cli.py` | Typer command routing, subcommands, exit codes |
| **Engine** | `engine/process.py` | Process lifecycle (start, stop, restart, signal handling) |
| | `engine/command.py` | `llama-server` command-line builder |
| | `engine/state.py` | SQLite V2 persistence, runtime state, event log |
| | `engine/detection.py` | GPU/runtime discovery (vulkaninfo, CUDA) |
| | `engine/locking.py` | Per-instance file-based locking |
| | `engine/logging_config.py` | File-based rotating log setup |
| | `engine/reconciler.py` | Stale-state reconciliation, port collision checks |
| | `engine/detach.py` | Detached/attached start behavior |
| | `engine/metadata.py` | Instance metadata management |
| | `engine/validator.py` | Engine-level validation |
| **Health** | `health/probes.py` | Pluggable HTTP/TCP/custom health probes |
| | `health/checker.py` | Health check orchestration flow |
| | `health/monitor.py` | Background health monitor loop |
| | `health/backoff.py` | Exponential backoff with jitter |
| | `health/client_pool.py` | Reusable `httpx.Client` pool |
| **GUI** | `gui/app.py` | Main Tkinter application window |
| | `gui/table.py` | Treeview model table rendering |
| | `gui/refresh.py` | Background refresh snapshot controller |
| | `gui/row_renderer.py` | Per-row diff rendering |
| | `gui/toolbar.py` | Action toolbar |
| | `gui/actions.py` | Row action handlers (start, stop, benchmark) |
| | `gui/benchmark_controls.py` | Benchmark UI controls |
| | `gui/grid_benchmark_dialog.py` | Grid benchmark dialog |
| | `gui/kv_cache_dialogs.py` | KV-cache configuration dialogs |
| | `gui/gpu_inventory.py` | GPU inventory panel |
| | `gui/activity_log.py` | Activity log panel |
| | `gui/dialogs.py` | Reusable dialog widgets |
| | `gui/model_dialogs.py` | Model selection/edit dialogs |
| | `gui/hf_import_dialog.py` | HuggingFace import dialog |
| | `gui/metadata_cache.py` | Cached model metadata |
| | `gui/usability.py` | Keyboard shortcuts, visual indicators |
| | `gui_state.py` | Persisted table column preferences |
| **Daemon** | `daemon/_daemon_main.py` | Background daemon entry point |
| | `daemon/service.py` | NSSM service install/uninstall |
| | `daemon/win_service.py` | Windows service wrapper |
| **Binary Mgmt** | `binaries/downloader.py` | GitHub release asset download |
| | `binaries/manager.py` | Install/list/remove/latest workflows |
| | `binaries/registry.py` | UUID registry management |
| | `binaries/github.py` | GitHub release metadata queries |
| | `binaries/schema.py` | Binary record Pydantic schema |
| **Config** | `config/schema.py` | Instance config Pydantic schemas |
| | `config/loader.py` | JSON config loading |
| | `config/validator.py` | Config validation |
| | `config/migration.py` | Schema migrations |
| **Benchmark** | `benchmark.py` | Quick/serial/grid benchmark harness |
| | `benchmark_grid.py` | Grid benchmark orchestration |
| **Utilities** | `model_metadata.py` | GGUF metadata extraction |
| | `memory_fit.py` | Estimated memory fit calculation |
| | `runtime_args.py` | Runtime argument resolution |
| | `hf_import.py` | HuggingFace model import |
| | `diffusion_http_adapter.py` | HTTP adapter for diffusion models |
| **CLI Helpers** | `cli_describe.py` | Rich describe output |
| | `cli_exit_codes.py` | Standardized exit code definitions |

---

## CLI Commands

### Instance Management

| Command | Description |
|---|---|
| `llama-orch up <name>` | Start an instance (detached by default) |
| `llama-orch up <name> --no-detach` | Start and keep attached to terminal |
| `llama-orch down <name>` | Stop an instance |
| `llama-orch restart <name>` | Restart an instance |
| `llama-orch ps` | List all instances with status |

### Diagnostics

| Command | Description |
|---|---|
| `llama-orch health <name>` | Check instance health |
| `llama-orch logs <name>` | View stdout/stderr logs |
| `llama-orch logs <name> --stream both` | Stream merged logs |
| `llama-orch describe <name>` | Show config, runtime, estimated memory fit, events, health history |

### Dashboard & GUI

| Command | Description |
|---|---|
| `llama-orch dashboard` | Live TUI dashboard with recent events panel |
| `llama-orch dashboard --events-for <name>` | Filter events to one instance |
| `llama-orch gui` | Open Windows desktop GUI |

### Configuration

| Command | Description |
|---|---|
| `llama-orch config validate` | Validate a configuration file |
| `llama-orch config lint` | Validate all discovered instance configs |

### Daemon Management

| Command | Description |
|---|---|
| `llama-orch daemon start` | Start background health monitor daemon |
| `llama-orch daemon status` | Show daemon status |
| `llama-orch daemon stop` | Stop background daemon |
| `llama-orch daemon install` | Install daemon as Windows service via NSSM |
| `llama-orch daemon uninstall` | Remove Windows service |

### Binary Management

| Command | Description |
|---|---|
| `llama-orch binary install [version]` | Install a llama.cpp `llama-server` package from GitHub releases |
| `llama-orch binary list` | List installed versioned binaries |
| `llama-orch binary info <uuid>` | Show metadata for an installed binary |
| `llama-orch binary remove <uuid>` | Remove an installed binary (prompts for confirmation) |
| `llama-orch binary latest` | Show the latest available llama.cpp release |

### CLI Exit Codes

| Range | Meaning |
|---|---|
| `2` | Usage / argument errors |
| `10–19` | Configuration errors |
| `20–39` | Instance / process errors |
| `50–69` | Binary / daemon errors |

---

## Configuration Reference

Instance configs are stored in `instances/<name>/config.json`:

```json
{
  "name": "gpt-oss",
  "binary": {
    "binary_id": "a9576b8e-4d9a-4f76-a392-8748632b35ed",
    "version": "b7572",
    "variant": "win-vulkan-x64",
    "source_url": "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
    "sha256": null
  },
  "model": {
    "path": "../../models/gpt-oss-20b-Q4_K_S.gguf",
    "context_size": 4096,
    "batch_size": 512,
    "threads": 16
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8001,
    "parallel": 4
  },
  "gpu": {
    "backend": "vulkan",
    "device_id": 1,
    "layers": 30
  },
  "env": {
    "GGML_VULKAN_DEVICE": "1"
  },
  "args": [
    "--no-mmproj",
    "--no-canvas",
    "--log-disable"
  ]
}
```

### Configuration Sections

| Section | Fields | Purpose |
|---|---|---|
| `binary` | `binary_id`, `version`, `variant`, `source_url`, `sha256` | Pin the `llama-server` binary to a specific version/variant |
| `model` | `path`, `context_size`, `batch_size`, `threads` | Define the GGUF model and inference parameters |
| `server` | `host`, `port`, `parallel` | HTTP server binding and parallelism |
| `gpu` | `backend`, `device_id`, `layers` | GPU backend selection and layer offloading |
| `env` | key-value pairs | Environment variables passed to the process |
| `args` | array of strings | Additional `llama-server` CLI flags |

---

## Directory Layout

```
llama-orchestrator/
├── src/llama_orchestrator/        # Python source package
│   ├── cli.py                     # Typer CLI entry point
│   ├── benchmark.py               # Benchmark harness
│   ├── benchmark_grid.py          # Grid benchmark orchestration
│   ├── engine/                    # Process lifecycle & state
│   ├── health/                    # Health probes & monitor
│   ├── gui/                       # Tkinter desktop GUI
│   ├── daemon/                    # Background daemon & Windows service
│   ├── binaries/                  # Versioned binary management
│   ├── config/                    # Schema, loader, validator
│   └── *.py                       # Utilities (metadata, memory fit, HF import)
├── instances/<name>/              # Instance desired-state configs (JSON)
├── bins/<uuid>/                   # Installed llama.cpp binary packages
│   └── registry.json              # UUID → binary metadata registry
├── state/                         # Runtime state & telemetry
│   ├── state.sqlite               # SQLite V2 runtime database
│   ├── benchmark_history.sqlite   # Benchmark result history
│   ├── benchmark_runs/            # Per-run benchmark artifacts
│   ├── benchmark_settings.json    # Benchmark settings
│   ├── daemon.log                 # Daemon rotating log
│   ├── gpu_aliases.json           # GPU alias mappings
│   └── gui_settings.json          # GUI column preferences
├── scripts/                       # PowerShell automation helpers
├── docs/                          # Implementation plans & checklists
├── tests/                         # pytest test suite
├── benchmarks/                    # Benchmark reference data
├── models/                        # Reference model paths
├── logs/                          # Additional log output
├── tmp/                           # Temporary working files
├── pyproject.toml                 # Project metadata & tool config
├── requirements.txt               # Python dependencies
├── uv.lock                        # uv lockfile
├── REFACTORING_SPEC.md            # Refactoring specification
└── README.md                      # This file
```

---

## Binary Management

`llama-orchestrator` manages `llama-server` binaries as versioned local
artifacts. Each installation receives a UUID and is recorded in
`bins/registry.json`, so multiple llama.cpp versions and variants can coexist
without relying on one shared `bin/llama-server.exe`.

### Storage Layout

```
llama-orchestrator/
├── bins/
│   ├── registry.json
│   └── <uuid>/
│       ├── llama-server.exe
│       ├── *.dll
│       └── version.json
└── bin/
    └── llama-server.exe          ← legacy fallback
```

### Registry Contract

`bins/registry.json` stores:

- `schema_version`
- `default_binary_id`
- One record per installed binary with `id`, `version`, `variant`,
  `download_url`, `sha256`, `installed_at`, `path`, `size_bytes`,
  `executables`, and optional GitHub release metadata

The UUID in `id` is the primary key. Version and variant are supplementary
metadata and fallback lookup hints.

### Instance Config Resolution Order

1. `binary.binary_id` resolves through `bins/registry.json`
2. `binary.version` + `binary.variant` as fallback lookup
3. `bins/default_binary_id` as default
4. Legacy `bin/llama-server.exe` as backward-compatible fallback

---

## Health Monitoring

The health subsystem provides pluggable health probes with automatic restart:

| Module | Purpose |
|---|---|
| `health/probes.py` | HTTP/TCP/custom probe implementations |
| `health/checker.py` | Orchestrates check cycles across instances |
| `health/monitor.py` | Background monitor loop with configurable intervals |
| `health/backoff.py` | Exponential backoff with jitter for restart delays |
| `health/client_pool.py` | Reusable `httpx.Client` pool (avoids per-call overhead) |

### Configurable Policies

- HTTP health path override
- TCP port probing
- Custom probe commands
- Retry count and timeout settings
- Jittered restart delay

---

## Desktop GUI

The Tkinter-based GUI provides a Windows management panel for:

- **Model table** — Treeview with sortable columns showing all instances
- **Tag filtering** — Filter instances by custom tags
- **Batch actions** — Start/stop/restart multiple instances at once
- **Inline runtime args editing** — Edit `llama-server` flags per instance
- **Benchmark controls** — Quick, serial, and grid benchmark workflows
- **GPU inventory panel** — Detected GPUs with editable aliases
- **Activity log** — Real-time action history
- **Keyboard shortcuts** — `Ctrl+S` (start), `Ctrl+R` (restart), `Ctrl+T` (stop)

### Performance Optimizations (V2)

- **Background refresh** — `RefreshController` runs snapshots on a background thread
- **Row-level diffing** — Only changed columns are updated in the Treeview
- **Debounced GPU inventory** — Cached for 60s, rebuilt only on change
- **Health client pooling** — Single `httpx.Client` shared across checks

---

## Benchmarking

### Workflow Types

| Type | Description |
|---|---|
| **Quick** | Single-prompt benchmark for fast throughput measurement |
| **Serial** | Sequential prompt execution for latency profiling |
| **Grid** | Multi-parameter grid search across temperature, top-p, context sizes |
| **Prompt** | Custom prompt selection with user-defined inputs |

### Telemetry Metrics

- **TTFT** (Time to First Token)
- **Generation throughput** (tokens/second)
- **End-to-end throughput**
- **Cache reuse** ratios
- **Speculative/draft acceptance** rates
- **Best-effort VRAM/memory** reporting

---

## Daemon & Service

The background daemon provides:

- **Health monitor loop** — Checks all running instances at configurable intervals
- **Auto-restart** — Exponential backoff on failure detection
- **Cooperative stop** — Graceful shutdown on interrupt
- **File-based rotating logs** — `daemon.log` with log rotation

### Windows Service Integration

```powershell
# Install as Windows service
llama-orch daemon install

# Uninstall Windows service
llama-orch daemon uninstall
```

Requires `nssm.exe` in `PATH`.

---

## PowerShell Automation

Scripts in `scripts/` provide Windows automation:

| Script | Purpose |
|---|---|
| `Install-AutostartTask.ps1` | Create Windows Task Scheduler autostart entries |
| `install-service.ps1` | NSSM service installation helper |
| `llama.ps1` | Legacy llama.cpp wrapper |
| `Start-Autostart.ps1` | Trigger autostart on session login |

---

## Testing

```powershell
# Run all tests
pytest

# Run with coverage
pytest --cov=llama_orchestrator --cov-report=term-missing

# Run specific test file
pytest tests/test_engine.py -v
```

Test configuration in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --cov=llama_orchestrator --cov-report=term-missing"
```

---

## Development Tools

| Tool | Configuration |
|---|---|
| **Ruff** | `line-length = 100`, selects `E,F,I,N,W,UP,B,C4,SIM` |
| **mypy** | Strict mode, Python 3.11, `pydantic.mypy` plugin |
| **pytest** | Auto asyncio, coverage reporting |

---

## Documentation

| File | Purpose |
|---|---|
| `REFACTORING_SPEC.md` | Refactoring specification with workstream breakdown |
| `docs/BINARY_MANAGEMENT.md` | Binary management guide |
| `docs/*.md` | Implementation plans, checklists, specs |

---

## Dependencies

### Core

```
pydantic>=2.5          # Data validation schemas
typer[all]>=0.9       # CLI framework
rich>=13.7             # Terminal rendering
httpx>=0.25            # HTTP client (health probes, benchmark)
psutil>=5.9            # Process monitoring
aiosqlite>=0.19        # Async SQLite
huggingface_hub>=0.32  # HuggingFace model import
keyring>=25.6          # Credential storage
```

### Development

```
pytest>=7.4            # Test framework
pytest-asyncio>=0.21   # Async test support
pytest-cov>=4.1        # Coverage reporting
ruff>=0.1              # Linter
mypy>=1.7              # Type checking
```

---

## Exit Code Reference

| Code Range | Meaning | Examples |
|---|---|---|
| `2` | Usage / argument errors | Missing required args |
| `10–19` | Configuration errors | Invalid JSON, missing model path |
| `20–39` | Instance / process errors | Port collision, process start failure |
| `50–69` | Binary / daemon errors | Binary download failure, service install error |

---

## Quick Reference Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Operator Workflow                       │
│                                                                 │
│  1. llama-orch init <name> --model <path> --port <port>       │
│     └─► Creates instances/<name>/config.json                   │
│                                                                 │
│  2. llama-orch up <name>                                      │
│     └─► Resolves binary from bins/registry.json               │
│     └─► Spawns llama-server process on configured port         │
│     └─► Registers state in state.sqlite                        │
│     └─► Health monitor begins probing                         │
│                                                                 │
│  3. llama-orch ps / dashboard / gui                           │
│     └─► Reads runtime state + live health probes              │
│                                                                 │
│  4. llama-orch down <name>                                    │
│     └─► Graceful process termination                          │
│     └─► Updates state.sqlite                                  │
│                                                                 │
│  5. llama-orch binary install <version>                       │
│     └─► Downloads from GitHub releases                        │
│     └─► Extracts to bins/<uuid>/                              │
│     └─► Updates registry.json                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Related Projects

| Project | Relationship |
|---|---|
| `infra-local/codex-local-delegation-mcp/` | MCP delegation layer that may consume orchestrator HTTP APIs |
| `infra-local/llm-eval-lab/` | LLM evaluation lab that consumes benchmark outputs |
| `agent-platforms/openclaw-docker/` | Agent platform that may orchestrate llama-orchestrator instances |
| `website_michaelprinc/` | WordPress site that may serve model endpoints from orchestrator |

---

## License

MIT License — see LICENSE file.

---

**Version:** 2.1.0  
**Last Updated:** 2026-07-06  
**Author:** MichaelPrinc
    "--reasoning", "off",
    "--flash-attn", "auto"
  ],
  "parameter_mutability": {
    "static": [
      "name",
      "binary.binary_id",
      "binary.version",
      "binary.variant",
      "binary.source_url",
      "binary.sha256",
      "model.path",
      "model.context_size",
      "model.batch_size",
      "model.threads",
      "server.host",
      "server.port",
      "server.timeout",
      "server.parallel",
      "gpu.backend",
      "gpu.device_id",
      "gpu.layers",
      "env",
      "args",
      "logs.stdout",
      "logs.stderr",
      "logs.max_size_mb",
      "logs.rotation"
    ],
    "dynamic": [
      "tags",
      "healthcheck.type",
      "healthcheck.path",
      "healthcheck.expected_status",
      "healthcheck.expected_body",
      "healthcheck.custom_script",
      "healthcheck.interval",
      "healthcheck.timeout",
      "healthcheck.retries",
      "healthcheck.retry_delay",
      "healthcheck.start_period",
      "healthcheck.backoff_enabled",
      "healthcheck.backoff_base",
      "healthcheck.backoff_max",
      "healthcheck.backoff_jitter",
      "restart_policy.enabled",
      "restart_policy.max_retries",
      "restart_policy.backoff_multiplier",
      "restart_policy.initial_delay",
      "restart_policy.max_delay"
    ]
  },
  "tags": ["router", "vulkan"],
  "healthcheck": {
    "type": "http",
    "path": "/health",
    "expected_status": [200],
    "interval": 10,
    "timeout": 5,
    "retries": 3,
    "retry_delay": 1.0,
    "start_period": 60,
    "backoff_enabled": true,
    "backoff_base": 1.0,
    "backoff_max": 60.0,
    "backoff_jitter": 0.1
  },
  "restart_policy": {
    "enabled": true,
    "max_retries": 5,
    "backoff_multiplier": 2.0,
    "initial_delay": 1.0,
    "max_delay": 300.0
  },
  "logs": {
    "stdout": "logs/gpt-oss/stdout.log",
    "stderr": "logs/gpt-oss/stderr.log",
    "max_size_mb": 100,
    "rotation": 5
  }
}
```

The `binary.binary_id` UUID is the primary join into `bins/registry.json`.
If it is missing, the resolver can fall back to `version` plus `variant`. If
the whole `binary` section is absent, legacy `bin/llama-server.exe` resolution
is still supported for older configs.

The persisted `parameter_mutability` section makes restart semantics explicit:
`static` paths require a llama.cpp process restart when changed, while
`dynamic` paths describe control-plane tunables such as health checks and
restart policy. In `2.1.0` this is a configuration contract only; GUI behavior
and live-reload semantics are unchanged.

`llama-orch config validate` emits a warning when `server.host` is `0.0.0.0`
so remote-access configurations remain possible without making broad exposure
look risk-free.

## Directory Structure

```
llama-orchestrator/
├── bins/                     # Versioned llama.cpp binaries
│   ├── registry.json         # UUID registry and default binary pointer
│   └── <uuid>/               # Installed package with llama-server.exe and DLLs
├── bin/llama-server.exe      # Legacy fallback path
├── instances/                 # Instance configurations
│   └── <name>/config.json
├── state/state.sqlite        # Runtime state
├── state/benchmark_history.sqlite
├── state/benchmark_settings.json
├── benchmarks/prompts/default.txt
├── logs/<name>/              # Instance logs
│   ├── stdout.log
│   ├── stderr.log
│   └── benchmarks/*.md
└── src/llama_orchestrator/   # Python package
```

## Requirements

- Python 3.11+
- Windows 10/11
- llama.cpp server binary (Vulkan/CPU)
- AMD GPU with Vulkan support (optional)

## Windows Autostart

The project can start automatically after Windows boots by registering a Task
Scheduler task. The scheduled task runs `scripts/Start-Autostart.ps1`, which
starts the orchestrator daemon and writes audit entries to
`logs/autostart-audit.log`.

```powershell
# Run from the llama-orchestrator project root.
# AtStartup usually requires an elevated PowerShell session.
.\scripts\Install-AutostartTask.ps1 -Trigger AtStartup

# Start the daemon and all configured model instances at user logon.
.\scripts\Install-AutostartTask.ps1 -Trigger AtLogOn -StartInstances

# Start only selected model instances.
.\scripts\Install-AutostartTask.ps1 -Trigger AtLogOn -StartInstances -InstanceNames gpt-oss

# Remove the scheduled task.
.\scripts\Install-AutostartTask.ps1 -Uninstall
```

Both install and bootstrap scripts support `-Verbose` and `-WhatIf`.

## Windows Service

If `nssm.exe` is available in `PATH`, the daemon can be installed as a Windows service directly from the CLI:

```powershell
llama-orch daemon install
llama-orch daemon status
llama-orch daemon stop
llama-orch daemon uninstall

# Custom service name
llama-orch daemon install --service-name llama-orch-dev
```

Run service installation from an elevated PowerShell session and make sure
`nssm.exe` is available in `PATH`. The service entry point runs the
orchestrator daemon in foreground mode and writes daemon stdout/stderr logs
under `logs/daemon/`. Manual Windows Services UI smoke verification is still a
tracked operational check; CLI install/uninstall coverage exists, but service
start/stop should be verified on the target Windows host before relying on it
for unattended operation.

## Desktop GUI

Launch the desktop management UI with:

```powershell
llama-orch gui
# or
.\scripts\llama.ps1 gui
```

The GUI supports:

- Viewing configured model instances with status, health, PID, port, backend,
  effective GPU label, CPU-use indicator, model size, model path, runtime args,
  tags, benchmark TPS, first-token latency, VRAM MB, prompt file, and uptime.
- Starting, stopping, restarting, and health-checking selected instances.
- Starting and stopping the orchestrator daemon.
- Adding a new GGUF-backed model instance config.
- Importing GGUF variants directly from Hugging Face inside `Add model`, with
  repo URL or `owner/repo` normalization, GGUF variant discovery, background
  download progress, cancel support, existing-file reuse or re-download
  handling, GGUF metadata validation, sidecar metadata writing, and Add Model
  autofill after the selected artifact is ready.
- Managing these llama-server args for new or selected instances:
  `--no-mmproj --reasoning off --flash-attn auto`.
- Installing a `llama-server.exe` binary from GitHub releases with
  `win-vulkan-x64` selected by default. The toolbar action is named
  `Install llama-server` because the installer supports CPU, Vulkan, CUDA,
  HIP/Radeon, and SYCL variants.
- Opening instance config files, log folders, and the project folder.
- Choosing visible table columns from the `Columns` menu, persisted across GUI restarts.
- Filtering by instance tags and applying batch actions to visible rows.
- Sorting the table by clicking column headers with stable primary/secondary ordering and a `Reset GUI` button that clears active sorting.
- Running `Quick benchmark` from the detail bar or row context menu, with the
  compact `Params` menu for endpoint, max tokens, temperature, top-p, top-k,
  repeat penalty, seed, ignore-EOS, and opening the persisted settings file.
- Running `Grid benchmark` from the selected row or queued rows. The dialog
  separates dynamic request parameters from restart-required runtime/model
  parameters, shows current/default values, supports `minimum`, `maximum`, and
  `step` for numeric rows, can save/reload the grid configuration, previews the
  combination count, runs combinations serially in the background, restarts the
  model between restart-required runtime combinations, and supports `Stop grid`
  between combinations.
- Marking rows in the `Queue` column and running `Serial benchmark` in current
  visible table order, one model at a time, with row highlighting, progress
  logs, automatic start/stop for rows that were not already running, and
  `Stop queue` to stop before the next queued run starts.
- Selecting and opening the editable benchmark prompt with
  `Edit Benchmark Prompt` and `Open prompt`.
- Cloning a row with an incremented/suggested port.
- Diffing runtime args for two selected rows.
- Copying a selected instance launch command with `Copy CLI`.
- Editing the `Runtime args` cell inline; saving restarts the instance when it
  is already running.
- Showing a hideable `Detected GPUs` panel with one device per line, using
  labels such as `Vulkan0` plus adapter names from the current Vulkan loader
  when available, with llama.cpp stderr as a fallback. Adapter aliases can be
  edited from this panel and are shown in the table `GPU` column in place of
  volatile labels such as `Vulkan0`.

GUI status display intentionally separates engine state from readiness:
`running + loading` is shown as `loading`, while `running + healthy` is shown
as `ready`. The underlying runtime status remains `running`.

Persisted GUI-observed state currently includes the selected benchmark prompt
and quick benchmark parameters (`state/benchmark_settings.json`) plus manual
health/benchmark health updates in the runtime state and `health_history`.
The main table also persists visible columns and primary/secondary sort order in
`state/gui_settings.json`. The Hugging Face import dialog persists the chosen
local models directory in `state/huggingface_import.json`; any Hugging Face
read token is stored through the system keyring when available and otherwise is
kept session-only instead of being written to repo files. Imported GGUF files
also get an additive `<model>.gguf.metadata.json` sidecar with source, extracted
GGUF facts, best-effort model-card claims, and validation warnings. Serial
benchmark queue checkmarks are session-only; tag filter and window geometry
still reset on GUI launch.

### Runtime Detection and GPU Mapping

- The GUI shows effective runtime selection, not just raw config values.
- `GPU` is derived from the final command and merged environment variables.
  Explicit main runtime device flags such as `--device` win first, then
  backend device env vars, then `gpu.device_id` when no stronger signal exists.
  Today the env-driven device mapping is treated as authoritative for Vulkan via
  `GGML_VULKAN_DEVICE`. `--device-draft` contributes additional active adapters
  to the GUI display but does not replace the primary runtime GPU.
- `CPU` shows a checkmark when inference resolves to CPU execution, either
  because the backend is `cpu` or because the effective `--n-gpu-layers`
  resolves to `0`.
- `Runtime args` remain unchanged by this feature. The GUI derives display
  metadata from the effective runtime but does not rewrite config values to
  make the display cleaner.
- `Model size` is the resolved GGUF file size shown in base-1024 `GB`, matching
  the project's RAM/VRAM normalization convention.
- The `Detected GPUs` summary first probes the current Vulkan loader with
  `vulkaninfo --summary`, so labels such as `Vulkan0`, `Vulkan1`, and `Vulkan2`
  follow the active driver ordering instead of stale historical logs.
- If `vulkaninfo` is unavailable or does not expose a name, the summary falls
  back to llama.cpp stderr inventory lines such as `Vulkan0 : Adapter Name (...)`
  and active-device lines such as `using device Vulkan1 (Adapter Name)`.
- If the GPU label is known but no live or log-derived adapter name is available,
  the summary keeps the label and shows `adapter name unavailable`.
- GPU aliases persist in `state/gpu_aliases.json` keyed by adapter name, not by
  `VulkanN` label. When driver ordering changes after a reboot, the GUI reapplies
  the alias to whichever current `VulkanN` label reports that adapter name.
  Alias values are limited to 10 characters to fit the fixed-width alias button.
- Quick benchmark fallback sampling uses the same effective runtime resolver and
  samples the primary resolved device when a multi-GPU config declares one.

### Quick Benchmark and VRAM

The default benchmark prompt lives at `benchmarks/prompts/default.txt`.
Benchmark settings persist to `state/benchmark_settings.json`; benchmark
attempts append to `state/benchmark_history.sqlite` with prompt file, prompt
SHA256, output token count, TPS, latency, dedicated VRAM MB, shared RAM MB,
total sampled GPU memory MB, config hash, status, error text, and the
corresponding artifact path.
The `Params` menu next to `Quick benchmark` edits the persisted sampling
controls without expanding the detail bar; blank optional values use the
llama.cpp server defaults. The default endpoint is `/v1/chat/completions` so
chat templates and generation prompts are applied for instruction-tuned models;
the legacy `/completion` endpoint remains selectable.
Every quick benchmark also writes a Markdown artifact under
`logs/<instance>/benchmarks/` with the full prompt text, model output,
request parameters, benchmark settings, final stream payload, and the same
summary metrics stored in SQLite.

`Grid benchmark` adds sweep history to the same SQLite database using additive
`benchmark_sweeps` and `benchmark_runs` tables. Each run stores the concrete
`parameters_json`, status, metrics JSON, error text, and the quick benchmark
artifact path when available. A per-sweep Markdown summary is written under
`logs/<instance>/benchmarks/grid/<sweep_id>/summary.md`.

Grid dialog configuration persists to `state/grid_benchmark_settings.json`.
Runtime-static parameters such as context size, batch size, GPU layers, KV cache
type, flash attention, and speculative decoding run through a temporary
in-memory config. The runner restarts the model before each restart-required
combination and restores the original runtime config after the sweep when the
instance was already running. It does not mutate persisted
`instances/*/config.json` files.

`Quick benchmark` requires the selected instance to expose a live llama.cpp
HTTP endpoint. GPU memory reporting is best-effort and prefers
process-scoped Windows GPU counters when available so the GUI can show total
sampled GPU memory plus any shared RAM used by the benchmarked process. If
shared RAM is non-zero, the GUI warns that inference may be slower.

If process-scoped counters are unavailable, vendor CLI tools (`nvidia-smi`,
`amd-smi`, `rocm-smi`) are sampled for dedicated VRAM only, then the benchmark
falls back to parsing the instance `stderr.log`. The fallback prioritizes
logged Vulkan model buffer size and can estimate `total - free` for the
configured device. Shared RAM is left unknown in these fallback paths rather
than guessed, so missing split memory data is reported neutrally.

Measured benchmark memory remains distinct from the config-derived estimate shown
by `llama-orch describe`. The benchmark path reports observed runtime memory,
while the describe estimate is a preflight heuristic based on model metadata,
effective runtime flags, and an optional dedicated-VRAM budget inferred from
prior device inventory lines.

Historical benchmark rows that only stored `vram_mb` remain readable. The GUI
derives total memory from the legacy value and omits the shared RAM warning
unless shared usage was positively observed.


## Development

```powershell
# Clone and setup
git clone <repo>
cd llama-orchestrator
uv sync

# Run tests
uv run pytest

# Focus the current GUI/benchmark slice
uv run pytest tests/test_gui.py tests/test_detection.py tests/test_benchmark.py -v --no-cov

# Lint touched GUI/benchmark files
uv run ruff check src\llama_orchestrator\benchmark.py src\llama_orchestrator\engine\detection.py src\llama_orchestrator\gui.py tests\test_benchmark.py tests\test_detection.py tests\test_gui.py

# Run in dev mode
python -m llama_orchestrator --help
```

If running `pytest` outside `uv`, set `PYTHONPATH=src` first. Repository-wide
Ruff may still report older pre-existing style issues; the May 2026 GUI and
benchmark changes were validated with Ruff scoped to touched files.

## Documentation

### V2 Upgrade (2026)

- [V2 Implementation Plan](docs/LLAMA_ORCH_V2_IMPLEMENTATION_PLAN.md) - Comprehensive upgrade plan
- [V2 Checklist](docs/LLAMA_ORCH_V2_CHECKLIST.md) - Detailed task tracking
- [V2 Dependency Map](docs/LLAMA_ORCH_V2_DEPENDENCY_MAP.md) - Module dependency graph
- [V2 Risk Register](docs/LLAMA_ORCH_V2_RISK_REGISTER.md) - Risk assessment and mitigation
- [Binary Management](docs/BINARY_MANAGEMENT.md) - Versioned llama.cpp binary registry and CLI workflows

### Recent Implementation Reports

- [Runtime detection and hardware display](../../reports/implementation/infra-local/llama-orchestrator/2026/20260525-llama-orchestrator-runtime-detection-display-report.md)
- [Benchmark artifact history](../../reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-benchmark-artifact-history.md)
- [Benchmark settings and artifact location](../../reports/implementation/infra-local/llama-orchestrator/2026/20260523-llama-orchestrator-benchmark-settings-artifact-location.md)
- [V2 implementation report](../../reports/20260516_llama-orchestrator-v2-implementation-report.md)
- [V2 README current-state audit](../../reports/implementation/infra-local/llama-orchestrator/2026/20260516-llama-orchestrator-v2-readme-current-state-audit.md)
- [Documentation refresh](../../reports/implementation/infra-local/llama-orchestrator/2026/20260516-llama-orchestrator-documentation-refresh.md)
- [Benchmark GUI improvements](../../reports/implementation/infra-local/llama-orchestrator/2026/20260516-llama-orchestrator-benchmark-gui-improvements.md)
- [GUI state and VRAM corrections](../../reports/implementation/infra-local/llama-orchestrator/2026/20260516-llama-orchestrator-gui-state-vram-corrections.md)
- [GUI install label update](../../reports/implementation/infra-local/llama-orchestrator/2026/20260516-llama-orchestrator-gui-install-label.md)
- [Routing classification consolidated results](../../reports/implementation/infra-local/llama-orchestrator/2026/20260509-routing-classification-consolidated-results.md)

### Original Documentation

- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Implementation Checklist](docs/CHECKLIST.md)

## License

MIT
