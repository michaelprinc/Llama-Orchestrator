# llama-orchestrator — Implementation Plan

> **Generated:** 2024-12-27  
> **Complexity:** Critical (multi-module, new service, Windows service integration)  
> **Estimated Effort:** 40-60 hours (phased delivery)  
> **Author:** GitHub Copilot (Implementation Planner Profile)

---

## 1. Executive Summary

**llama-orchestrator** je Python-based control plane pro správu více běžících instancí llama.cpp serveru. Poskytuje Docker-like UX bez Dockeru — umožňuje spouštět, zastavovat, monitorovat a automaticky restartovat LLM inference servery na Windows.

### Klíčové vlastnosti

| Feature | Popis |
|---------|-------|
| **Multi-instance** | Více llama-server procesů na různých portech |
| **Health monitoring** | GET /health polling s auto-restart policy |
| **CLI + Daemon** | Interaktivní příkazy + background monitoring |
| **TUI Dashboard** | Live tabulka stavu instancí (rich) |
| **Windows Autostart** | Task Scheduler / NSSM integrace |
| **GPU Backend Support** | CPU, Vulkan (AMD), CUDA (budoucnost) |

---

## 2. Current State Snapshot

### Existující infrastruktura

```
llama-cpp-server/
├── bin/llama-server.exe     # Binární soubor llama.cpp
├── config.json              # Ukázková konfigurace (JSON)
├── start-server.ps1         # PowerShell launcher (266 řádků)
├── test-api.ps1             # API testy (146 řádků)
└── README.md
```

### Aktuální konfigurace (config.json)

```json
{
  "server": { "host": "127.0.0.1", "port": 8001, "timeout": 600 },
  "model": { 
    "path": "../models/gpt-oss-20b-Q4_K_S.gguf",
    "context_size": 4096, "batch_size": 512, 
    "threads": 16, "gpu_layers": 30 
  },
  "gpu": { "backend": "vulkan", "device_id": 1 }
}
```

### Omezení současného řešení

1. **Single instance** — pouze jeden server najednou
2. **Manual start** — žádný daemon, nutno spustit ručně
3. **No monitoring** — žádný health check, auto-restart
4. **PowerShell-only** — obtížná rozšiřitelnost

---

## 3. Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE (Python)                         │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  llama-orch  │    │   Daemon     │    │  TUI/CLI     │                  │
│  │     CLI      │───▶│  (Monitor)   │───▶│  Dashboard   │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                           │
│         ▼                   ▼                   ▼                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    State Manager (SQLite/JSON)                       │   │
│  │   • instances/<name>/config.json                                     │   │
│  │   • state/state.sqlite (PID, health, uptime)                        │   │
│  │   • logs/<name>/{stdout,stderr}.log                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA PLANE (llama.cpp)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │ llama-server │    │ llama-server │    │ llama-server │                  │
│  │  :8001       │    │  :8002       │    │  :8003       │                  │
│  │  (model-A)   │    │  (model-B)   │    │  (model-C)   │                  │
│  │  Vulkan GPU  │    │  CPU-only    │    │  Vulkan iGPU │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                           │
│     /health             /health             /health                         │
│     /v1/models          /v1/models          /v1/models                      │
│     /v1/chat/...        /v1/chat/...        /v1/chat/...                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Directory Structure

```
llama-orchestrator/
├── pyproject.toml                    # Projekt definice (uv/pip)
├── README.md                         # Dokumentace
├── docs/
│   ├── IMPLEMENTATION_PLAN.md        # Tento dokument
│   ├── CHECKLIST.md                  # Implementační checklist
│   └── USER_GUIDE.md                 # Uživatelská příručka
├── src/
│   └── llama_orchestrator/
│       ├── __init__.py
│       ├── __main__.py               # Entry point: python -m llama_orchestrator
│       ├── cli.py                    # Typer/Click CLI
│       ├── config/
│       │   ├── __init__.py
│       │   ├── schema.py             # Pydantic modely
│       │   └── validator.py          # Config validation
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── process.py            # Process start/stop/restart
│       │   ├── state.py              # State manager (SQLite)
│       │   └── health.py             # Health polling
│       ├── daemon/
│       │   ├── __init__.py
│       │   ├── monitor.py            # Background monitoring
│       │   └── scheduler.py          # Auto-restart policy
│       ├── dashboard/
│       │   ├── __init__.py
│       │   └── tui.py                # Rich TUI dashboard
│       └── utils/
│           ├── __init__.py
│           ├── logging.py            # Structured logging
│           └── gpu.py                # GPU detection
├── instances/                        # Instance configs (per-model)
│   └── example/
│       └── config.json
├── state/
│   └── state.sqlite                  # Runtime state
├── logs/
│   └── <instance>/
│       ├── stdout.log
│       └── stderr.log
├── bin/
│   └── llama-server.exe              # Symlink/copy
├── scripts/
│   ├── llama.ps1                     # PowerShell wrapper
│   └── install-service.ps1           # Windows service setup
└── tests/
    ├── test_config.py
    ├── test_engine.py
    └── test_health.py
```

---

## 5. Configuration Schema

### Instance Config (instances/<name>/config.json)

```json
{
  "$schema": "llama-instance-schema.json",
  "name": "gpt-oss-20b",
  "model": {
    "path": "../../models/gpt-oss-20b-Q4_K_S.gguf",
    "context_size": 4096,
    "batch_size": 512,
    "threads": 16
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8001,
    "timeout": 600,
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
  "args": [],
  "healthcheck": {
    "path": "/health",
    "interval": 10,
    "timeout": 5,
    "retries": 3,
    "start_period": 60
  },
  "restart_policy": {
    "enabled": true,
    "max_retries": 5,
    "backoff_multiplier": 2.0
  },
  "logs": {
    "stdout": "logs/{name}/stdout.log",
    "stderr": "logs/{name}/stderr.log",
    "max_size_mb": 100,
    "rotation": 5
  }
}
```

### Pydantic Schema (Python)

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from pathlib import Path

class ModelConfig(BaseModel):
    path: Path
    context_size: int = 4096
    batch_size: int = 512
    threads: int = Field(default=8, ge=1, le=128)

class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8001, ge=1024, le=65535)
    timeout: int = 600
    parallel: int = Field(default=1, ge=1, le=16)

class GpuConfig(BaseModel):
    backend: Literal["cpu", "vulkan", "cuda", "metal"] = "cpu"
    device_id: int = 0
    layers: int = Field(default=0, ge=0)

class HealthcheckConfig(BaseModel):
    path: str = "/health"
    interval: int = 10
    timeout: int = 5
    retries: int = 3
    start_period: int = 60

class RestartPolicy(BaseModel):
    enabled: bool = True
    max_retries: int = 5
    backoff_multiplier: float = 2.0

class InstanceConfig(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    model: ModelConfig
    server: ServerConfig
    gpu: GpuConfig = GpuConfig()
    env: dict[str, str] = {}
    args: list[str] = []
    healthcheck: HealthcheckConfig = HealthcheckConfig()
    restart_policy: RestartPolicy = RestartPolicy()
```

---

## 6. Implementation Phases

### Phase 0: Foundations (4-6 hours)

**Goal:** Project scaffolding, basic structure, dependencies

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| Create pyproject.toml | uv/pip compatible | `uv sync` works |
| Setup src layout | Package structure | Import works |
| Add dependencies | pydantic, typer, rich, httpx, psutil | No conflicts |
| Create __main__.py | Entry point | `python -m llama_orchestrator` |

**Dependencies:**
```toml
[project]
dependencies = [
    "pydantic>=2.0",
    "typer[all]>=0.9",
    "rich>=13.0",
    "httpx>=0.25",
    "psutil>=5.9",
]
```

---

### Phase 1: Configuration Model + Validation (6-8 hours)

**Goal:** Pydantic schemas, config loading, validation CLI

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| Define Pydantic models | schema.py | All fields validated |
| Implement config loader | Load from JSON | Handles missing fields |
| Port collision check | Validate unique ports | Raises on duplicate |
| Model path validation | Check file exists | Clear error message |
| CLI: `config validate` | Validate config file | Exit 0/1 appropriately |
| CLI: `config lint` | Full validation report | JSON/table output |

**Validations:**
- ✅ Model file exists and is readable
- ✅ Port not already in use
- ✅ Port unique across instances
- ✅ GPU device exists (optional)
- ✅ Log directory writable

---

### Phase 2: Process Engine (8-10 hours)

**Goal:** Start/stop/restart instances, state persistence

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| Process start | subprocess.Popen | Server starts |
| Build command args | From config | All params passed |
| Environment setup | GGML_VULKAN_DEVICE etc. | Env vars set |
| Stdout/stderr redirect | To log files | Logs written |
| State persistence | SQLite: PID, start_time | Survives restart |
| Process stop | Terminate + tree kill | Clean shutdown |
| Process restart | stop + start | Preserves config |
| CLI: `up <name>` | Start instance | Returns 0 on success |
| CLI: `down <name>` | Stop instance | Returns 0 on success |
| CLI: `restart <name>` | Restart instance | No downtime issues |

**State Schema (SQLite):**
```sql
CREATE TABLE instances (
    name TEXT PRIMARY KEY,
    pid INTEGER,
    start_time REAL,
    status TEXT,  -- 'running', 'stopped', 'loading', 'error'
    health TEXT,  -- 'healthy', 'unhealthy', 'unknown'
    last_health_check REAL,
    restart_count INTEGER DEFAULT 0,
    config_hash TEXT
);
```

---

### Phase 3: Health Monitoring (6-8 hours)

**Goal:** Health polling, status tracking, auto-restart

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| HTTP health check | GET /health | Handles 200/503/500 |
| Parse health response | status: ok/loading/error | Correct parsing |
| Fallback to /v1/health | If /health fails | Works |
| Health state machine | loading → ok → error | Correct transitions |
| Timeout handling | Configurable timeout | No hangs |
| CLI: `health <name>` | Show health status | Clear output |
| Auto-restart logic | On error/timeout | Respects policy |
| Backoff algorithm | Exponential backoff | Prevents thrashing |

**Health States:**
```
┌─────────┐     ┌─────────┐     ┌─────────┐
│ UNKNOWN │────▶│ LOADING │────▶│ HEALTHY │
└─────────┘     └─────────┘     └─────────┘
     │               │               │
     │               ▼               ▼
     │          ┌─────────┐     ┌─────────┐
     └─────────▶│  ERROR  │◀────│UNHEALTHY│
                └─────────┘     └─────────┘
```

---

### Phase 4: CLI + Dashboard (8-10 hours)

**Goal:** Full CLI, TUI dashboard, logging

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| CLI: `ps` | List instances | Table with status |
| CLI: `logs <name>` | Show logs | --tail, --follow |
| CLI: `describe <name>` | Full instance info | Config + runtime |
| TUI: Live table | rich.live | 1s refresh |
| TUI: Status colors | Green/Yellow/Red | Visual feedback |
| TUI: Keyboard shortcuts | q=quit, r=refresh | Responsive |

**Dashboard Columns:**
```
┌────────────┬───────┬──────┬─────────┬────────┬───────────────────┬──────────┬────────┐
│ Name       │ PID   │ Port │ Backend │ Device │ Model             │ Health   │ Uptime │
├────────────┼───────┼──────┼─────────┼────────┼───────────────────┼──────────┼────────┤
│ gpt-oss    │ 12345 │ 8001 │ vulkan  │ GPU:1  │ gpt-oss-20b-Q4    │ ● OK     │ 2h 15m │
│ gemma-tiny │ 12346 │ 8002 │ cpu     │ -      │ gemma-270m        │ ● OK     │ 45m    │
│ mistral    │ -     │ 8003 │ vulkan  │ GPU:0  │ mistral-7b        │ ○ STOPPED│ -      │
└────────────┴───────┴──────┴─────────┴────────┴───────────────────┴──────────┴────────┘
```

---

### Phase 5: Daemon + Autostart (6-8 hours)

**Goal:** Background daemon, Windows integration

| Task | Details | Acceptance Criteria |
|------|---------|---------------------|
| Daemon mode | `daemon start` | Runs in background |
| Daemon stop | `daemon stop` | Clean shutdown |
| Daemon status | `daemon status` | Running/stopped |
| PID file | state/daemon.pid | Lock mechanism |
| Windows service | NSSM/WinSW wrapper | Starts on boot |
| Task Scheduler | Alternative | Login trigger |
| Startup config | Which instances to start | Configurable |

**Daemon Architecture:**
```python
async def daemon_loop():
    while running:
        for instance in get_all_instances():
            if instance.should_be_running:
                await check_health(instance)
                if instance.needs_restart:
                    await restart_instance(instance)
        await asyncio.sleep(config.poll_interval)
```

---

### Phase 6: Extensions (Future, 10+ hours)

**Goal:** Advanced features

| Task | Details | Priority |
|------|---------|----------|
| GPU auto-detection | llama-server --list-devices | High |
| Config templates | CPU/Vulkan/CUDA profiles | Medium |
| REST API | FastAPI control plane | Medium |
| Web GUI | React/Vue frontend | Low |
| Model pooling | Share models across instances | Low |
| Load balancing | Round-robin across instances | Low |

---

## 7. Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Process tree kill fails | Medium | High | Use psutil.Process().children() + terminate |
| Health check false positive | Low | Medium | Multiple retries, backoff |
| Port collision at runtime | Low | Medium | Lock file per port |
| SQLite corruption | Low | High | WAL mode, backup on write |
| Vulkan device enumeration fails | Medium | Low | Fallback to manual config |
| Windows service permission | Medium | Medium | Document requirements |

---

## 8. Testing Strategy

### Unit Tests
- Config validation (pydantic)
- State manager CRUD
- Health parsing

### Integration Tests
- Process start/stop lifecycle
- Health check with mock server
- Log file rotation

### E2E Tests
- Full instance lifecycle
- Daemon start/stop
- Multi-instance scenario

---

## 9. Deliverables Summary

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 0 | Project structure, dependencies | ⬜ TODO |
| 1 | Config schema, validation CLI | ⬜ TODO |
| 2 | Process engine (up/down/restart) | ⬜ TODO |
| 3 | Health monitoring, auto-restart | ⬜ TODO |
| 4 | CLI ps/logs/describe, TUI dashboard | ⬜ TODO |
| 5 | Daemon mode, Windows autostart | ⬜ TODO |
| 6 | Extensions (optional) | ⬜ FUTURE |

---

## 10. Next Steps

1. **Review this plan** — Confirm scope and priorities
2. **Begin Phase 0** — Setup project structure
3. **Iterate** — MVP first, then extend

---

## References

- [llama.cpp Server Documentation](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md)
- [llama.cpp API Reference](https://github.com/ggml-org/llama.cpp/wiki/Server-API)
- [Existing start-server.ps1](../llama-cpp-server/start-server.ps1)
- [Pydantic v2 Documentation](https://docs.pydantic.dev/latest/)
- [Typer CLI Framework](https://typer.tiangolo.com/)
- [Rich Terminal Library](https://rich.readthedocs.io/)
