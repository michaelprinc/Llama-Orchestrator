"""
Process management for llama-orchestrator.

Handles starting, stopping, and monitoring llama-server processes.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from llama_orchestrator.config import (
    ConfigLoadError,
    get_instance_config,
    get_logs_dir,
    get_project_root,
)
from llama_orchestrator.engine.command import build_command, build_env, validate_executable
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
    delete_state,
    load_state,
    save_state,
)

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig


class ProcessError(Exception):
    """Error during process management."""
    
    def __init__(self, instance: str, message: str, cause: Exception | None = None):
        self.instance = instance
        self.message = message
        self.cause = cause
        super().__init__(f"[{instance}] {message}")


def get_log_files(name: str) -> tuple[Path, Path]:
    """Get log file paths for an instance."""
    logs_dir = get_logs_dir()
    instance_log_dir = logs_dir / name
    instance_log_dir.mkdir(parents=True, exist_ok=True)
    
    stdout_log = instance_log_dir / "stdout.log"
    stderr_log = instance_log_dir / "stderr.log"
    
    return stdout_log, stderr_log


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def get_process_info(pid: int) -> dict | None:
    """Get information about a running process."""
    try:
        proc = psutil.Process(pid)
        return {
            "pid": pid,
            "name": proc.name(),
            "status": proc.status(),
            "create_time": proc.create_time(),
            "cmdline": proc.cmdline(),
            "memory_percent": proc.memory_percent(),
            "cpu_percent": proc.cpu_percent(),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def kill_process_tree(pid: int, timeout: float = 10.0) -> bool:
    """
    Kill a process and all its children.
    
    Args:
        pid: Process ID to kill
        timeout: Timeout for graceful shutdown before force kill
        
    Returns:
        True if process was killed, False if not found
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    
    # Get all children first
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    
    # Terminate parent first
    try:
        parent.terminate()
    except psutil.NoSuchProcess:
        pass
    
    # Terminate all children
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    
    # Wait for graceful shutdown
    gone, alive = psutil.wait_procs([parent] + children, timeout=timeout)
    
    # Force kill any remaining
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    
    return True


def check_stale_state(state: InstanceState) -> InstanceState:
    """
    Check if state is stale (process died but state shows running).
    
    Updates and returns the corrected state.
    """
    if state.status in (InstanceStatus.RUNNING, InstanceStatus.STARTING):
        if state.pid is None or not is_process_running(state.pid):
            # Process is gone but state says running - mark as stopped
            state.status = InstanceStatus.STOPPED
            state.pid = None
            state.health = HealthStatus.UNKNOWN
            state.error_message = "Process died unexpectedly"
            save_state(state)
    
    return state


def start_instance(name: str, wait_for_ready: bool = True) -> InstanceState:
    """
    Start a llama-server instance.
    
    Args:
        name: Instance name to start
        wait_for_ready: Wait for the server to become ready
        
    Returns:
        Updated instance state
        
    Raises:
        ProcessError: If instance cannot be started
    """
    # Validate executable exists
    exe_valid, exe_msg = validate_executable()
    if not exe_valid:
        raise ProcessError(name, exe_msg)
    
    # Load config
    try:
        config = get_instance_config(name)
    except ConfigLoadError as e:
        raise ProcessError(name, f"Failed to load config: {e.message}", e) from e
    
    # Check current state
    state = load_state(name)
    if state is not None:
        state = check_stale_state(state)
        if state.status == InstanceStatus.RUNNING:
            raise ProcessError(name, f"Instance is already running (PID: {state.pid})")
    else:
        state = InstanceState(name=name)
    
    # Build command and environment
    cmd = build_command(config)
    env = build_env(config)
    
    # Get log files
    stdout_log, stderr_log = get_log_files(name)
    
    # Update state to starting
    state.status = InstanceStatus.STARTING
    state.health = HealthStatus.UNKNOWN
    state.error_message = ""
    save_state(state)
    
    try:
        # Open log files
        stdout_file = open(stdout_log, "a", encoding="utf-8")
        stderr_file = open(stderr_log, "a", encoding="utf-8")
        
        # Write startup marker
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        stdout_file.write(f"\n{'='*60}\n")
        stdout_file.write(f"Starting instance at {timestamp}\n")
        stdout_file.write(f"Command: {' '.join(cmd)}\n")
        stdout_file.write(f"{'='*60}\n\n")
        stdout_file.flush()
        
        # Start the process
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            cwd=str(get_project_root()),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # Windows: allow Ctrl+Break
        )
        
        # Update state
        state.pid = proc.pid
        state.start_time = time.time()
        state.status = InstanceStatus.RUNNING
        state.health = HealthStatus.LOADING
        save_state(state)
        
        # Brief wait to check if process started successfully
        time.sleep(0.5)
        
        if proc.poll() is not None:
            # Process exited immediately
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = f"Process exited with code {proc.returncode}"
            save_state(state)
            raise ProcessError(name, state.error_message)
        
        return state
        
    except Exception as e:
        # Update state on failure
        state.status = InstanceStatus.ERROR
        state.health = HealthStatus.ERROR
        state.error_message = str(e)
        save_state(state)
        
        if not isinstance(e, ProcessError):
            raise ProcessError(name, f"Failed to start: {e}", e) from e
        raise


def stop_instance(name: str, force: bool = False, timeout: float = 10.0) -> InstanceState:
    """
    Stop a llama-server instance.
    
    Args:
        name: Instance name to stop
        force: Force kill without graceful shutdown
        timeout: Timeout for graceful shutdown
        
    Returns:
        Updated instance state
        
    Raises:
        ProcessError: If instance cannot be stopped
    """
    state = load_state(name)
    
    if state is None:
        raise ProcessError(name, "Instance not found in state")
    
    state = check_stale_state(state)
    
    if state.status == InstanceStatus.STOPPED:
        return state
    
    if state.pid is None:
        state.status = InstanceStatus.STOPPED
        state.health = HealthStatus.UNKNOWN
        save_state(state)
        return state
    
    # Update state to stopping
    state.status = InstanceStatus.STOPPING
    save_state(state)
    
    # Kill the process
    killed = kill_process_tree(state.pid, timeout=0 if force else timeout)
    
    # Update state
    state.pid = None
    state.status = InstanceStatus.STOPPED
    state.health = HealthStatus.UNKNOWN
    save_state(state)
    
    # Write to log
    stdout_log, _ = get_log_files(name)
    try:
        with open(stdout_log, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n{'='*60}\n")
            f.write(f"Instance stopped at {timestamp}\n")
            f.write(f"{'='*60}\n\n")
    except OSError:
        pass
    
    return state


def restart_instance(name: str, force: bool = False) -> InstanceState:
    """
    Restart a llama-server instance.
    
    Args:
        name: Instance name to restart
        force: Force kill without graceful shutdown
        
    Returns:
        Updated instance state
    """
    state = load_state(name)
    
    # Increment restart count
    restart_count = 0
    if state is not None:
        restart_count = state.restart_count + 1
    
    # Stop if running
    try:
        stop_instance(name, force=force)
    except ProcessError:
        pass  # May not be running
    
    # Small delay between stop and start
    time.sleep(0.5)
    
    # Start
    state = start_instance(name)
    state.restart_count = restart_count
    save_state(state)
    
    return state


def get_instance_status(name: str) -> InstanceState:
    """
    Get current status of an instance.
    
    Returns a corrected state (checks for stale PIDs).
    """
    state = load_state(name)
    
    if state is None:
        return InstanceState(name=name, status=InstanceStatus.STOPPED)
    
    return check_stale_state(state)


def list_instances() -> dict[str, InstanceState]:
    """
    List all instances with their current status.
    
    Returns:
        Dictionary of instance name -> state
    """
    from llama_orchestrator.config import discover_instances
    from llama_orchestrator.engine.state import load_all_states
    
    states = load_all_states()
    
    # Also include instances that have configs but no state yet
    for name, _ in discover_instances():
        if name not in states:
            states[name] = InstanceState(name=name, status=InstanceStatus.STOPPED)
    
    # Check for stale states
    for name, state in states.items():
        states[name] = check_stale_state(state)
    
    return states
