"""
State management for llama-orchestrator.

Uses SQLite to persist instance state (PID, status, health, etc.)

V2 Schema:
- instances: Basic instance configuration reference
- runtime: Live process state (PID, port, cmdline, health)
- events: Audit log for all instance operations
- health_history: Health check history
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from llama_orchestrator.config import get_state_dir

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Schema version for migration tracking
SCHEMA_VERSION = 2


class InstanceStatus(Enum):
    """Status of an instance."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class HealthStatus(Enum):
    """Health status of a running instance."""
    UNKNOWN = "unknown"
    LOADING = "loading"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    ERROR = "error"


class DesiredState(Enum):
    """Desired state for reconciliation."""
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass
class RuntimeState:
    """Extended runtime state for V2 schema."""
    
    name: str
    pid: int | None = None
    port: int | None = None
    cmdline: str = ""
    binary_version: str = ""
    status: InstanceStatus = InstanceStatus.STOPPED
    health: HealthStatus = HealthStatus.UNKNOWN
    started_at: float | None = None
    last_seen_at: float | None = None
    last_health_ok_at: float | None = None
    restart_attempts: int = 0
    last_exit_code: int | None = None
    last_error: str = ""


@dataclass
class InstanceState:
    """Runtime state of an instance."""
    
    name: str
    pid: int | None = None
    status: InstanceStatus = InstanceStatus.STOPPED
    health: HealthStatus = HealthStatus.UNKNOWN
    start_time: float | None = None
    last_health_check: float | None = None
    restart_count: int = 0
    config_hash: str = ""
    error_message: str = ""
    
    @property
    def uptime(self) -> float | None:
        """Get uptime in seconds, or None if not running."""
        if self.start_time is None:
            return None
        return time.time() - self.start_time
    
    @property
    def uptime_str(self) -> str:
        """Get formatted uptime string."""
        uptime = self.uptime
        if uptime is None:
            return "-"
        
        if uptime < 60:
            return f"{int(uptime)}s"
        elif uptime < 3600:
            return f"{int(uptime // 60)}m {int(uptime % 60)}s"
        else:
            hours = int(uptime // 3600)
            minutes = int((uptime % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    @property
    def status_symbol(self) -> str:
        """Get status indicator symbol."""
        return {
            InstanceStatus.STOPPED: "○",
            InstanceStatus.STARTING: "◐",
            InstanceStatus.RUNNING: "●",
            InstanceStatus.STOPPING: "◑",
            InstanceStatus.ERROR: "✗",
        }.get(self.status, "?")
    
    @property
    def health_symbol(self) -> str:
        """Get health indicator symbol."""
        return {
            HealthStatus.UNKNOWN: "?",
            HealthStatus.LOADING: "◐",
            HealthStatus.HEALTHY: "●",
            HealthStatus.UNHEALTHY: "◑",
            HealthStatus.ERROR: "✗",
        }.get(self.health, "?")


def get_db_path() -> Path:
    """Get the path to the state database."""
    return get_state_dir() / "state.sqlite"


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    """Get a database connection with proper cleanup."""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    
    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema with V2 support."""
    with get_db_connection() as conn:
        # Check and perform migration if needed
        current_version = _get_schema_version(conn)
        
        if current_version < SCHEMA_VERSION:
            _migrate_schema(conn, current_version, SCHEMA_VERSION)
        
        # V1 table (kept for backward compatibility)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instances (
                name TEXT PRIMARY KEY,
                pid INTEGER,
                status TEXT NOT NULL DEFAULT 'stopped',
                health TEXT NOT NULL DEFAULT 'unknown',
                start_time REAL,
                last_health_check REAL,
                restart_count INTEGER NOT NULL DEFAULT 0,
                config_hash TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        
        # V2: Runtime table (extended process state)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime (
                name TEXT PRIMARY KEY,
                pid INTEGER,
                port INTEGER,
                cmdline TEXT NOT NULL DEFAULT '',
                binary_version TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'stopped',
                health TEXT NOT NULL DEFAULT 'unknown',
                started_at REAL,
                last_seen_at REAL,
                last_health_ok_at REAL,
                restart_attempts INTEGER NOT NULL DEFAULT 0,
                last_exit_code INTEGER,
                last_error TEXT NOT NULL DEFAULT ''
            )
        """)
        
        # V2: Events table (audit log)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                instance_name TEXT,
                level TEXT NOT NULL DEFAULT 'info',
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                meta_json TEXT
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_instance 
            ON events(instance_name, ts DESC)
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_level 
            ON events(level, ts DESC)
        """)
        
        # V1: Health history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_name TEXT NOT NULL,
                health TEXT NOT NULL,
                response_time_ms REAL,
                error_message TEXT,
                checked_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (instance_name) REFERENCES instances(name) ON DELETE CASCADE
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_health_history_instance 
            ON health_history(instance_name, checked_at DESC)
        """)
        
        # V2: Schema info table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # Update schema version
        conn.execute("""
            INSERT OR REPLACE INTO schema_info (key, value)
            VALUES ('version', ?)
        """, (str(SCHEMA_VERSION),))
        
        conn.commit()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database."""
    try:
        row = conn.execute(
            "SELECT value FROM schema_info WHERE key = 'version'"
        ).fetchone()
        return int(row[0]) if row else 1
    except sqlite3.OperationalError:
        # Table doesn't exist = V1
        return 1


def _backup_database() -> Path | None:
    """Create backup of database before migration."""
    db_path = get_db_path()
    if not db_path.exists():
        return None
    
    backup_path = db_path.with_suffix(f".sqlite.v{_get_schema_version_from_file()}.backup")
    shutil.copy2(db_path, backup_path)
    logger.info(f"Created database backup at {backup_path}")
    return backup_path


def _get_schema_version_from_file() -> int:
    """Get schema version by connecting to existing DB."""
    db_path = get_db_path()
    if not db_path.exists():
        return 0
    
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'version'"
            ).fetchone()
            return int(row[0]) if row else 1
        except sqlite3.OperationalError:
            return 1
        finally:
            conn.close()
    except Exception:
        return 1


def _migrate_schema(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """Migrate database schema between versions."""
    logger.info(f"Migrating database schema from v{from_version} to v{to_version}")
    
    # Backup before migration
    _backup_database()
    
    if from_version < 2 and to_version >= 2:
        _migrate_v1_to_v2(conn)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from V1 to V2 schema."""
    logger.info("Migrating V1 -> V2: Adding runtime and events tables")
    
    # V2 tables will be created by init_db
    # Copy existing instance data to runtime table if instances table exists
    try:
        rows = conn.execute("SELECT * FROM instances").fetchall()
        for row in rows:
            conn.execute("""
                INSERT OR IGNORE INTO runtime (
                    name, pid, port, status, health, started_at, 
                    restart_attempts, last_error
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)
            """, (
                row["name"],
                row["pid"],
                row["status"],
                row["health"],
                row["start_time"],
                row["restart_count"],
                row["error_message"],
            ))
        logger.info(f"Migrated {len(rows)} instance records to runtime table")
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not migrate instances: {e}")
    
    conn.commit()


def save_state(state: InstanceState) -> None:
    """Save instance state to database."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO instances (
                name, pid, status, health, start_time, 
                last_health_check, restart_count, config_hash, error_message, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                pid = excluded.pid,
                status = excluded.status,
                health = excluded.health,
                start_time = excluded.start_time,
                last_health_check = excluded.last_health_check,
                restart_count = excluded.restart_count,
                config_hash = excluded.config_hash,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
        """, (
            state.name,
            state.pid,
            state.status.value,
            state.health.value,
            state.start_time,
            state.last_health_check,
            state.restart_count,
            state.config_hash,
            state.error_message,
            time.time(),
        ))
        conn.commit()


def load_state(name: str) -> InstanceState | None:
    """Load instance state from database."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE name = ?", (name,)
        ).fetchone()
        
        if row is None:
            return None
        
        return InstanceState(
            name=row["name"],
            pid=row["pid"],
            status=InstanceStatus(row["status"]),
            health=HealthStatus(row["health"]),
            start_time=row["start_time"],
            last_health_check=row["last_health_check"],
            restart_count=row["restart_count"],
            config_hash=row["config_hash"],
            error_message=row["error_message"],
        )


def load_all_states() -> dict[str, InstanceState]:
    """Load all instance states from database."""
    states = {}
    
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM instances ORDER BY name").fetchall()
        
        for row in rows:
            states[row["name"]] = InstanceState(
                name=row["name"],
                pid=row["pid"],
                status=InstanceStatus(row["status"]),
                health=HealthStatus(row["health"]),
                start_time=row["start_time"],
                last_health_check=row["last_health_check"],
                restart_count=row["restart_count"],
                config_hash=row["config_hash"],
                error_message=row["error_message"],
            )
    
    return states


def delete_state(name: str) -> bool:
    """Delete instance state from database."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM instances WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0


def record_health_check(
    name: str, 
    health: HealthStatus, 
    response_time_ms: float | None = None,
    error_message: str = "",
) -> None:
    """Record a health check result."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO health_history (instance_name, health, response_time_ms, error_message)
            VALUES (?, ?, ?, ?)
        """, (name, health.value, response_time_ms, error_message))
        
        # Also update the main instance state
        conn.execute("""
            UPDATE instances 
            SET health = ?, last_health_check = ?, updated_at = ?
            WHERE name = ?
        """, (health.value, time.time(), time.time(), name))
        
        conn.commit()


def get_health_history(name: str, limit: int = 10) -> list[dict]:
    """Get recent health check history for an instance."""
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT health, response_time_ms, error_message, checked_at
            FROM health_history
            WHERE instance_name = ?
            ORDER BY checked_at DESC
            LIMIT ?
        """, (name, limit)).fetchall()
        
        return [dict(row) for row in rows]


# =============================================================================
# V2: Runtime State Functions
# =============================================================================


def save_runtime(runtime: RuntimeState) -> None:
    """Save runtime state to V2 runtime table."""
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO runtime (
                name, pid, port, cmdline, binary_version, status, health,
                started_at, last_seen_at, last_health_ok_at, restart_attempts,
                last_exit_code, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                pid = excluded.pid,
                port = excluded.port,
                cmdline = excluded.cmdline,
                binary_version = excluded.binary_version,
                status = excluded.status,
                health = excluded.health,
                started_at = excluded.started_at,
                last_seen_at = excluded.last_seen_at,
                last_health_ok_at = excluded.last_health_ok_at,
                restart_attempts = excluded.restart_attempts,
                last_exit_code = excluded.last_exit_code,
                last_error = excluded.last_error
        """, (
            runtime.name,
            runtime.pid,
            runtime.port,
            runtime.cmdline,
            runtime.binary_version,
            runtime.status.value,
            runtime.health.value,
            runtime.started_at,
            runtime.last_seen_at,
            runtime.last_health_ok_at,
            runtime.restart_attempts,
            runtime.last_exit_code,
            runtime.last_error,
        ))
        conn.commit()


def load_runtime(name: str) -> RuntimeState | None:
    """Load runtime state from V2 runtime table."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM runtime WHERE name = ?", (name,)
        ).fetchone()
        
        if row is None:
            return None
        
        return RuntimeState(
            name=row["name"],
            pid=row["pid"],
            port=row["port"],
            cmdline=row["cmdline"],
            binary_version=row["binary_version"],
            status=InstanceStatus(row["status"]),
            health=HealthStatus(row["health"]),
            started_at=row["started_at"],
            last_seen_at=row["last_seen_at"],
            last_health_ok_at=row["last_health_ok_at"],
            restart_attempts=row["restart_attempts"],
            last_exit_code=row["last_exit_code"],
            last_error=row["last_error"],
        )


def load_all_runtime() -> dict[str, RuntimeState]:
    """Load all runtime states from V2 runtime table."""
    states = {}
    
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM runtime ORDER BY name").fetchall()
        
        for row in rows:
            states[row["name"]] = RuntimeState(
                name=row["name"],
                pid=row["pid"],
                port=row["port"],
                cmdline=row["cmdline"],
                binary_version=row["binary_version"],
                status=InstanceStatus(row["status"]),
                health=HealthStatus(row["health"]),
                started_at=row["started_at"],
                last_seen_at=row["last_seen_at"],
                last_health_ok_at=row["last_health_ok_at"],
                restart_attempts=row["restart_attempts"],
                last_exit_code=row["last_exit_code"],
                last_error=row["last_error"],
            )
    
    return states


def update_runtime_seen(name: str) -> None:
    """Update last_seen_at timestamp for an instance."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE runtime SET last_seen_at = ? WHERE name = ?",
            (time.time(), name)
        )
        conn.commit()


def delete_runtime(name: str) -> bool:
    """Delete runtime state from database."""
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM runtime WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0


# =============================================================================
# V2: Event Functions
# =============================================================================


def log_event(
    event_type: str,
    message: str,
    instance_name: str | None = None,
    level: str = "info",
    meta: dict | None = None,
) -> int:
    """
    Log an event to the database.
    
    Args:
        event_type: Type of event (started, stopped, health_change, restart, error)
        message: Human-readable message
        instance_name: Associated instance (optional)
        level: Log level (info, warning, error)
        meta: Additional metadata as dict
        
    Returns:
        Event ID
    """
    with get_db_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO events (instance_name, level, event_type, message, meta_json)
            VALUES (?, ?, ?, ?, ?)
        """, (
            instance_name,
            level,
            event_type,
            message,
            json.dumps(meta) if meta else None,
        ))
        conn.commit()
        return cursor.lastrowid or 0


def get_recent_events(
    instance_name: str | None = None,
    level: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Get recent events from the database.
    
    Args:
        instance_name: Filter by instance (optional)
        level: Filter by log level (optional)
        limit: Maximum number of events to return
        
    Returns:
        List of event dictionaries
    """
    with get_db_connection() as conn:
        query = "SELECT * FROM events WHERE 1=1"
        params: list = []
        
        if instance_name:
            query += " AND instance_name = ?"
            params.append(instance_name)
        
        if level:
            query += " AND level = ?"
            params.append(level)
        
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        
        events = []
        for row in rows:
            event = dict(row)
            if event.get("meta_json"):
                try:
                    event["meta"] = json.loads(event["meta_json"])
                except json.JSONDecodeError:
                    event["meta"] = {}
            else:
                event["meta"] = {}
            del event["meta_json"]
            events.append(event)
        
        return events


def cleanup_old_events(retention_days: int = 7) -> int:
    """
    Delete events older than retention period.
    
    Args:
        retention_days: Number of days to keep events
        
    Returns:
        Number of events deleted
    """
    cutoff = time.time() - (retention_days * 86400)
    
    with get_db_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM events WHERE ts < ?",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount


def get_schema_version() -> int:
    """Get current database schema version."""
    return _get_schema_version_from_file()


# Initialize database on module import
init_db()
