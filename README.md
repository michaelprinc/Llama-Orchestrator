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
| `llama-orch daemon start` | Start background daemon |
| `llama-orch daemon install` | Install the daemon as a Windows service via NSSM |
| `llama-orch daemon uninstall` | Remove the Windows service |

## CLI Notes

- `llama-orch up <name> --no-detach` keeps the server attached to the current terminal.
- `llama-orch logs <name> --stream both` shows merged stdout and stderr output.
- `llama-orch dashboard --events-for <name>` filters the recent-events panel to one instance.
- Commands return standard exit codes for automation: `2` usage, `10-19` config, `20-39` instance/process, `50-69` binary/daemon.

## Configuration

Instance configs are stored in `instances/<name>/config.json`:

```json
{
  "name": "gpt-oss",
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
  "healthcheck": {
    "interval": 10,
    "timeout": 5,
    "retries": 3
  },
  "restart_policy": {
    "enabled": true,
    "max_retries": 5
  }
}
```

## Directory Structure

```
llama-orchestrator/
├── bin/llama-server.exe      # llama.cpp binary
├── instances/                 # Instance configurations
│   └── <name>/config.json
├── state/state.sqlite        # Runtime state
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
llama-orch daemon uninstall

# Custom service name
llama-orch daemon install --service-name llama-orch-dev
```

The service entry point runs the orchestrator daemon in foreground mode and writes daemon stdout/stderr logs under `logs/daemon/`.

## Desktop GUI

Launch the desktop management UI with:

```powershell
llama-orch gui
# or
.\scripts\llama.ps1 gui
```

The GUI supports:

- Viewing configured model instances with status, health, PID, port, backend,
  model path, runtime args, and uptime.
- Starting, stopping, restarting, and health-checking selected instances.
- Starting and stopping the orchestrator daemon.
- Adding a new GGUF-backed model instance config.
- Managing these llama-server args for new or selected instances:
  `--no-mmproj --reasoning off --flash-attn auto`.
- Installing a `llama-server.exe` binary from GitHub releases with
  `win-vulkan-x64` selected by default.
- Opening instance config files, log folders, and the project folder.

## Development

```powershell
# Clone and setup
git clone <repo>
cd llama-orchestrator
uv sync

# Run tests
pytest

# Run in dev mode
python -m llama_orchestrator --help
```

## Documentation

### V2 Upgrade (2026)

- [V2 Implementation Plan](docs/LLAMA_ORCH_V2_IMPLEMENTATION_PLAN.md) - Comprehensive upgrade plan
- [V2 Checklist](docs/LLAMA_ORCH_V2_CHECKLIST.md) - Detailed task tracking
- [V2 Dependency Map](docs/LLAMA_ORCH_V2_DEPENDENCY_MAP.md) - Module dependency graph
- [V2 Risk Register](docs/LLAMA_ORCH_V2_RISK_REGISTER.md) - Risk assessment and mitigation

### Original Documentation

- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Implementation Checklist](docs/CHECKLIST.md)

## License

MIT
