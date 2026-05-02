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
| `llama-orch logs <name>` | View instance logs |
| `llama-orch describe <name>` | Show full config + status |
| `llama-orch dashboard` | Live TUI dashboard |
| `llama-orch config validate` | Validate configuration |
| `llama-orch daemon start` | Start background daemon |

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
