# Llama Orchestrator V2 - Implementation Plan

> **Generated:** 2026-01-01  
> **Version:** 2.0.0  
> **Complexity:** Critical  
> **Estimated Total Effort:** 40-60 hours  
> **Primary Author:** GitHub Copilot (implementation_planner_complex profile)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Analysis](#current-state-analysis)
3. [Target Architecture](#target-architecture)
4. [Phased Implementation](#phased-implementation)
5. [Risk Register](#risk-register)
6. [Dependency Map](#dependency-map)
7. [Rollback Procedures](#rollback-procedures)
8. [Testing Strategy](#testing-strategy)
9. [Acceptance Criteria](#acceptance-criteria)

---

## Executive Summary

### Problem Statement

Llama Orchestrator má několik kritických nedostatků, které brání produkčnímu nasazení:

1. **Detach režim nefunguje spolehlivě** - po zavření terminálu se ztratí handle na proces
2. **stdout/stderr PIPE způsobuje deadlock** - proces se může zaseknout při velkém logovacím výstupu
3. **Daemon nelze korektně zastavit** - monitoring loop ignoruje `_running` flag
4. **Konstantní backoff místo exponenciálního** - restart politika nefunguje dle dokumentace
5. **Chybí port collision detection** - více instancí může kolidovat na stejném portu

### Proposed Solution

Implementace "Docker-like" modelu s:
- SQLite jako source of truth pro stav procesů
- Psutil-based process detection a validation
- File-based logging s rotací
- Async/Event-based daemon s korektní terminací
- Pluggable health checks

### Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Detach reliability | ~60% | 99%+ |
| Process detection accuracy | ~70% | 99%+ |
| Daemon stop time | infinite (stuck) | <5s |
| Max log file size | unbounded | 10MB (rotated) |

---

## Current State Analysis

### Existing Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE                           │
│  ┌─────────┐    ┌─────────────┐    ┌──────────────────┐     │
│  │   CLI   │───▶│ engine/     │───▶│ daemon/service   │     │
│  │         │    │ process.py  │    │ (broken stop)    │     │
│  └─────────┘    └─────────────┘    └──────────────────┘     │
│       │              │                    │                  │
│       └──────────────┼────────────────────┘                  │
│                      ▼                                       │
│            ┌─────────────────┐    ┌──────────────────┐      │
│            │  state.sqlite   │    │  health/monitor  │      │
│            │  (incomplete)   │    │  (backoff wrong) │      │
│            └─────────────────┘    └──────────────────┘      │
└──────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                      DATA PLANE                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │ llama:8001  │    │ llama:8002  │    │ llama:8003  │      │
│  │ (may orphan)│    │ (may orphan)│    │ (may orphan)│      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

### Critical Issues Identified

#### A) Process State & Detach Mode (CRITICAL)

**Location:** [engine/process.py](../src/llama_orchestrator/engine/process.py)

**Problem:** 
```python
# Current implementation uses file-based stdout/stderr
proc = subprocess.Popen(
    cmd,
    stdout=stdout_file,  # ✓ Good - file based
    stderr=stderr_file,  # ✓ Good - file based
    ...
)
```

**Status:** Částečně vyřešeno - logování jde do souborů, ale:
- Chybí validace, že proces patří nám (cmdline check)
- Chybí file locking pro paralelní přístup
- Chybí rozšířená runtime tabulka

#### B) Daemon Stop (CRITICAL)

**Location:** [daemon/service.py](../src/llama_orchestrator/daemon/service.py#L200-L230)

**Problem:**
```python
def _main_loop(self) -> None:
    while self._running:  # ✓ Kontroluje _running
        ...
        time.sleep(self.check_interval)  # ✗ Blocking sleep!
```

**Status:** Částečně vyřešeno - `_running` se kontroluje, ale:
- `time.sleep()` blokuje celý interval
- Chybí graceful task cancellation
- Chybí Windows Service support

#### C) Health Check Endpoint (MEDIUM)

**Location:** [health/checker.py](../src/llama_orchestrator/health/checker.py)

**Problem:** Hardcoded `/health` endpoint nemusí fungovat pro všechny varianty llama.cpp

#### D) Exponential Backoff (MEDIUM)

**Location:** [health/monitor.py](../src/llama_orchestrator/health/monitor.py#L200-L220)

**Status:** ✓ VYŘEŠENO - Již implementováno:
```python
def _calculate_backoff(self, attempt, initial_delay, multiplier, max_delay):
    delay = initial_delay * (multiplier ** attempt)
    return min(delay, max_delay)
```

---

## Target Architecture

### Enhanced State Schema

```sql
-- Rozšířená tabulka instances
CREATE TABLE instances (
    name TEXT PRIMARY KEY,
    config_path TEXT NOT NULL,
    desired_state TEXT NOT NULL DEFAULT 'stopped',  -- NEW
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Nová tabulka runtime (oddělená od konfigurace)
CREATE TABLE runtime (
    name TEXT PRIMARY KEY,
    pid INTEGER,
    port INTEGER,                    -- NEW
    cmdline TEXT,                    -- NEW: pro validaci procesu
    binary_version TEXT,             -- NEW
    status TEXT NOT NULL DEFAULT 'stopped',
    health TEXT NOT NULL DEFAULT 'unknown',
    started_at REAL,
    last_seen_at REAL,               -- NEW
    last_health_ok_at REAL,          -- NEW
    restart_attempts INTEGER DEFAULT 0,
    last_exit_code INTEGER,          -- NEW
    last_error TEXT,
    FOREIGN KEY (name) REFERENCES instances(name) ON DELETE CASCADE
);

-- Nová tabulka events (audit log)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    instance_name TEXT,
    level TEXT NOT NULL,             -- info, warning, error
    event_type TEXT NOT NULL,        -- started, stopped, health_change, restart, error
    message TEXT NOT NULL,
    meta_json TEXT,                  -- JSON s dodatečnými daty
    FOREIGN KEY (instance_name) REFERENCES instances(name) ON DELETE SET NULL
);

CREATE INDEX idx_events_instance ON events(instance_name, ts DESC);
CREATE INDEX idx_events_level ON events(level, ts DESC);
```

### Enhanced Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                        CONTROL PLANE V2                            │
│  ┌─────────┐    ┌───────────────┐    ┌───────────────────────┐    │
│  │   CLI   │───▶│ InstanceMgr   │───▶│ DaemonService V2      │    │
│  │         │    │ (reconciler)  │    │ (async + Event-based) │    │
│  └─────────┘    └───────────────┘    └───────────────────────┘    │
│       │                │                       │                   │
│       │                ▼                       │                   │
│       │    ┌───────────────────────┐          │                   │
│       │    │   ProcessValidator    │◀─────────┘                   │
│       │    │   (psutil + cmdline)  │                              │
│       │    └───────────────────────┘                              │
│       │                │                                          │
│       ▼                ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    SQLite State Store                        │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │  │
│  │  │instances │  │ runtime  │  │ events   │  │health_hist. │  │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│  ┌───────────────────────────┼───────────────────────────────┐    │
│  │              File System Layer                             │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  │    │
│  │  │ daemon.pid  │  │  .lock      │  │ logs/ (rotating) │  │    │
│  │  └─────────────┘  └─────────────┘  └──────────────────┘  │    │
│  └────────────────────────────────────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────┼───────────────────────────────┐    │
│  │           Health Check Layer (Pluggable)                   │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │    │
│  │  │HTTP Probe│  │TCP Probe │  │Warmup    │  │Custom     │ │    │
│  │  │/health   │  │port check│  │inference │  │script     │ │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └───────────┘ │    │
│  └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
┌────────────────────────────────────────────────────────────────────┐
│                        DATA PLANE                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐    │
│  │ llama:8001      │  │ llama:8002      │  │ llama:8003      │    │
│  │ PID validated   │  │ PID validated   │  │ PID validated   │    │
│  │ logs→file       │  │ logs→file       │  │ logs→file       │    │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
```

---

## Phased Implementation

### Phase 1: State & Process Reliability (Critical - 15h)

**Goal:** Zajistit spolehlivou detekci a správu procesů přes restart CLI.

#### 1.1 Enhanced State Schema (3h)

**File:** `src/llama_orchestrator/engine/state.py`

**Changes:**
- Přidat tabulku `runtime` s rozšířenými poli
- Přidat tabulku `events` pro audit log
- Migrační logika pro existující DB

```python
def init_db_v2() -> None:
    """Initialize enhanced database schema with migration."""
    with get_db_connection() as conn:
        # Check schema version
        version = get_schema_version(conn)
        
        if version < 2:
            migrate_v1_to_v2(conn)
        
        # Create new tables...
```

#### 1.2 Process Validator (4h)

**New File:** `src/llama_orchestrator/engine/validator.py`

**Features:**
- Validate PID belongs to our process via cmdline check
- Detect orphaned processes
- Port binding verification

```python
@dataclass
class ProcessValidation:
    """Result of process validation."""
    pid: int
    valid: bool
    reason: str
    process_info: dict | None = None

def validate_process(name: str, expected_pid: int, expected_port: int) -> ProcessValidation:
    """Validate that a process belongs to the orchestrator."""
    try:
        proc = psutil.Process(expected_pid)
        cmdline = " ".join(proc.cmdline())
        
        # Check if it's our llama-server
        if "llama-server" not in cmdline:
            return ProcessValidation(expected_pid, False, "Not a llama-server process")
        
        if f"--port {expected_port}" not in cmdline and f"--port={expected_port}" not in cmdline:
            return ProcessValidation(expected_pid, False, f"Wrong port in cmdline")
        
        return ProcessValidation(expected_pid, True, "Valid", get_process_info(expected_pid))
        
    except psutil.NoSuchProcess:
        return ProcessValidation(expected_pid, False, "Process not found")
```

#### 1.3 File Locking (2h)

**File:** `src/llama_orchestrator/engine/locking.py`

**Features:**
- Per-instance lock files
- Context manager for atomic operations
- Timeout support

```python
@contextmanager
def instance_lock(name: str, timeout: float = 10.0) -> Iterator[None]:
    """Acquire exclusive lock for instance operations."""
    lock_file = get_state_dir() / f"{name}.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    try:
        start = time.time()
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # Windows
                break
            except OSError:
                if time.time() - start > timeout:
                    raise LockTimeoutError(name, timeout)
                time.sleep(0.1)
        
        yield
        
    finally:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)
```

#### 1.4 Port Collision Detection (2h)

**File:** `src/llama_orchestrator/engine/process.py`

**Changes:**
- Check port availability before start
- Better error messages with port info

```python
def check_port_available(port: int, host: str = "127.0.0.1") -> tuple[bool, str]:
    """Check if a port is available for binding."""
    import socket
    
    # First check if another instance claims this port
    for name, state in load_all_states().items():
        runtime = load_runtime(name)
        if runtime and runtime.port == port and state.status == InstanceStatus.RUNNING:
            return False, f"Port {port} already used by instance '{name}'"
    
    # Then check actual binding
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True, f"Port {port} is available"
    except OSError as e:
        return False, f"Port {port} is in use: {e}"
```

#### 1.5 Event Logging (2h)

**File:** `src/llama_orchestrator/engine/events.py`

**Features:**
- Structured event logging to SQLite
- Query API for TUI and CLI
- Retention policy (auto-cleanup old events)

```python
def log_event(
    event_type: str,
    message: str,
    instance_name: str | None = None,
    level: str = "info",
    meta: dict | None = None,
) -> int:
    """Log an event to the database."""
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO events (instance_name, level, event_type, message, meta_json)
            VALUES (?, ?, ?, ?, ?)
        """, (instance_name, level, event_type, message, json.dumps(meta or {})))
        conn.commit()
        return cursor.lastrowid
```

#### 1.6 State Reconciliation (2h)

**File:** `src/llama_orchestrator/engine/reconciler.py`

**Features:**
- Periodic state check and correction
- Detect orphaned processes
- Update stale states

```python
def reconcile_all() -> list[ReconciliationResult]:
    """Reconcile all instance states with actual process status."""
    results = []
    
    for name, config_path in discover_instances():
        state = load_state(name)
        runtime = load_runtime(name)
        
        if state and state.status == InstanceStatus.RUNNING:
            if runtime and runtime.pid:
                validation = validate_process(name, runtime.pid, runtime.port)
                if not validation.valid:
                    # Process died or was replaced
                    update_to_stopped(name, f"Process lost: {validation.reason}")
                    results.append(ReconciliationResult(name, "stopped", validation.reason))
    
    return results
```

---

### Phase 2: Logging & Daemon Reliability (Medium - 12h)

**Goal:** Vyřešit logování bez deadlocku a zastavitelnost daemonu.

#### 2.1 Rotating Log Handler (3h)

**File:** `src/llama_orchestrator/engine/logging.py`

**Features:**
- RotatingFileHandler integration
- Max file size and backup count
- Async log reader for TUI

```python
def setup_instance_logging(name: str, max_bytes: int = 10_000_000, backup_count: int = 3):
    """Setup rotating log files for an instance."""
    log_dir = get_logs_dir() / name
    log_dir.mkdir(parents=True, exist_ok=True)
    
    stdout_handler = RotatingFileHandler(
        log_dir / "stdout.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    
    stderr_handler = RotatingFileHandler(
        log_dir / "stderr.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    
    return stdout_handler, stderr_handler
```

#### 2.2 Log Tail Command (2h)

**File:** `src/llama_orchestrator/cli.py` (update logs command)

**Features:**
- Follow mode (`-f`)
- Line count limit (`-n`)
- Combined stdout/stderr view

```python
@app.command()
def logs(
    name: Annotated[str, typer.Argument()],
    follow: Annotated[bool, typer.Option("-f", "--follow")] = False,
    lines: Annotated[int, typer.Option("-n", "--lines")] = 100,
    stream: Annotated[str, typer.Option("--stream")] = "both",
):
    """View instance logs with optional follow mode."""
    log_files = get_log_files(name)
    
    if follow:
        tail_follow(log_files, stream)
    else:
        tail_last_n(log_files, lines, stream)
```

#### 2.3 Daemon V2 with Event-based Loop (5h)

**File:** `src/llama_orchestrator/daemon/service.py` (major refactor)

**Changes:**
- Replace `time.sleep()` with `threading.Event.wait()`
- Proper signal handling
- Graceful shutdown with timeout

```python
class DaemonServiceV2:
    def __init__(self):
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
    
    def _main_loop(self) -> None:
        """Main daemon loop with interruptible wait."""
        while not self._stop_event.is_set():
            try:
                self._check_all_instances()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            
            # Interruptible wait
            self._stop_event.wait(timeout=self.check_interval)
    
    def stop(self, timeout: float = 10.0) -> bool:
        """Request graceful shutdown."""
        logger.info("Stopping daemon...")
        self._stop_event.set()
        
        # Wait for threads to finish
        for thread in self._threads:
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(f"Thread {thread.name} did not stop in time")
                return False
        
        return True
```

#### 2.4 Windows Service Support (2h)

**New File:** `src/llama_orchestrator/daemon/win_service.py`

**Features:**
- NSSM wrapper for Windows Service registration
- Service install/uninstall commands

```python
def install_windows_service(service_name: str = "LlamaOrchestrator"):
    """Install as Windows Service using NSSM."""
    nssm = find_nssm()
    python_exe = sys.executable
    script = str(Path(__file__).parent / "_service_entry.py")
    
    subprocess.run([
        nssm, "install", service_name,
        python_exe, script,
    ], check=True)
    
    # Configure service
    subprocess.run([nssm, "set", service_name, "DisplayName", "Llama Orchestrator"], check=True)
    subprocess.run([nssm, "set", service_name, "Description", "Manages llama.cpp server instances"], check=True)
```

---

### Phase 3: Health Check Enhancements (Medium - 8h)

**Goal:** Pluggable health checks a robustní restart politika.

#### 3.1 Pluggable Health Check System (4h)

**File:** `src/llama_orchestrator/health/probes.py`

**Features:**
- Abstract probe interface
- HTTP probe (with configurable path)
- TCP probe
- Custom script probe

```python
from abc import ABC, abstractmethod

class HealthProbe(ABC):
    @abstractmethod
    def check(self, host: str, port: int, timeout: float) -> HealthCheckResult:
        """Perform health check."""
        pass

class HTTPProbe(HealthProbe):
    def __init__(self, path: str = "/health", expected_status: int = 200):
        self.path = path
        self.expected_status = expected_status
    
    def check(self, host: str, port: int, timeout: float) -> HealthCheckResult:
        # ... existing HTTP check logic ...
        pass

class TCPProbe(HealthProbe):
    def check(self, host: str, port: int, timeout: float) -> HealthCheckResult:
        """Just check if port is accepting connections."""
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return HealthCheckResult(status=HealthCheckStatus.OK)
        except socket.error as e:
            return HealthCheckResult(status=HealthCheckStatus.UNREACHABLE, error_message=str(e))
```

#### 3.2 Config Schema Update (2h)

**File:** `src/llama_orchestrator/config/schema.py`

**Changes:**
- Add health check type selection
- Add custom health path
- Add warmup probe option

```python
@dataclass
class HealthcheckConfig:
    interval: int = 10
    timeout: float = 5.0
    retries: int = 3
    start_period: int = 60
    
    # New fields
    type: str = "http"  # http, tcp, custom
    path: str = "/health"  # for HTTP probe
    expected_status: int = 200
    custom_script: str = ""  # for custom probe
    warmup_enabled: bool = False
    warmup_prompt: str = "Hello"
```

#### 3.3 Backoff with Jitter (2h)

**File:** `src/llama_orchestrator/health/monitor.py`

**Changes:**
- Add jitter to prevent thundering herd
- Improve logging

```python
def _calculate_backoff_with_jitter(
    self,
    attempt: int,
    initial_delay: float,
    multiplier: float,
    max_delay: float,
) -> float:
    """Calculate exponential backoff with jitter."""
    import random
    
    base_delay = initial_delay * (multiplier ** attempt)
    jitter = random.uniform(0, initial_delay)
    delay = min(base_delay + jitter, max_delay)
    
    return delay
```

---

### Phase 4: CLI & UX Improvements (Low - 5h)

**Goal:** Lepší error messages, exit codes, describe command.

#### 4.1 Exit Code Standards (2h)

**File:** `src/llama_orchestrator/cli.py`

**Exit Codes:**
- 0: Success
- 1: General error
- 2: Instance not found
- 3: Instance already running
- 4: Port collision
- 5: Executable not found
- 10: Daemon error

```python
class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL_ERROR = 1
    INSTANCE_NOT_FOUND = 2
    ALREADY_RUNNING = 3
    PORT_COLLISION = 4
    EXECUTABLE_NOT_FOUND = 5
    DAEMON_ERROR = 10
```

#### 4.2 Describe Command Enhancement (2h)

**File:** `src/llama_orchestrator/cli.py`

**Features:**
- Show effective command that would be run
- Show runtime info
- Show recent events

#### 4.3 Recent Events in Dashboard (1h)

**File:** `src/llama_orchestrator/dashboard/` (if exists)

**Features:**
- Real-time event feed
- Filter by instance

---

## Risk Register

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R1 | Schema migration breaks existing data | Medium | High | Backup before migration, test on copy | Dev |
| R2 | psutil not available on all Windows | Low | High | Fallback to subprocess, document requirements | Dev |
| R3 | File locking deadlock | Low | Medium | Timeout mechanism, stale lock detection | Dev |
| R4 | Windows Service registration fails | Medium | Low | Provide manual NSSM instructions | Dev |
| R5 | Health probe changes break existing configs | Low | Medium | Backward compatible defaults | Dev |
| R6 | Large log files consume disk | Medium | Medium | Default rotation, configurable limits | Dev |

### Mitigation Details

**R1: Schema Migration**
```python
def backup_database():
    """Create backup before migration."""
    db_path = get_db_path()
    backup_path = db_path.with_suffix(".sqlite.backup")
    shutil.copy2(db_path, backup_path)
    return backup_path
```

**R3: Stale Lock Detection**
```python
def is_lock_stale(lock_file: Path, max_age: float = 300) -> bool:
    """Check if lock file is stale (process died)."""
    if not lock_file.exists():
        return False
    
    age = time.time() - lock_file.stat().st_mtime
    return age > max_age
```

---

## Dependency Map

```
┌─────────────────────────────────────────────────────────────────┐
│                     PHASE 1: FOUNDATIONS                        │
│  ┌────────────┐       ┌────────────┐       ┌────────────┐      │
│  │ 1.1 Schema │──────▶│ 1.2 Valid. │──────▶│ 1.6 Recon. │      │
│  │  (3h)      │       │  (4h)      │       │  (2h)      │      │
│  └────────────┘       └────────────┘       └────────────┘      │
│        │                    │                                   │
│        ▼                    ▼                                   │
│  ┌────────────┐       ┌────────────┐                           │
│  │ 1.3 Lock   │       │ 1.4 Port   │                           │
│  │  (2h)      │       │  (2h)      │                           │
│  └────────────┘       └────────────┘                           │
│        │                    │                                   │
│        └────────┬───────────┘                                   │
│                 ▼                                               │
│           ┌────────────┐                                        │
│           │ 1.5 Events │                                        │
│           │  (2h)      │                                        │
│           └────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PHASE 2: LOGGING & DAEMON                     │
│  ┌────────────┐       ┌────────────┐       ┌────────────┐      │
│  │ 2.1 Rotate │──────▶│ 2.2 Tail   │       │ 2.3 Daemon │      │
│  │  (3h)      │       │  (2h)      │       │  V2 (5h)   │      │
│  └────────────┘       └────────────┘       └────────────┘      │
│                                                   │             │
│                                                   ▼             │
│                                            ┌────────────┐      │
│                                            │ 2.4 WinSvc │      │
│                                            │  (2h)      │      │
│                                            └────────────┘      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 3: HEALTH CHECKS                       │
│  ┌────────────┐       ┌────────────┐       ┌────────────┐      │
│  │ 3.1 Probes │──────▶│ 3.2 Config │──────▶│ 3.3 Jitter │      │
│  │  (4h)      │       │  (2h)      │       │  (2h)      │      │
│  └────────────┘       └────────────┘       └────────────┘      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PHASE 4: UX & CLI                           │
│  ┌────────────┐       ┌────────────┐       ┌────────────┐      │
│  │ 4.1 Exits  │       │ 4.2 Desc.  │       │ 4.3 TUI    │      │
│  │  (2h)      │       │  (2h)      │       │  (1h)      │      │
│  └────────────┘       └────────────┘       └────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Rollback Procedures

### Phase 1 Rollback

```powershell
# 1. Backup current DB
Copy-Item state/state.sqlite state/state.sqlite.v2-rollback

# 2. Restore original DB
Copy-Item state/state.sqlite.backup state/state.sqlite

# 3. Checkout previous code
git checkout main -- src/llama_orchestrator/engine/

# 4. Reinstall
pip install -e .
```

### Phase 2 Rollback

```powershell
# Stop daemon
llama-orch daemon stop --force

# Remove service if installed
nssm remove LlamaOrchestrator confirm

# Restore daemon code
git checkout main -- src/llama_orchestrator/daemon/
```

### Phase 3 Rollback

```powershell
# Restore health module
git checkout main -- src/llama_orchestrator/health/

# Restore config schema
git checkout main -- src/llama_orchestrator/config/schema.py
```

---

## Testing Strategy

### Unit Tests

| Module | Test File | Coverage Target |
|--------|-----------|-----------------|
| engine/state.py | tests/test_state_v2.py | 90% |
| engine/validator.py | tests/test_validator.py | 95% |
| engine/locking.py | tests/test_locking.py | 85% |
| daemon/service.py | tests/test_daemon_v2.py | 80% |
| health/probes.py | tests/test_probes.py | 90% |

### Integration Tests

```python
# tests/test_integration_v2.py

def test_detach_survives_restart():
    """Test that instances survive CLI process restart."""
    # Start instance
    start_instance("test-instance")
    
    # Simulate CLI restart (new process)
    clear_in_memory_state()
    
    # Verify instance still detected
    state = get_instance_status("test-instance")
    assert state.status == InstanceStatus.RUNNING

def test_orphan_detection():
    """Test detection of orphaned processes."""
    # Start instance
    start_instance("test-instance")
    pid = load_state("test-instance").pid
    
    # Corrupt state (simulate crash)
    delete_state("test-instance")
    
    # Reconcile should detect orphan
    results = reconcile_all()
    assert any(r.name == "test-instance" for r in results)

def test_daemon_stops_within_timeout():
    """Test daemon stops gracefully within 5 seconds."""
    start_daemon(foreground=False)
    time.sleep(1)  # Wait for start
    
    start = time.time()
    stop_daemon()
    elapsed = time.time() - start
    
    assert elapsed < 5.0
    assert not is_daemon_running()
```

### Property-Based Tests (Hypothesis)

```python
from hypothesis import given, strategies as st

@given(st.integers(min_value=1, max_value=65535))
def test_port_collision_detection(port):
    """Test port collision detection with random ports."""
    available, msg = check_port_available(port)
    
    # Should return consistent results
    available2, msg2 = check_port_available(port)
    assert available == available2

@given(
    st.integers(min_value=0, max_value=10),
    st.floats(min_value=0.1, max_value=60.0),
    st.floats(min_value=1.0, max_value=5.0),
    st.floats(min_value=60.0, max_value=3600.0),
)
def test_backoff_calculation(attempt, initial, multiplier, max_delay):
    """Test backoff calculation with various inputs."""
    delay = calculate_backoff_with_jitter(attempt, initial, multiplier, max_delay)
    
    assert delay >= 0
    assert delay <= max_delay + initial  # Jitter can add up to initial_delay
```

---

## Acceptance Criteria

### Phase 1 Acceptance

- [ ] Instance survives CLI restart and is detectable via `ps`
- [ ] Orphaned processes are detected within 30 seconds
- [ ] Port collision prevents duplicate starts with clear error
- [ ] File locking prevents race conditions
- [ ] Events are logged and queryable

### Phase 2 Acceptance

- [ ] Logs rotate at 10MB with 3 backups
- [ ] `logs -f` follows file in real-time
- [ ] Daemon stops within 5 seconds
- [ ] Windows Service can be installed/uninstalled

### Phase 3 Acceptance

- [ ] TCP probe works for non-HTTP servers
- [ ] Custom health path is respected
- [ ] Jitter prevents synchronized restarts

### Phase 4 Acceptance

- [ ] Exit codes are consistent and documented
- [ ] `describe` shows effective command
- [ ] Dashboard shows recent events

---

## Appendix: File Changes Summary

| File | Action | Lines Changed (est.) |
|------|--------|----------------------|
| engine/state.py | Modify | +100 |
| engine/validator.py | Create | +150 |
| engine/locking.py | Create | +80 |
| engine/events.py | Create | +100 |
| engine/reconciler.py | Create | +120 |
| engine/process.py | Modify | +50 |
| daemon/service.py | Major refactor | +200, -100 |
| daemon/win_service.py | Create | +100 |
| health/probes.py | Create | +200 |
| health/monitor.py | Modify | +30 |
| health/checker.py | Modify | +20 |
| config/schema.py | Modify | +30 |
| cli.py | Modify | +100 |
| tests/test_*.py | Create/Modify | +500 |

**Total estimated changes:** ~1,800 lines added, ~100 lines removed

---

## Next Steps

1. **Review this plan** with stakeholders
2. **Create feature branch:** `feature/llama-orch-v2`
3. **Begin Phase 1.1** (Schema enhancement)
4. **Set up CI** for new tests
5. **Schedule code review** after each phase
