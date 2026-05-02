# Llama Orchestrator V2 - Risk Register

> **Generated:** 2026-01-01  
> **Version:** 2.0.0  
> **Project:** Llama Orchestrator Critical Update

---

## Risk Assessment Matrix

### Probability Scale

| Level | Value | Description |
|-------|-------|-------------|
| Rare | 1 | < 10% chance |
| Unlikely | 2 | 10-30% chance |
| Possible | 3 | 30-50% chance |
| Likely | 4 | 50-70% chance |
| Almost Certain | 5 | > 70% chance |

### Impact Scale

| Level | Value | Description |
|-------|-------|-------------|
| Negligible | 1 | Minor inconvenience, workaround available |
| Minor | 2 | Some functionality affected, quick fix |
| Moderate | 3 | Significant functionality loss, requires fix |
| Major | 4 | Critical functionality broken, blocking |
| Severe | 5 | Complete system failure, data loss |

### Risk Score

**Risk Score = Probability × Impact**

| Score Range | Priority | Action Required |
|-------------|----------|-----------------|
| 1-5 | Low | Monitor, address in normal cycle |
| 6-12 | Medium | Plan mitigation, address soon |
| 13-19 | High | Prioritize mitigation, address immediately |
| 20-25 | Critical | Stop other work, mitigate now |

---

## Identified Risks

### R1: Database Schema Migration Data Loss

| Attribute | Value |
|-----------|-------|
| **ID** | R1 |
| **Category** | Data Integrity |
| **Description** | Migration from v1 to v2 schema may corrupt or lose existing instance state data |
| **Probability** | 3 (Possible) |
| **Impact** | 4 (Major) |
| **Risk Score** | **12 (Medium-High)** |
| **Phase** | 1.1 |

**Triggers:**
- Interrupted migration (power loss, kill -9)
- Schema mismatch between expected and actual v1 format
- SQLite corruption

**Mitigation Strategies:**

1. **Automatic Backup** (Primary)
   ```python
   def migrate_v1_to_v2(conn):
       # ALWAYS backup first
       backup_path = backup_database()
       logger.info(f"Created backup at {backup_path}")
       
       try:
           # Migration logic
           ...
       except Exception as e:
           # Restore from backup
           restore_database(backup_path)
           raise MigrationError(f"Migration failed, restored from backup: {e}")
   ```

2. **Transaction-based Migration**
   ```python
   with conn:  # Implicit transaction
       conn.execute("ALTER TABLE ...")
       conn.execute("INSERT INTO ...")
       # Commit only if all succeed
   ```

3. **Schema Version Check**
   ```python
   def get_schema_version(conn):
       try:
           row = conn.execute("SELECT version FROM schema_info").fetchone()
           return row[0] if row else 1
       except sqlite3.OperationalError:
           return 1  # Table doesn't exist = v1
   ```

**Contingency:**
- Manual restore from backup: `Copy-Item state/state.sqlite.backup state/state.sqlite`
- Re-initialize DB: `llama-orch config init --force`

**Owner:** Development Team  
**Status:** Open

---

### R2: psutil Compatibility Issues

| Attribute | Value |
|-----------|-------|
| **ID** | R2 |
| **Category** | Dependency |
| **Description** | psutil may not work correctly on all Windows configurations (restricted permissions, antivirus interference) |
| **Probability** | 2 (Unlikely) |
| **Impact** | 4 (Major) |
| **Risk Score** | **8 (Medium)** |
| **Phase** | 1.2 |

**Triggers:**
- Running without administrator privileges
- Antivirus blocking process inspection
- Unusual Windows configurations (Server Core, etc.)

**Mitigation Strategies:**

1. **Graceful Fallback**
   ```python
   def get_process_info(pid: int) -> dict | None:
       try:
           proc = psutil.Process(pid)
           return {
               "pid": pid,
               "cmdline": proc.cmdline(),
               ...
           }
       except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
           logger.warning(f"Cannot access process {pid}: {e}")
           # Fallback: trust the state database
           return None
   ```

2. **Alternative Detection via Port**
   ```python
   def is_port_in_use(port: int) -> bool:
       """Fallback check using socket."""
       import socket
       with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
           return s.connect_ex(('127.0.0.1', port)) == 0
   ```

3. **Documentation**
   - Add requirements section mentioning psutil
   - Document troubleshooting for access denied errors

**Contingency:**
- Document manual process management commands
- Provide PowerShell fallback scripts

**Owner:** Development Team  
**Status:** Open

---

### R3: File Locking Deadlock

| Attribute | Value |
|-----------|-------|
| **ID** | R3 |
| **Category** | Concurrency |
| **Description** | Multiple CLI processes may deadlock when competing for instance locks |
| **Probability** | 2 (Unlikely) |
| **Impact** | 3 (Moderate) |
| **Risk Score** | **6 (Medium)** |
| **Phase** | 1.3 |

**Triggers:**
- Rapid concurrent CLI invocations
- Lock held by crashed process
- Timeout too short for slow operations

**Mitigation Strategies:**

1. **Timeout with Clear Error**
   ```python
   @contextmanager
   def instance_lock(name: str, timeout: float = 10.0):
       start = time.time()
       while True:
           try:
               acquire_lock(name)
               break
           except LockBusyError:
               if time.time() - start > timeout:
                   raise LockTimeoutError(
                       f"Could not acquire lock for '{name}' within {timeout}s. "
                       f"Another operation may be in progress. "
                       f"If stuck, delete state/{name}.lock"
                   )
               time.sleep(0.1)
       try:
           yield
       finally:
           release_lock(name)
   ```

2. **Stale Lock Detection**
   ```python
   def is_lock_stale(name: str, max_age: float = 300) -> bool:
       lock_file = get_lock_path(name)
       if not lock_file.exists():
           return False
       
       # Check file age
       age = time.time() - lock_file.stat().st_mtime
       if age > max_age:
           logger.warning(f"Stale lock detected for {name} (age: {age:.0f}s)")
           return True
       
       # Check if holding process is alive (if stored in lock file)
       try:
           pid = int(lock_file.read_text().strip())
           if not psutil.pid_exists(pid):
               logger.warning(f"Lock holder (PID {pid}) no longer exists")
               return True
       except (ValueError, IOError):
           pass
       
       return False
   ```

3. **Auto-cleanup on Stale**
   ```python
   def acquire_lock(name: str):
       lock_file = get_lock_path(name)
       
       if is_lock_stale(name):
           logger.info(f"Removing stale lock for {name}")
           lock_file.unlink()
       
       # Continue with normal acquisition
       ...
   ```

**Contingency:**
- Manual lock removal: `Remove-Item state/*.lock`
- Add `--force-unlock` flag to commands

**Owner:** Development Team  
**Status:** Open

---

### R4: Windows Service Registration Failure

| Attribute | Value |
|-----------|-------|
| **ID** | R4 |
| **Category** | Deployment |
| **Description** | NSSM-based Windows Service installation may fail on some systems |
| **Probability** | 3 (Possible) |
| **Impact** | 2 (Minor) |
| **Risk Score** | **6 (Medium)** |
| **Phase** | 2.4 |

**Triggers:**
- NSSM not in PATH
- Insufficient privileges
- Antivirus blocking service creation
- Service name already exists

**Mitigation Strategies:**

1. **Bundled NSSM**
   - Include NSSM binary in `bin/nssm.exe`
   - Use bundled version if PATH version not found

2. **Clear Error Messages**
   ```python
   def install_windows_service():
       # Check prerequisites
       if not is_admin():
           raise ServiceError(
               "Administrator privileges required. "
               "Run PowerShell as Administrator and try again."
           )
       
       nssm = find_nssm()
       if not nssm:
           raise ServiceError(
               "NSSM not found. Download from https://nssm.cc/ "
               "and add to PATH, or place nssm.exe in bin/"
           )
       
       # Check for existing service
       if service_exists("LlamaOrchestrator"):
           raise ServiceError(
               "Service 'LlamaOrchestrator' already exists. "
               "Use 'llama-orch daemon uninstall' first."
           )
   ```

3. **Manual Instructions Fallback**
   ```
   If automatic installation fails, you can install manually:
   
   1. Download NSSM from https://nssm.cc/download
   2. Open PowerShell as Administrator
   3. Run: nssm install LlamaOrchestrator "python.exe" "-m" "llama_orchestrator.daemon"
   4. Run: nssm start LlamaOrchestrator
   ```

**Contingency:**
- Document manual service installation
- Provide batch script for manual setup
- Fallback to scheduled task instead of service

**Owner:** Development Team  
**Status:** Open

---

### R5: Health Probe Changes Break Configs

| Attribute | Value |
|-----------|-------|
| **ID** | R5 |
| **Category** | Backward Compatibility |
| **Description** | New healthcheck config fields may cause validation errors for existing configs |
| **Probability** | 2 (Unlikely) |
| **Impact** | 3 (Moderate) |
| **Risk Score** | **6 (Medium)** |
| **Phase** | 3.2 |

**Triggers:**
- Existing config lacks new required fields
- New field defaults differ from previous behavior
- Validation too strict

**Mitigation Strategies:**

1. **Sensible Defaults Match Current Behavior**
   ```python
   @dataclass
   class HealthcheckConfig:
       interval: int = 10
       timeout: float = 5.0
       retries: int = 3
       start_period: int = 60
       
       # New fields with backward-compatible defaults
       type: str = "http"  # Current behavior
       path: str = "/health"  # Current default
       expected_status: int = 200  # Current expectation
       custom_script: str = ""  # Optional, not used by default
       warmup_enabled: bool = False  # Disabled by default
   ```

2. **Validation Warnings Instead of Errors**
   ```python
   def validate_healthcheck_config(config: dict) -> list[ValidationIssue]:
       issues = []
       
       # Warn about deprecated/missing, don't error
       if "type" not in config:
           issues.append(ValidationIssue(
               level="warning",
               message="healthcheck.type not specified, defaulting to 'http'"
           ))
       
       return issues
   ```

3. **Config Migration Helper**
   ```bash
   llama-orch config migrate  # Adds new fields with defaults
   ```

**Contingency:**
- Document all new fields with explicit defaults
- Provide config upgrade script

**Owner:** Development Team  
**Status:** Open

---

### R6: Log File Disk Exhaustion

| Attribute | Value |
|-----------|-------|
| **ID** | R6 |
| **Category** | Resource Management |
| **Description** | Even with rotation, multiple instances over time may exhaust disk space |
| **Probability** | 3 (Possible) |
| **Impact** | 3 (Moderate) |
| **Risk Score** | **9 (Medium)** |
| **Phase** | 2.1 |

**Triggers:**
- Many instances running
- Verbose output from llama.cpp
- Long-running instances without restart
- Small disk partition

**Mitigation Strategies:**

1. **Configurable Limits**
   ```python
   @dataclass
   class LogsConfig:
       max_size_mb: int = 10  # Per log file
       backup_count: int = 3
       retention_days: int = 7  # Delete older than this
   ```

2. **Total Size Warning**
   ```python
   def check_log_disk_usage():
       logs_dir = get_logs_dir()
       total_size = sum(f.stat().st_size for f in logs_dir.rglob("*") if f.is_file())
       
       if total_size > 1_000_000_000:  # 1GB
           logger.warning(f"Log directory using {total_size / 1e9:.1f}GB")
           return Warning(f"Log disk usage high: {total_size / 1e9:.1f}GB")
   ```

3. **Automatic Cleanup**
   ```python
   def cleanup_old_logs(retention_days: int = 7):
       """Delete log files older than retention period."""
       cutoff = time.time() - (retention_days * 86400)
       
       for log_file in get_logs_dir().rglob("*.log*"):
           if log_file.stat().st_mtime < cutoff:
               log_file.unlink()
               logger.info(f"Deleted old log: {log_file}")
   ```

4. **Disk Space Check Before Start**
   ```python
   def check_disk_space(min_mb: int = 100) -> bool:
       import shutil
       free = shutil.disk_usage(get_logs_dir()).free
       return free > min_mb * 1024 * 1024
   ```

**Contingency:**
- `llama-orch logs clean` command
- Document disk requirements

**Owner:** Development Team  
**Status:** Open

---

### R7: Daemon Memory Leak

| Attribute | Value |
|-----------|-------|
| **ID** | R7 |
| **Category** | Resource Management |
| **Description** | Long-running daemon may accumulate memory over time due to unbounded collections |
| **Probability** | 2 (Unlikely) |
| **Impact** | 3 (Moderate) |
| **Risk Score** | **6 (Medium)** |
| **Phase** | 2.3 |

**Triggers:**
- Many health checks accumulating results
- Event log growing unbounded
- Instance state objects not cleaned up

**Mitigation Strategies:**

1. **Bounded Collections**
   ```python
   from collections import deque
   
   class HealthMonitor:
       def __init__(self):
           self._recent_results = deque(maxlen=1000)  # Bounded
           self._instance_states = LRUCache(maxsize=100)
   ```

2. **Periodic Cleanup**
   ```python
   def _main_loop(self):
       cleanup_counter = 0
       
       while not self._stop_event.is_set():
           self._check_all_instances()
           
           cleanup_counter += 1
           if cleanup_counter >= 360:  # Every hour @ 10s interval
               self._cleanup_old_data()
               cleanup_counter = 0
           
           self._stop_event.wait(timeout=self.check_interval)
   ```

3. **Memory Monitoring**
   ```python
   def log_memory_usage():
       import psutil
       process = psutil.Process()
       mem = process.memory_info()
       logger.info(f"Memory: RSS={mem.rss / 1e6:.1f}MB, VMS={mem.vms / 1e6:.1f}MB")
   ```

**Contingency:**
- Scheduled daemon restart (e.g., daily)
- Memory threshold auto-restart

**Owner:** Development Team  
**Status:** Open

---

### R8: Process Termination Race Condition

| Attribute | Value |
|-----------|-------|
| **ID** | R8 |
| **Category** | Concurrency |
| **Description** | Race condition between health check detecting failure and manual stop command |
| **Probability** | 2 (Unlikely) |
| **Impact** | 2 (Minor) |
| **Risk Score** | **4 (Low)** |
| **Phase** | 1.2, 2.3 |

**Triggers:**
- User runs `down` while daemon is restarting instance
- Rapid start/stop cycles

**Mitigation Strategies:**

1. **State Locking**
   ```python
   def restart_instance(name: str):
       with instance_lock(name):
           stop_instance(name)
           start_instance(name)
   ```

2. **Desired State Pattern**
   ```python
   def request_stop(name: str):
       """Set desired state, let reconciler handle actual stop."""
       with get_db_connection() as conn:
           conn.execute(
               "UPDATE instances SET desired_state = 'stopped' WHERE name = ?",
               (name,)
           )
       # Reconciler will stop the process
   ```

**Contingency:**
- Retry stop command
- Force kill with `--force`

**Owner:** Development Team  
**Status:** Open

---

## Risk Summary

| ID | Risk | Score | Priority | Mitigation Status |
|----|------|-------|----------|-------------------|
| R1 | Schema Migration Data Loss | 12 | Medium-High | Planned |
| R2 | psutil Compatibility | 8 | Medium | Planned |
| R3 | File Locking Deadlock | 6 | Medium | Planned |
| R4 | Windows Service Failure | 6 | Medium | Planned |
| R5 | Config Backward Compat | 6 | Medium | Planned |
| R6 | Log Disk Exhaustion | 9 | Medium | Planned |
| R7 | Daemon Memory Leak | 6 | Medium | Planned |
| R8 | Termination Race | 4 | Low | Planned |

### Risk Heat Map

```
     Impact →
P    1   2   3   4   5
r  ┌───┬───┬───┬───┬───┐
o 5│   │   │   │   │   │
b  ├───┼───┼───┼───┼───┤
a 4│   │   │   │   │   │
b  ├───┼───┼───┼───┼───┤
i 3│   │   │R6 │R1 │   │
l  ├───┼───┼───┼───┼───┤
i 2│   │R8 │R3,│R2 │   │
t  │   │   │R4,│   │   │
y  │   │   │R5,│   │   │
↓  │   │   │R7 │   │   │
  ├───┼───┼───┼───┼───┤
  1│   │   │   │   │   │
   └───┴───┴───┴───┴───┘
```

---

## Review Schedule

| Review | Date | Reviewer | Status |
|--------|------|----------|--------|
| Initial Risk Assessment | 2026-01-01 | Auto-generated | Complete |
| Post-Phase 1 Review | TBD | Dev Lead | Pending |
| Post-Phase 2 Review | TBD | Dev Lead | Pending |
| Pre-Release Review | TBD | All | Pending |

---

*Last updated: 2026-01-01*
