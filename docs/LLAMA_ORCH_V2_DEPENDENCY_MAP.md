# Llama Orchestrator V2 - Dependency Map

> **Generated:** 2026-01-01  
> **Version:** 2.0.0  
> **Purpose:** Visual dependency graph for implementation planning

---

## Module Dependency Graph

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL DEPENDENCIES                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ psutil   │  │  httpx   │  │ sqlite3  │  │  typer   │  │ rich (terminal)  │  │
│  │ >=5.9.0  │  │ >=0.24.0 │  │ (stdlib) │  │ >=0.9.0  │  │   >=13.0.0       │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘  │
│       │             │             │             │                  │            │
└───────┼─────────────┼─────────────┼─────────────┼──────────────────┼────────────┘
        │             │             │             │                  │
        ▼             ▼             ▼             ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CORE LAYER                                         │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        config/ module                                    │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                      │   │
│  │  │  loader.py  │  │  schema.py  │  │ validator.py│                      │   │
│  │  │ (paths,     │  │ (dataclass  │  │ (validation │                      │   │
│  │  │  discovery) │  │  configs)   │  │  rules)     │                      │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                      │   │
│  │         │                │                │                              │   │
│  │         └────────────────┼────────────────┘                              │   │
│  │                          │                                               │   │
│  └──────────────────────────┼───────────────────────────────────────────────┘   │
│                             │                                                   │
└─────────────────────────────┼───────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                             ENGINE LAYER                                        │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         engine/ module                                    │  │
│  │                                                                           │  │
│  │  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐            │  │
│  │  │  state.py   │◄──────│  locking.py │       │  events.py  │            │  │
│  │  │ (SQLite DB, │       │  (file      │       │ (event log, │            │  │
│  │  │  instances, │       │   locks)    │       │  audit)     │            │  │
│  │  │  runtime)   │       │  [NEW]      │       │  [NEW]      │            │  │
│  │  └──────┬──────┘       └──────┬──────┘       └──────┬──────┘            │  │
│  │         │                     │                     │                    │  │
│  │         ▼                     ▼                     ▼                    │  │
│  │  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐            │  │
│  │  │ validator.py│◄──────│ process.py  │──────▶│ command.py  │            │  │
│  │  │ (process    │       │ (start,stop,│       │ (cmdline    │            │  │
│  │  │  validation)│       │  lifecycle) │       │  builder)   │            │  │
│  │  │  [NEW]      │       └──────┬──────┘       └─────────────┘            │  │
│  │  └─────────────┘              │                                          │  │
│  │                               ▼                                          │  │
│  │                        ┌─────────────┐       ┌─────────────┐            │  │
│  │                        │reconciler.py│◄──────│ logging.py  │            │  │
│  │                        │ (state      │       │ (rotating   │            │  │
│  │                        │  sync)      │       │  handlers)  │            │  │
│  │                        │  [NEW]      │       │  [NEW]      │            │  │
│  │                        └─────────────┘       └─────────────┘            │  │
│  │                                                                          │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            HEALTH LAYER                                         │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         health/ module                                    │  │
│  │                                                                           │  │
│  │  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐            │  │
│  │  │  probes.py  │◄──────│  checker.py │──────▶│ monitor.py  │            │  │
│  │  │ (HTTP, TCP, │       │ (health     │       │ (background │            │  │
│  │  │  Custom)    │       │  check API) │       │  monitoring)│            │  │
│  │  │  [NEW]      │       └─────────────┘       └──────┬──────┘            │  │
│  │  └─────────────┘                                    │                    │  │
│  │                                                     │                    │  │
│  └─────────────────────────────────────────────────────┼────────────────────┘  │
│                                                        │                        │
└────────────────────────────────────────────────────────┼────────────────────────┘
                                                         │
                                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            DAEMON LAYER                                         │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         daemon/ module                                    │  │
│  │                                                                           │  │
│  │  ┌─────────────┐       ┌─────────────┐                                   │  │
│  │  │ service.py  │──────▶│win_service.py                                   │  │
│  │  │ (daemon     │       │ (NSSM       │                                   │  │
│  │  │  loop, V2)  │       │  wrapper)   │                                   │  │
│  │  │  [REFACTOR] │       │  [NEW]      │                                   │  │
│  │  └─────────────┘       └─────────────┘                                   │  │
│  │                                                                           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         CLI / USER INTERFACE                                    │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐            │  │
│  │  │   cli.py    │──────▶│  tui/       │       │  binaries/  │            │  │
│  │  │ (commands,  │       │ (dashboard) │       │ (version    │            │  │
│  │  │  exit codes)│       │             │       │  manager)   │            │  │
│  │  │  [UPDATE]   │       │             │       │             │            │  │
│  │  └─────────────┘       └─────────────┘       └─────────────┘            │  │
│  │                                                                           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## New File Dependencies

### Files to Create

| File | Depends On | Depended By |
|------|------------|-------------|
| `engine/validator.py` | psutil, state.py | process.py, reconciler.py |
| `engine/locking.py` | (stdlib only) | process.py, state.py |
| `engine/events.py` | state.py (DB) | process.py, monitor.py, cli.py |
| `engine/logging.py` | (stdlib) | process.py |
| `engine/reconciler.py` | validator.py, state.py, events.py | daemon/service.py |
| `health/probes.py` | httpx, (socket) | checker.py |
| `daemon/win_service.py` | (subprocess, NSSM) | cli.py |

### Files to Modify

| File | Changes | Breaking |
|------|---------|----------|
| `engine/state.py` | New tables, migration | No (migration) |
| `engine/process.py` | Port check, locking | No |
| `daemon/service.py` | Event-based loop | No (API same) |
| `health/monitor.py` | Jitter, cleanup | No |
| `health/checker.py` | Use probes | No |
| `config/schema.py` | New fields | No (defaults) |
| `cli.py` | Exit codes, describe | No |

---

## Phase Dependency Graph

```
                    ┌─────────────────────────────────────────┐
                    │            PRE-FLIGHT                   │
                    │  - Backup DB                            │
                    │  - Run existing tests                   │
                    │  - Create branch                        │
                    └────────────────┬────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                              PHASE 1 (15h)                                     │
│                      State & Process Reliability                               │
│                                                                                │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐│
│  │1.1 Schema│───▶│1.2 Valid.│───▶│1.3 Lock  │    │1.4 Port  │    │1.5 Event ││
│  │   (3h)   │    │   (4h)   │    │   (2h)   │    │   (2h)   │    │   (2h)   ││
│  └──────────┘    └──────────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘│
│                        │              │               │               │       │
│                        │              └───────────────┼───────────────┘       │
│                        │                              │                       │
│                        ▼                              ▼                       │
│                  ┌──────────────────────────────────────────┐                 │
│                  │         1.6 Reconciler (2h)              │                 │
│                  │  Depends on: validator, state, events    │                 │
│                  └──────────────────────────────────────────┘                 │
│                                                                                │
└────────────────────────────────────────────┬───────────────────────────────────┘
                                             │
                         ╔═══════════════════╧═══════════════════╗
                         ║     PHASE 1 VALIDATION GATE           ║
                         ║  - All Phase 1 tests pass             ║
                         ║  - Code review completed              ║
                         ╚═══════════════════╤═══════════════════╝
                                             │
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                              PHASE 2 (12h)                                     │
│                        Logging & Daemon Reliability                            │
│                                                                                │
│  ┌──────────┐    ┌──────────┐         ┌──────────────────────────────────┐   │
│  │2.1 Rotate│───▶│2.2 Tail  │         │      2.3 Daemon V2 (5h)          │   │
│  │   (3h)   │    │   (2h)   │         │  Depends on: events, reconciler  │   │
│  └──────────┘    └──────────┘         └───────────────┬──────────────────┘   │
│                                                       │                       │
│                                                       ▼                       │
│                                               ┌──────────────┐                │
│                                               │2.4 Win Svc   │                │
│                                               │    (2h)      │                │
│                                               │Depends: daemon│               │
│                                               └──────────────┘                │
│                                                                                │
└────────────────────────────────────────────┬───────────────────────────────────┘
                                             │
                         ╔═══════════════════╧═══════════════════╗
                         ║     PHASE 2 VALIDATION GATE           ║
                         ║  - All Phase 2 tests pass             ║
                         ║  - Daemon stops in <5s                ║
                         ╚═══════════════════╤═══════════════════╝
                                             │
                              ┌──────────────┴──────────────┐
                              │                             │
                              ▼                             ▼
┌─────────────────────────────────────────┐  ┌─────────────────────────────────┐
│           PHASE 3 (8h)                  │  │           PHASE 4 (5h)          │
│     Health Check Enhancements           │  │       CLI & UX Improvements     │
│                                         │  │                                 │
│  ┌────────┐  ┌────────┐  ┌────────┐    │  │  ┌────────┐  ┌────────┐         │
│  │3.1     │─▶│3.2     │─▶│3.3     │    │  │  │4.1 Exit│  │4.2 Desc│         │
│  │Probes  │  │Config  │  │Jitter  │    │  │  │ Codes  │  │ribe   │         │
│  │(4h)    │  │(2h)    │  │(2h)    │    │  │  │ (2h)   │  │ (2h)  │         │
│  └────────┘  └────────┘  └────────┘    │  │  └────────┘  └───┬────┘         │
│                                         │  │                  │              │
│                                         │  │                  ▼              │
│                                         │  │           ┌────────────┐        │
│                                         │  │           │4.3 TUI     │        │
│                                         │  │           │Events (1h) │        │
│                                         │  │           │Needs:events│        │
│                                         │  │           └────────────┘        │
└─────────────────────────────────────────┘  └─────────────────────────────────┘
                              │                             │
                              └──────────────┬──────────────┘
                                             │
                                             ▼
                    ┌─────────────────────────────────────────┐
                    │         FINAL VALIDATION                │
                    │  - All tests pass                       │
                    │  - Documentation complete               │
                    │  - PR review & merge                    │
                    └─────────────────────────────────────────┘
```

---

## Import Dependency Graph (Python)

### engine/validator.py (NEW)

```python
# Internal dependencies
from llama_orchestrator.config import get_instance_config
from llama_orchestrator.engine.state import load_state, load_runtime

# External dependencies
import psutil
```

### engine/locking.py (NEW)

```python
# Internal dependencies
from llama_orchestrator.config import get_state_dir

# External dependencies
import os
import msvcrt  # Windows
import time
import logging
```

### engine/events.py (NEW)

```python
# Internal dependencies
from llama_orchestrator.engine.state import get_db_connection

# External dependencies
import json
import time
import logging
```

### engine/reconciler.py (NEW)

```python
# Internal dependencies
from llama_orchestrator.config import discover_instances
from llama_orchestrator.engine.state import load_state, load_runtime, save_state
from llama_orchestrator.engine.validator import validate_process
from llama_orchestrator.engine.events import log_event

# External dependencies
import logging
```

### health/probes.py (NEW)

```python
# Internal dependencies
(none - leaf module)

# External dependencies
import httpx
import socket
import subprocess
import abc
```

### daemon/win_service.py (NEW)

```python
# Internal dependencies
from llama_orchestrator.config import get_state_dir

# External dependencies
import subprocess
import sys
import os
import logging
```

---

## Database Schema Dependencies

```
┌─────────────────┐       ┌─────────────────┐
│   instances     │       │     runtime     │
├─────────────────┤       ├─────────────────┤
│ name (PK)       │◄──────│ name (PK, FK)   │
│ config_path     │       │ pid             │
│ desired_state   │       │ port            │
│ created_at      │       │ cmdline         │
│ updated_at      │       │ status          │
└────────┬────────┘       │ health          │
         │                │ started_at      │
         │                │ last_seen_at    │
         │                │ restart_attempts│
         │                │ last_exit_code  │
         │                │ last_error      │
         │                └─────────────────┘
         │
         │                ┌─────────────────┐
         │                │  health_history │
         │                ├─────────────────┤
         └───────────────▶│ instance_name FK│
                          │ health          │
                          │ response_time_ms│
                          │ error_message   │
                          │ checked_at      │
                          └─────────────────┘
                          
         │                ┌─────────────────┐
         │                │     events      │
         │                ├─────────────────┤
         └───────────────▶│ instance_name FK│ (nullable)
                          │ level           │
                          │ event_type      │
                          │ message         │
                          │ meta_json       │
                          │ ts              │
                          └─────────────────┘
```

---

## Test File Dependencies

```
tests/
├── test_state_v2.py        ← engine/state.py (migration, new tables)
├── test_validator.py       ← engine/validator.py (process validation)
├── test_locking.py         ← engine/locking.py (concurrent access)
├── test_events.py          ← engine/events.py (logging, querying)
├── test_reconciler.py      ← engine/reconciler.py (state sync)
├── test_logging_rotate.py  ← engine/logging.py (rotation)
├── test_daemon_v2.py       ← daemon/service.py (stop behavior)
├── test_probes.py          ← health/probes.py (HTTP, TCP, Custom)
├── test_monitor_jitter.py  ← health/monitor.py (backoff calculation)
├── test_cli_exitcodes.py   ← cli.py (exit code consistency)
└── test_integration_v2.py  ← (all modules - end-to-end)
```

---

## Parallelization Opportunities

### Can Run in Parallel (No Dependencies)

```
Phase 1:
  1.3 Locking ──┐
  1.4 Port     ──┼── Can develop simultaneously
  1.5 Events   ──┘

Phase 2+3+4 (After Phase 1):
  2.1-2.2 (Logging) ──┐
  3.1-3.3 (Health)  ──┼── Can develop simultaneously  
  4.1-4.2 (CLI)     ──┘
```

### Must Be Sequential

```
1.1 Schema ──▶ 1.2 Validator ──▶ 1.6 Reconciler
2.3 Daemon V2 ──▶ 2.4 Win Service
3.1 Probes ──▶ 3.2 Config ──▶ 3.3 Jitter
```

---

*Last updated: 2026-01-01*
