"""
Detached process management for Llama Orchestrator V2.

Provides robust process spawning with file-based logging that doesn't cause
deadlocks when the parent process exits.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from llama_orchestrator.config import get_logs_dir, get_project_root
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    log_event,
    save_runtime,
)

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig

logger = logging.getLogger(__name__)


@dataclass
class DetachResult:
    """Result of a detached process start."""
    
    success: bool
    pid: int | None
    port: int | None
    cmdline: str | None
    stdout_log: Path | None
    stderr_log: Path | None
    error: str | None = None


class LogRotator:
    """
    Manages log file rotation for instances.
    
    Keeps last N log files and rotates on startup.
    """
    
    def __init__(self, log_dir: Path, max_files: int = 5):
        self.log_dir = log_dir
        self.max_files = max_files
    
    def rotate(self, base_name: str) -> Path:
        """
        Rotate log files and return path for new log.
        
        Args:
            base_name: Base name for log file (e.g., "stdout", "stderr")
            
        Returns:
            Path to new log file
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Find existing log files
        pattern = f"{base_name}.*.log"
        existing = sorted(self.log_dir.glob(pattern), reverse=True)
        
        # Remove old files beyond max
        for old_file in existing[self.max_files - 1:]:
            try:
                old_file.unlink()
                logger.debug(f"Removed old log: {old_file}")
            except OSError as e:
                logger.warning(f"Failed to remove old log {old_file}: {e}")
        
        # Generate timestamp for new file
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        new_log = self.log_dir / f"{base_name}.{timestamp}.log"
        
        return new_log


def get_instance_log_dir(name: str) -> Path:
    """Get log directory for an instance."""
    return get_logs_dir() / name


def setup_log_files(name: str, rotate: bool = True) -> tuple[Path, Path]:
    """
    Set up log files for an instance.
    
    Args:
        name: Instance name
        rotate: Whether to rotate logs (creates new timestamped files)
        
    Returns:
        Tuple of (stdout_log, stderr_log) paths
    """
    log_dir = get_instance_log_dir(name)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    if rotate:
        rotator = LogRotator(log_dir)
        stdout_log = rotator.rotate("stdout")
        stderr_log = rotator.rotate("stderr")
    else:
        # Use fixed names (append mode)
        stdout_log = log_dir / "stdout.log"
        stderr_log = log_dir / "stderr.log"
    
    return stdout_log, stderr_log


def write_startup_marker(log_file: Path, cmd: list[str], name: str) -> None:
    """Write startup marker to log file."""
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'=' * 60}\n")
            f.write(f"[{name}] Starting at {timestamp}\n")
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"PID: {os.getpid()} (launcher)\n")
            f.write(f"{'=' * 60}\n\n")
    except OSError as e:
        logger.warning(f"Failed to write startup marker: {e}")


def write_shutdown_marker(log_file: Path, name: str, reason: str = "") -> None:
    """Write shutdown marker to log file."""
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'=' * 60}\n")
            f.write(f"[{name}] Stopped at {timestamp}\n")
            if reason:
                f.write(f"Reason: {reason}\n")
            f.write(f"{'=' * 60}\n\n")
    except OSError as e:
        logger.warning(f"Failed to write shutdown marker: {e}")


def start_detached(
    name: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    port: int | None = None,
    cwd: Path | None = None,
    rotate_logs: bool = True,
) -> DetachResult:
    """
    Start a detached process with file-based logging.
    
    This function:
    1. Opens log files
    2. Writes startup marker
    3. Spawns process with CREATE_NEW_PROCESS_GROUP
    4. Closes log file handles immediately (process has its own handles)
    5. Saves runtime state
    6. Returns result
    
    The key difference from the old approach is that we DON'T keep file handles
    open - the child process inherits them and they're closed in the parent.
    
    Args:
        name: Instance name
        cmd: Command to run as list
        env: Environment variables (merged with current env)
        port: Port the server will listen on
        cwd: Working directory
        rotate_logs: Whether to rotate logs
        
    Returns:
        DetachResult with process info
    """
    # Setup log files
    stdout_log, stderr_log = setup_log_files(name, rotate=rotate_logs)
    
    # Prepare environment
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    
    # Prepare working directory
    if cwd is None:
        cwd = get_project_root()
    
    # Write startup marker before spawning
    write_startup_marker(stdout_log, cmd, name)
    
    cmdline = " ".join(cmd)
    
    try:
        # Open log files for the child process
        # Use line buffering (buffering=1) for better real-time logging
        stdout_handle = open(stdout_log, "a", encoding="utf-8", buffering=1)
        stderr_handle = open(stderr_log, "a", encoding="utf-8", buffering=1)
        
        # Spawn the process
        # On Windows, CREATE_NEW_PROCESS_GROUP allows the process to survive
        # parent termination and receive Ctrl+Break signals
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=process_env,
            cwd=str(cwd),
            creationflags=creationflags,
            # Don't close_fds on Windows - it's not supported with redirects
            close_fds=False if sys.platform == "win32" else True,
        )
        
        pid = proc.pid
        
        # IMPORTANT: Close our handles immediately!
        # The child process has inherited its own handles, so closing ours
        # doesn't affect the child. This prevents the deadlock issue.
        stdout_handle.close()
        stderr_handle.close()
        
        logger.info(f"Started detached process: {name} (PID: {pid})")
        
        # Brief wait to check immediate crash
        time.sleep(0.3)
        
        if proc.poll() is not None:
            # Process exited immediately
            exit_code = proc.returncode
            error_msg = f"Process exited immediately with code {exit_code}"
            
            log_event(
                event_type="start_failed",
                message=error_msg,
                instance_name=name,
                level="error",
                meta={"exit_code": exit_code, "cmdline": cmdline[:200]},
            )
            
            return DetachResult(
                success=False,
                pid=None,
                port=port,
                cmdline=cmdline,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                error=error_msg,
            )
        
        # Save runtime state
        runtime = RuntimeState(
            name=name,
            pid=pid,
            port=port,
            cmdline=cmdline,
            status=InstanceStatus.RUNNING,
            health=HealthStatus.LOADING,
            started_at=time.time(),
            last_seen_at=time.time(),
        )
        save_runtime(runtime)
        
        log_event(
            event_type="started",
            message=f"Instance started (PID: {pid}, port: {port})",
            instance_name=name,
            level="info",
            meta={"pid": pid, "port": port},
        )
        
        return DetachResult(
            success=True,
            pid=pid,
            port=port,
            cmdline=cmdline,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to start detached process: {error_msg}")
        
        log_event(
            event_type="start_failed",
            message=f"Failed to start: {error_msg}",
            instance_name=name,
            level="error",
            meta={"error": error_msg},
        )
        
        return DetachResult(
            success=False,
            pid=None,
            port=port,
            cmdline=cmdline,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            error=error_msg,
        )


def stop_detached(
    name: str,
    pid: int,
    timeout: float = 10.0,
    force: bool = False,
) -> bool:
    """
    Stop a detached process gracefully.
    
    Args:
        name: Instance name (for logging)
        pid: Process ID to stop
        timeout: Timeout for graceful shutdown
        force: Force kill immediately
        
    Returns:
        True if process was stopped
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        logger.debug(f"Process {pid} not found (already stopped)")
        return True
    
    # Get children before terminating parent
    try:
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    
    # Terminate
    if force:
        # Force kill immediately
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
        
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    else:
        # Graceful shutdown
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        
        # Wait for graceful shutdown
        gone, alive = psutil.wait_procs([proc] + children, timeout=timeout)
        
        # Force kill remaining
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
    
    # Write shutdown marker
    log_dir = get_instance_log_dir(name)
    stdout_log = log_dir / "stdout.log"
    if stdout_log.exists():
        write_shutdown_marker(stdout_log, name, "stopped" if not force else "killed")
    
    log_event(
        event_type="stopped",
        message=f"Instance stopped (PID: {pid}, force: {force})",
        instance_name=name,
        level="info",
        meta={"pid": pid, "force": force},
    )
    
    return True


def get_latest_logs(name: str, lines: int = 100) -> dict[str, list[str]]:
    """
    Get latest log lines for an instance.
    
    Args:
        name: Instance name
        lines: Number of lines to return
        
    Returns:
        Dictionary with 'stdout' and 'stderr' keys containing log lines
    """
    log_dir = get_instance_log_dir(name)
    result = {"stdout": [], "stderr": []}
    
    for log_type in ["stdout", "stderr"]:
        # Find most recent log file
        pattern = f"{log_type}.*.log"
        log_files = sorted(log_dir.glob(pattern), reverse=True)
        
        # Also check fixed name
        fixed_log = log_dir / f"{log_type}.log"
        if fixed_log.exists():
            log_files.append(fixed_log)
        
        if not log_files:
            continue
        
        latest = log_files[0]
        
        try:
            with open(latest, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                result[log_type] = all_lines[-lines:]
        except OSError as e:
            logger.warning(f"Failed to read log {latest}: {e}")
    
    return result


def tail_log(name: str, log_type: str = "stdout", lines: int = 50) -> str:
    """
    Tail a log file for an instance.
    
    Args:
        name: Instance name
        log_type: "stdout" or "stderr"
        lines: Number of lines to return
        
    Returns:
        Log content as string
    """
    logs = get_latest_logs(name, lines)
    return "".join(logs.get(log_type, []))
