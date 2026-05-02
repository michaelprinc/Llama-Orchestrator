"""
Process validator module for Llama Orchestrator V2.

Provides robust process validation to ensure orphaned processes are detected
and state is consistent with running processes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import psutil

from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    load_runtime,
    log_event,
    save_runtime,
)

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Status of process validation."""
    
    VALID = "valid"           # Process exists and matches expected state
    MISSING = "missing"       # Process doesn't exist
    PID_MISMATCH = "pid_mismatch"  # PID exists but is different process
    ZOMBIE = "zombie"         # Process is zombie/defunct
    STALE = "stale"          # Process hasn't been seen recently


@dataclass
class ProcessValidation:
    """Result of process validation."""
    
    status: ValidationStatus
    expected_pid: int | None
    actual_pid: int | None
    expected_cmdline: str | None
    actual_cmdline: str | None
    process_running: bool
    process_responding: bool
    last_seen_age_seconds: float | None
    message: str
    
    def is_valid(self) -> bool:
        """Check if process validation passed."""
        return self.status == ValidationStatus.VALID
    
    def needs_cleanup(self) -> bool:
        """Check if process needs cleanup/restart."""
        return self.status in (
            ValidationStatus.MISSING,
            ValidationStatus.PID_MISMATCH,
            ValidationStatus.ZOMBIE,
        )


def get_process_info(pid: int) -> dict | None:
    """
    Get detailed information about a process.
    
    Args:
        pid: Process ID to check
        
    Returns:
        Dictionary with process info or None if process doesn't exist
    """
    try:
        proc = psutil.Process(pid)
        
        # Get various process attributes safely
        try:
            cmdline = " ".join(proc.cmdline())
        except (psutil.AccessDenied, psutil.ZombieProcess):
            cmdline = proc.name()
        
        try:
            status = proc.status()
        except psutil.AccessDenied:
            status = "unknown"
        
        try:
            create_time = proc.create_time()
        except psutil.AccessDenied:
            create_time = None
        
        try:
            cwd = proc.cwd()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            cwd = None
        
        return {
            "pid": pid,
            "cmdline": cmdline,
            "name": proc.name(),
            "status": status,
            "create_time": create_time,
            "cwd": cwd,
            "is_running": proc.is_running(),
        }
        
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        # Process exists but we can't access it
        return {
            "pid": pid,
            "cmdline": None,
            "name": None,
            "status": "access_denied",
            "create_time": None,
            "cwd": None,
            "is_running": True,
        }


def is_llama_server_process(cmdline: str | None, expected_binary: str | None = None) -> bool:
    """
    Check if cmdline looks like a llama-server process.
    
    Args:
        cmdline: Command line string to check
        expected_binary: Expected binary name to match
        
    Returns:
        True if this looks like a llama-server process
    """
    if not cmdline:
        return False
    
    cmdline_lower = cmdline.lower()
    
    # Check for llama-server binary
    if "llama-server" in cmdline_lower or "llama_server" in cmdline_lower:
        if expected_binary:
            return expected_binary.lower() in cmdline_lower
        return True
    
    # Check for common llama.cpp patterns
    if "llama.cpp" in cmdline_lower:
        return True
    
    return False


def validate_process(
    name: str,
    expected_pid: int | None = None,
    expected_cmdline: str | None = None,
    stale_threshold_seconds: float = 300.0,
) -> ProcessValidation:
    """
    Validate that an instance's process is running correctly.
    
    Args:
        name: Instance name to validate
        expected_pid: Expected PID (if None, loads from runtime state)
        expected_cmdline: Expected command line (if None, loads from runtime state)
        stale_threshold_seconds: Seconds before a process is considered stale
        
    Returns:
        ProcessValidation with validation results
    """
    # Load runtime state if needed
    runtime = load_runtime(name)
    
    if runtime is None:
        return ProcessValidation(
            status=ValidationStatus.MISSING,
            expected_pid=expected_pid,
            actual_pid=None,
            expected_cmdline=expected_cmdline,
            actual_cmdline=None,
            process_running=False,
            process_responding=False,
            last_seen_age_seconds=None,
            message=f"No runtime state found for instance '{name}'",
        )
    
    # Use runtime state values if not provided
    if expected_pid is None:
        expected_pid = runtime.pid
    if expected_cmdline is None:
        expected_cmdline = runtime.cmdline
    
    # Check if process exists
    proc_info = get_process_info(expected_pid) if expected_pid else None
    
    if proc_info is None:
        # Process doesn't exist
        log_event(
            event_type="process_missing",
            message=f"Process {expected_pid} not found for instance '{name}'",
            instance_name=name,
            level="warning",
            meta={"expected_pid": expected_pid},
        )
        
        return ProcessValidation(
            status=ValidationStatus.MISSING,
            expected_pid=expected_pid,
            actual_pid=None,
            expected_cmdline=expected_cmdline,
            actual_cmdline=None,
            process_running=False,
            process_responding=False,
            last_seen_age_seconds=_get_last_seen_age(runtime),
            message=f"Process {expected_pid} does not exist",
        )
    
    # Check for zombie process
    if proc_info["status"] == "zombie":
        log_event(
            event_type="process_zombie",
            message=f"Zombie process detected for instance '{name}'",
            instance_name=name,
            level="warning",
            meta={"pid": expected_pid},
        )
        
        return ProcessValidation(
            status=ValidationStatus.ZOMBIE,
            expected_pid=expected_pid,
            actual_pid=expected_pid,
            expected_cmdline=expected_cmdline,
            actual_cmdline=proc_info.get("cmdline"),
            process_running=False,
            process_responding=False,
            last_seen_age_seconds=_get_last_seen_age(runtime),
            message=f"Process {expected_pid} is a zombie",
        )
    
    # Check command line match
    actual_cmdline = proc_info.get("cmdline")
    if expected_cmdline and actual_cmdline:
        # Check if this is the expected process
        if not is_llama_server_process(actual_cmdline):
            log_event(
                event_type="pid_mismatch",
                message=f"PID {expected_pid} is not a llama-server process",
                instance_name=name,
                level="error",
                meta={
                    "expected_cmdline": expected_cmdline,
                    "actual_cmdline": actual_cmdline,
                },
            )
            
            return ProcessValidation(
                status=ValidationStatus.PID_MISMATCH,
                expected_pid=expected_pid,
                actual_pid=expected_pid,
                expected_cmdline=expected_cmdline,
                actual_cmdline=actual_cmdline,
                process_running=True,
                process_responding=False,
                last_seen_age_seconds=_get_last_seen_age(runtime),
                message=f"PID {expected_pid} is different process: {actual_cmdline[:100]}",
            )
    
    # Check staleness
    last_seen_age = _get_last_seen_age(runtime)
    if last_seen_age and last_seen_age > stale_threshold_seconds:
        log_event(
            event_type="process_stale",
            message=f"Process {expected_pid} not seen for {last_seen_age:.0f}s",
            instance_name=name,
            level="warning",
            meta={"last_seen_age": last_seen_age},
        )
        
        return ProcessValidation(
            status=ValidationStatus.STALE,
            expected_pid=expected_pid,
            actual_pid=expected_pid,
            expected_cmdline=expected_cmdline,
            actual_cmdline=actual_cmdline,
            process_running=True,
            process_responding=False,
            last_seen_age_seconds=last_seen_age,
            message=f"Process not seen for {last_seen_age:.0f} seconds",
        )
    
    # Process is valid
    return ProcessValidation(
        status=ValidationStatus.VALID,
        expected_pid=expected_pid,
        actual_pid=expected_pid,
        expected_cmdline=expected_cmdline,
        actual_cmdline=actual_cmdline,
        process_running=True,
        process_responding=True,  # Will be updated by health check
        last_seen_age_seconds=last_seen_age,
        message="Process is running and valid",
    )


def _get_last_seen_age(runtime: RuntimeState) -> float | None:
    """Get age in seconds since process was last seen."""
    if runtime.last_seen_at:
        return time.time() - runtime.last_seen_at
    if runtime.started_at:
        return time.time() - runtime.started_at
    return None


def find_orphaned_processes(known_instances: list[str]) -> list[dict]:
    """
    Find llama-server processes that are not in our known instances.
    
    Args:
        known_instances: List of known instance names
        
    Returns:
        List of orphaned process info dicts
    """
    orphans = []
    known_pids = set()
    
    # Get PIDs of known instances
    for name in known_instances:
        runtime = load_runtime(name)
        if runtime and runtime.pid:
            known_pids.add(runtime.pid)
    
    # Scan all processes
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = proc.info['pid']
            
            if pid in known_pids:
                continue
            
            # Get cmdline
            try:
                cmdline = " ".join(proc.info.get('cmdline') or [])
            except (psutil.AccessDenied, TypeError):
                continue
            
            # Check if this is a llama-server
            if is_llama_server_process(cmdline):
                orphans.append({
                    "pid": pid,
                    "cmdline": cmdline,
                    "name": proc.info.get('name'),
                })
                
                log_event(
                    event_type="orphan_detected",
                    message=f"Orphaned llama-server process found: PID {pid}",
                    level="warning",
                    meta={"pid": pid, "cmdline": cmdline[:200]},
                )
                
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    return orphans


def cleanup_stale_runtime(name: str, max_age_seconds: float = 3600.0) -> bool:
    """
    Check if a runtime state is stale and should be cleaned up.
    
    Args:
        name: Instance name to check
        max_age_seconds: Maximum age before considering cleanup
        
    Returns:
        True if runtime was cleaned up
    """
    from llama_orchestrator.engine.state import delete_runtime
    
    runtime = load_runtime(name)
    if runtime is None:
        return False
    
    # Validate the process
    validation = validate_process(name)
    
    if validation.status == ValidationStatus.MISSING:
        # Process is gone, check age
        age = _get_last_seen_age(runtime)
        if age and age > max_age_seconds:
            # Update status to stopped
            runtime.status = InstanceStatus.STOPPED
            runtime.health = HealthStatus.UNKNOWN
            save_runtime(runtime)
            
            log_event(
                event_type="stale_cleanup",
                message=f"Marked stale instance '{name}' as stopped",
                instance_name=name,
                level="info",
                meta={"age_seconds": age},
            )
            return True
    
    return False
