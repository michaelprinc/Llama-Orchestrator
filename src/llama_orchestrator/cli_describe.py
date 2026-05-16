"""
Describe command utilities for enhanced instance information.

Provides detailed information about instances including V2 features:
- Runtime state from SQLite
- Recent events log
- Health probe status
- Process validation status
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llama_orchestrator.config import get_state_dir

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig
    from llama_orchestrator.engine.state import RuntimeState

logger = logging.getLogger(__name__)


def _timestamp_to_datetime(timestamp: float | None) -> datetime | None:
    """Convert a POSIX timestamp to datetime when present."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp)


def _coerce_datetime(value: object) -> datetime | None:
    """Normalize timestamps or datetime values to datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value)
    return None


def _enum_or_value(value: object) -> str:
    """Return enum value when present, otherwise a string representation."""
    if value is None:
        return "unknown"
    return str(getattr(value, "value", value))


def _resolve_process_valid(validation: object) -> bool:
    """Support both method-based and attribute-based validation results."""
    is_valid = getattr(validation, "is_valid", None)
    if callable(is_valid):
        return bool(is_valid())
    if is_valid is not None:
        return bool(is_valid)
    return False


def _get_numeric_attr(obj: object, *names: str, default: int = 0) -> int:
    """Return the first real numeric attribute from a list of candidate names."""
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return int(value)
    return default


@dataclass
class InstanceDescription:
    """Complete description of an instance."""
    
    # Basic info
    name: str
    config_path: Path | None = None
    
    # Configuration
    model_path: str | None = None
    context_size: int = 0
    batch_size: int = 0
    threads: int = 0
    port: int = 0
    host: str = ""
    gpu_backend: str = ""
    gpu_device: int = 0
    gpu_layers: int = 0
    effective_command: str | None = None
    
    # Runtime state (V2)
    pid: int | None = None
    status: str = "unknown"
    health: str = "unknown"
    started_at: datetime | None = None
    uptime_seconds: float = 0
    restart_count: int = 0
    memory_percent: float | None = None
    memory_rss_mb: float | None = None
    
    # V2 specific
    config_hash: str | None = None
    binary_version: str | None = None
    last_health_check: datetime | None = None
    last_health_latency_ms: float | None = None
    
    # Process validation
    process_valid: bool = False
    process_exists: bool = False
    process_cmdline: str | None = None
    
    # Events (recent)
    recent_events: list[dict] = field(default_factory=list)
    health_history: list[dict] = field(default_factory=list)
    
    # Paths
    stdout_log: str = ""
    stderr_log: str = ""
    state_db_path: str = ""
    lock_file_path: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "name": self.name,
            "config_path": str(self.config_path) if self.config_path else None,
            "configuration": {
                "model_path": self.model_path,
                "context_size": self.context_size,
                "batch_size": self.batch_size,
                "threads": self.threads,
                "port": self.port,
                "host": self.host,
                "gpu": {
                    "backend": self.gpu_backend,
                    "device": self.gpu_device,
                    "layers": self.gpu_layers,
                },
                "effective_command": self.effective_command,
            },
            "runtime": {
                "pid": self.pid,
                "status": self.status,
                "health": self.health,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "uptime_seconds": self.uptime_seconds,
                "restart_count": self.restart_count,
                "memory_percent": self.memory_percent,
                "memory_rss_mb": self.memory_rss_mb,
                "config_hash": self.config_hash,
                "binary_version": self.binary_version,
                "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
                "last_health_latency_ms": self.last_health_latency_ms,
            },
            "process": {
                "valid": self.process_valid,
                "exists": self.process_exists,
                "cmdline": self.process_cmdline,
            },
            "events": self.recent_events,
            "health_history": self.health_history,
            "paths": {
                "stdout_log": self.stdout_log,
                "stderr_log": self.stderr_log,
                "state_db": self.state_db_path,
                "lock_file": self.lock_file_path,
            },
        }
    
    @property
    def uptime_str(self) -> str:
        """Get human-readable uptime string."""
        if self.uptime_seconds <= 0:
            return "-"
        
        seconds = int(self.uptime_seconds)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        
        return " ".join(parts[:2])  # Show at most 2 units
    
    @property
    def status_color(self) -> str:
        """Get Rich color for status."""
        colors = {
            "running": "green",
            "stopped": "dim",
            "crashed": "red",
            "starting": "yellow",
            "stopping": "yellow",
            "unknown": "dim",
        }
        return colors.get(self.status.lower(), "dim")
    
    @property
    def health_color(self) -> str:
        """Get Rich color for health."""
        colors = {
            "healthy": "green",
            "unhealthy": "red",
            "degraded": "yellow",
            "unknown": "dim",
        }
        return colors.get(self.health.lower(), "dim")


def build_description(
    name: str,
    config: "InstanceConfig | None" = None,
    runtime: "RuntimeState | None" = None,
    include_events: bool = True,
    event_limit: int = 10,
) -> InstanceDescription:
    """
    Build a complete instance description.
    
    Args:
        name: Instance name
        config: Instance configuration (optional)
        runtime: Runtime state from V2 DB (optional)
        include_events: Whether to include recent events
        event_limit: Maximum number of events to include
        
    Returns:
        InstanceDescription with all available information
    """
    from llama_orchestrator.engine.command import build_command
    from llama_orchestrator.engine.process import get_process_info
    from llama_orchestrator.engine.state import get_health_history, get_recent_events, load_runtime
    from llama_orchestrator.engine.validator import validate_process
    
    desc = InstanceDescription(name=name)
    
    # Fill in config info
    if config:
        desc.config_path = Path(f"instances/{name}/config.json")
        desc.model_path = str(config.model.path)
        desc.context_size = config.model.context_size
        desc.batch_size = config.model.batch_size
        desc.threads = config.model.threads
        desc.port = config.server.port
        desc.host = config.server.host
        desc.gpu_backend = config.gpu.backend
        desc.gpu_device = config.gpu.device_id
        desc.gpu_layers = config.gpu.layers
        try:
            desc.effective_command = " ".join(build_command(config))
        except Exception as e:
            logger.debug(f"Could not build effective command for {name}: {e}")
        desc.stdout_log = config.logs.stdout.format(name=name)
        desc.stderr_log = config.logs.stderr.format(name=name)
    
    # Get or use provided runtime state
    if runtime is None:
        try:
            runtime = load_runtime(name)
        except Exception as e:
            logger.debug(f"Could not load runtime for {name}: {e}")
    
    if runtime:
        desc.pid = runtime.pid
        desc.status = _enum_or_value(getattr(runtime, "status", None))
        desc.health = _enum_or_value(getattr(runtime, "health", None))
        desc.started_at = _coerce_datetime(getattr(runtime, "started_at", None))
        desc.restart_count = _get_numeric_attr(runtime, "restart_attempts", "restart_count")
        desc.config_hash = getattr(runtime, "config_hash", None)
        desc.binary_version = getattr(runtime, "binary_version", None)
        desc.last_health_check = _coerce_datetime(
            getattr(runtime, "last_seen_at", getattr(runtime, "last_health_check", None))
        )
        desc.last_health_latency_ms = getattr(runtime, "last_health_latency_ms", None)
        
        # Calculate uptime
        if desc.started_at:
            desc.uptime_seconds = (datetime.now() - desc.started_at).total_seconds()
        
        # Validate process
        if runtime.pid:
            try:
                validation = validate_process(name, expected_pid=runtime.pid)
                desc.process_valid = _resolve_process_valid(validation)
                desc.process_exists = bool(
                    getattr(validation, "process_running", getattr(validation, "exists", False))
                )
                desc.process_cmdline = getattr(
                    validation,
                    "actual_cmdline",
                    getattr(validation, "cmdline", None),
                )
            except Exception as e:
                logger.debug(f"Could not validate process: {e}")

            try:
                process_info = get_process_info(runtime.pid)
                if process_info:
                    memory_percent = process_info.get("memory_percent")
                    memory_rss = process_info.get("memory_rss")
                    if isinstance(memory_percent, int | float):
                        desc.memory_percent = float(memory_percent)
                    if isinstance(memory_rss, int | float):
                        desc.memory_rss_mb = float(memory_rss) / (1024 * 1024)
            except Exception as e:
                logger.debug(f"Could not gather process memory info: {e}")
    
    # Get recent events
    if include_events:
        try:
            events = get_recent_events(name, limit=event_limit)
            desc.recent_events = [
                {
                    "timestamp": e.timestamp.isoformat() if hasattr(e, 'timestamp') else str(e.get('timestamp')),
                    "type": e.event_type if hasattr(e, 'event_type') else e.get('event_type'),
                    "message": e.message if hasattr(e, 'message') else e.get('message'),
                }
                for e in events
            ]
        except Exception as e:
            logger.debug(f"Could not get events: {e}")

        try:
            health_history = get_health_history(name, limit=5)
            desc.health_history = [
                {
                    "health": entry.get("health"),
                    "response_time_ms": entry.get("response_time_ms"),
                    "error_message": entry.get("error_message"),
                    "checked_at": _coerce_datetime(entry.get("checked_at")).isoformat()
                    if _coerce_datetime(entry.get("checked_at"))
                    else None,
                }
                for entry in health_history
            ]
        except Exception as e:
            logger.debug(f"Could not get health history: {e}")
    
    # Set paths
    desc.state_db_path = str(get_state_dir() / "state.sqlite")
    desc.lock_file_path = f"state/{name}.lock"
    
    return desc


def format_description_rich(desc: InstanceDescription) -> str:
    """
    Format instance description for Rich panel output.
    
    Args:
        desc: Instance description
        
    Returns:
        Formatted string for Rich panel
    """
    lines = []
    
    # Configuration section
    lines.append("[bold cyan]Configuration[/bold cyan]")
    lines.append(f"  Model:        {desc.model_path or '-'}")
    lines.append(f"  Context:      {desc.context_size}")
    lines.append(f"  Batch size:   {desc.batch_size}")
    lines.append(f"  Threads:      {desc.threads}")
    lines.append("")
    lines.append(f"  Port:         {desc.port}")
    lines.append(f"  Host:         {desc.host}")
    lines.append("")
    lines.append(f"  GPU Backend:  {desc.gpu_backend}")
    lines.append(f"  GPU Device:   {desc.gpu_device}")
    lines.append(f"  GPU Layers:   {desc.gpu_layers}")
    if desc.effective_command:
        lines.append(f"  Command:      {desc.effective_command}")
    lines.append("")
    
    # Runtime section
    lines.append("[bold cyan]Runtime Status[/bold cyan]")
    lines.append(f"  Status:       [{desc.status_color}]{desc.status}[/{desc.status_color}]")
    lines.append(f"  Health:       [{desc.health_color}]{desc.health}[/{desc.health_color}]")
    lines.append(f"  PID:          {desc.pid or '-'}")
    lines.append(f"  Uptime:       {desc.uptime_str}")
    lines.append(f"  Restarts:     {desc.restart_count}")
    if desc.memory_rss_mb is not None:
        memory_details = f"{desc.memory_rss_mb:.1f} MiB"
        if desc.memory_percent is not None:
            memory_details += f" ({desc.memory_percent:.1f}%)"
        lines.append(f"  Memory:       {memory_details}")
    lines.append("")
    
    # V2 Runtime Details
    if desc.config_hash or desc.binary_version:
        lines.append("[bold cyan]Runtime Details (V2)[/bold cyan]")
        if desc.config_hash:
            lines.append(f"  Config Hash:  {desc.config_hash[:16]}...")
        if desc.binary_version:
            lines.append(f"  Binary:       {desc.binary_version}")
        if desc.last_health_check:
            lines.append(f"  Last Check:   {desc.last_health_check.strftime('%H:%M:%S')}")
        if desc.last_health_latency_ms:
            lines.append(f"  Latency:      {desc.last_health_latency_ms:.1f}ms")
        lines.append("")
    
    # Process validation
    if desc.pid:
        lines.append("[bold cyan]Process Validation[/bold cyan]")
        valid_icon = "✓" if desc.process_valid else "✗"
        valid_color = "green" if desc.process_valid else "red"
        lines.append(f"  Valid:        [{valid_color}]{valid_icon}[/{valid_color}] {desc.process_valid}")
        lines.append(f"  Exists:       {desc.process_exists}")
        if desc.process_cmdline:
            cmdline_short = desc.process_cmdline[:60] + "..." if len(desc.process_cmdline) > 60 else desc.process_cmdline
            lines.append(f"  Cmdline:      {cmdline_short}")
        lines.append("")
    
    # Recent events
    if desc.recent_events:
        lines.append("[bold cyan]Recent Events[/bold cyan]")
        for event in desc.recent_events[:5]:
            ts = event.get("timestamp", "")[:19]  # Trim to seconds
            evt_type = event.get("type", "")
            msg = event.get("message", "")[:40]
            lines.append(f"  [{ts}] {evt_type}: {msg}")
        lines.append("")

    if desc.health_history:
        lines.append("[bold cyan]Health History[/bold cyan]")
        for entry in desc.health_history[:5]:
            checked_at = (entry.get("checked_at") or "")[:19]
            response_time = entry.get("response_time_ms")
            latency = f" ({response_time:.1f}ms)" if isinstance(response_time, int | float) else ""
            lines.append(f"  [{checked_at}] {entry.get('health', 'unknown')}{latency}")
        lines.append("")
    
    # Paths section
    lines.append("[bold cyan]Paths[/bold cyan]")
    lines.append(f"  Config:       instances/{desc.name}/config.json")
    lines.append(f"  Stdout:       {desc.stdout_log}")
    lines.append(f"  Stderr:       {desc.stderr_log}")
    lines.append(f"  State DB:     {desc.state_db_path}")
    
    return "\n".join(lines)
