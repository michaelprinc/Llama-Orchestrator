# llama-orchestrator

> Docker-like CLI orchestration for llama.cpp server instances on Windows

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Overview

**llama-orchestrator** is a Python-based control plane for managing multiple llama.cpp server instances. It provides:

- 🚀 **Multi-instance support** — Run multiple models on different ports
- 🔄 **Health monitoring** — Automatic health checks with configurable policies
- ♻️ **Auto-restart** — Intelligent restart on failure with exponential backoff
- 📊 **TUI Dashboard** — Live terminal dashboard showing all instances
- 🪟 **Windows native** — Task Scheduler / NSSM service integration

## Local Version Status

**Primary supported tool version:** `llama-orchestrator` `2.0.0`

Use this local checkout at `infra-local/llama-orchestrator/` as the main
version of the tool for this workspace. It is the preferred implementation for
local llama.cpp orchestration, Windows autostart, GUI usage, daemon operation,
health checks, and versioned llama.cpp binary management.

When this workspace also contains older planning notes or upstream package
copies, treat this directory and its `pyproject.toml` version as authoritative
unless a newer migration document explicitly supersedes it.

## Current V2 Status

The V2 proposal has been implemented in this local checkout for the core
orchestration surface:

- Process and state reliability: SQLite V2 schema, runtime state, event log,
  process validation, per-instance locking, stale-state reconciliation, and
  port collision checks.
- Daemon and logging reliability: file-based rotating logs, `logs -f`, an
  interruptible daemon loop, cooperative daemon stop, and NSSM-backed Windows
  service install/uninstall commands.
- Health and restart behavior: configurable HTTP/TCP/custom probes, custom
  health paths, retry/backoff settings, and jittered restart delays.
- CLI and dashboard UX: standard exit codes, richer `describe` output, recent
  events in the dashboard, and explicit detached/attached start behavior.
- Desktop GUI and benchmark workflows: model table management, tag filtering,
  batch actions, inline runtime args editing, quick benchmark history, prompt
  selection, `loading` versus `ready` display semantics, and best-effort VRAM
  reporting.
- Binary management: versioned `llama-server` packages under `bins/`, a UUID
  registry in `bins/registry.json`, per-instance binary pinning, and GUI/CLI
  install/list/info/remove/latest workflows.

Known remaining gaps are operational or follow-up items, not blockers for the
local V2 command surface: manual Windows Services UI smoke testing still needs
to be run on a target host with `nssm.exe` in `PATH`; several binary-management
convenience commands remain listed as future work in `docs/BINARY_MANAGEMENT.md`;
and GUI column visibility, tag filter, and window geometry are intentionally
session-local today.

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

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CONTROL PLANE (Python)                   │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│  │   CLI   │───▶│ Daemon  │───▶│   TUI   │                 │
│  └─────────┘    └─────────┘    └─────────┘                 │
│       │              │              │                       │
│       └──────────────┼──────────────┘                       │
│                      ▼                                      │
│            ┌─────────────────┐                             │
│            │  State (SQLite) │                             │
│            └─────────────────┘                             │
└─────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATA PLANE (llama.cpp)                   │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│  │ :8001   │    │ :8002   │    │ :8003   │                 │
│  │ model-A │    │ model-B │    │ model-C │                 │
│  └─────────┘    └─────────┘    └─────────┘                 │
└─────────────────────────────────────────────────────────────┘
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `llama-orch up <name>` | Start an instance |
| `llama-orch down <name>` | Stop an instance |
| `llama-orch restart <name>` | Restart an instance |
| `llama-orch ps` | List all instances |
| `llama-orch health <name>` | Check instance health |
| `llama-orch logs <name>` | View stdout, stderr, or merged logs |
| `llama-orch describe <name>` | Show config, runtime, memory, events, and health history |
| `llama-orch dashboard` | Live TUI dashboard with recent events panel |
| `llama-orch gui` | Windows desktop GUI for model management |
| `llama-orch config validate` | Validate configuration |
| `llama-orch config lint` | Validate all discovered instance configs |
| `llama-orch daemon start` | Start background daemon |
| `llama-orch daemon status` | Show daemon status |
| `llama-orch daemon stop` | Stop background daemon |
| `llama-orch daemon install` | Install the daemon as a Windows service via NSSM |
| `llama-orch daemon uninstall` | Remove the Windows service |
| `llama-orch binary install [version]` | Install a llama.cpp `llama-server` package from GitHub releases |
| `llama-orch binary list` | List installed versioned binaries |
| `llama-orch binary info <uuid>` | Show metadata for an installed binary |
| `llama-orch binary remove <uuid>` | Remove an installed binary after confirmation |
| `llama-orch binary latest` | Show the latest available llama.cpp release |

## CLI Notes

- `llama-orch up <name> --no-detach` keeps the server attached to the current terminal.
- `llama-orch logs <name> --stream both` shows merged stdout and stderr output.
- `llama-orch dashboard --events-for <name>` filters the recent-events panel to one instance.
- `llama-orch binary remove <uuid>` prompts before deleting the binary directory; use `--force` only for scripted cleanup.
- Commands return standard exit codes for automation: `2` usage, `10-19` config, `20-39` instance/process, `50-69` binary/daemon.

## Configuration

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
    "--reasoning", "off",
    "--flash-attn", "auto"
  ],
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
│   └── stderr.log
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
  model path, runtime args, tags, benchmark TPS, first-token latency, VRAM MB,
  prompt file, and uptime.
- Starting, stopping, restarting, and health-checking selected instances.
- Starting and stopping the orchestrator daemon.
- Adding a new GGUF-backed model instance config.
- Managing these llama-server args for new or selected instances:
  `--no-mmproj --reasoning off --flash-attn auto`.
- Installing a `llama-server.exe` binary from GitHub releases with
  `win-vulkan-x64` selected by default. The toolbar action is named
  `Install llama-server` because the installer supports CPU, Vulkan, CUDA,
  HIP/Radeon, and SYCL variants.
- Opening instance config files, log folders, and the project folder.
- Choosing visible table columns from the `Columns` menu.
- Filtering by instance tags and applying batch actions to visible rows.
- Running `Quick benchmark` from the detail bar or row context menu.
- Selecting and opening the editable benchmark prompt with
  `Edit Benchmark Prompt` and `Open prompt`.
- Cloning a row with an incremented/suggested port.
- Diffing runtime args for two selected rows.
- Copying a selected instance launch command with `Copy CLI`.
- Editing the `Runtime args` cell inline; saving restarts the instance when it
  is already running.

GUI status display intentionally separates engine state from readiness:
`running + loading` is shown as `loading`, while `running + healthy` is shown
as `ready`. The underlying runtime status remains `running`.

Persisted GUI-observed state currently includes the selected benchmark prompt
(`state/benchmark_settings.json`) and manual health/benchmark health updates in
the runtime state and `health_history`. Column visibility, tag filter, and
window geometry reset on GUI launch.

### Quick Benchmark and VRAM

The default benchmark prompt lives at `benchmarks/prompts/default.txt`.
Benchmark settings persist to `state/benchmark_settings.json`; benchmark
attempts append to `state/benchmark_history.sqlite` with prompt file, prompt
SHA256, output token count, TPS, latency, VRAM MB, config hash, status, and
error text.

`Quick benchmark` requires the selected instance to expose a live llama.cpp
`/completion` endpoint. VRAM is best-effort: vendor CLI tools
(`nvidia-smi`, `amd-smi`, `rocm-smi`) are sampled first, then the benchmark
falls back to parsing the instance `stderr.log`. The fallback prioritizes
logged Vulkan model buffer size and can estimate `total - free` for the
configured device. This depends on llama.cpp log format stability, so missing
VRAM data is reported as `-`.

## Development

```powershell
# Clone and setup
git clone <repo>
cd llama-orchestrator
uv sync

# Run tests
uv run pytest

# Focus the current GUI/benchmark slice
uv run pytest tests/test_gui.py tests/test_benchmark.py -v --no-cov

# Lint touched GUI/benchmark files
uv run ruff check src\llama_orchestrator\benchmark.py src\llama_orchestrator\gui.py tests\test_benchmark.py tests\test_gui.py

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
