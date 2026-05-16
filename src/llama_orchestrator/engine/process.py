"""
Process management for llama-orchestrator.

Handles starting, stopping, and monitoring llama-server processes.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import suppress
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
from llama_orchestrator.engine.detach import start_detached
from llama_orchestrator.engine.locking import instance_lock
from llama_orchestrator.engine.logging_config import get_instance_log_handler
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
    RuntimeState,
    load_all_states,
    load_all_runtime,
    load_runtime,
    log_event,
    save_runtime,
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


def _runtime_to_state(runtime: RuntimeState) -> InstanceState:
    """Convert V2 runtime data to the legacy state shape used by the CLI."""
    return InstanceState(
        name=runtime.name,
        pid=runtime.pid,
        status=runtime.status,
        health=runtime.health,
        start_time=runtime.started_at,
        restart_count=runtime.restart_attempts,
        error_message=runtime.last_error,
    )


def _sync_runtime_from_state(
    state: InstanceState,
    config: "InstanceConfig | None" = None,
    cmdline: str = "",
    last_error: str | None = None,
) -> RuntimeState:
    """Mirror the legacy state updates into the V2 runtime table."""
    runtime = load_runtime(state.name) or RuntimeState(name=state.name)
    runtime.pid = state.pid
    runtime.port = config.server.port if config else runtime.port
    runtime.cmdline = cmdline or runtime.cmdline
    runtime.status = state.status
    runtime.health = state.health
    runtime.started_at = state.start_time if state.start_time is not None else runtime.started_at
    runtime.last_seen_at = time.time()
    runtime.restart_attempts = state.restart_count
    if state.health == HealthStatus.HEALTHY:
        runtime.last_health_ok_at = runtime.last_seen_at
    if last_error is not None:
        runtime.last_error = last_error
    elif state.error_message:
        runtime.last_error = state.error_message
    elif state.status == InstanceStatus.STOPPED:
        runtime.last_error = ""
    save_runtime(runtime)
    return runtime


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
        memory_info = proc.memory_info()
        return {
            "pid": pid,
            "name": proc.name(),
            "status": proc.status(),
            "create_time": proc.create_time(),
            "cmdline": proc.cmdline(),
            "memory_percent": proc.memory_percent(),
            "memory_rss": memory_info.rss,
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
            _sync_runtime_from_state(state, last_error=state.error_message)
    
    return state


def start_instance(name: str, wait_for_ready: bool = True, detach: bool = False) -> InstanceState:
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
    with instance_lock(name, operation="start"):
        # Load config
        try:
            config = get_instance_config(name)
        except ConfigLoadError as e:
            raise ProcessError(name, f"Failed to load config: {e.message}", e) from e

        # Validate executable exists after loading config so UUID-based binary
        # resolution works for per-instance binary selections.
        exe_valid, exe_msg = validate_executable(config)
        if not exe_valid:
            raise ProcessError(name, exe_msg)

        # Check current state
        state = load_state(name)
        if state is not None:
            state = check_stale_state(state)
            if state.status == InstanceStatus.RUNNING:
                raise ProcessError(name, f"Instance is already running (PID: {state.pid})")
        else:
            runtime = load_runtime(name)
            state = _runtime_to_state(runtime) if runtime is not None else InstanceState(name=name)

        # Build command and environment
        from llama_orchestrator.health.ports import validate_port_for_instance

        port_valid, port_message = validate_port_for_instance(
            config.server.port,
            name,
            config.server.host,
        )
        if not port_valid:
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = port_message
            save_state(state)
            _sync_runtime_from_state(state, config=config, last_error=port_message)
            raise ProcessError(name, port_message)

        cmd = build_command(config)
        cmdline = " ".join(cmd)
        env = build_env(config)

        log_handler = get_instance_log_handler(
            name,
            max_bytes=config.logs.max_size_mb * 1024 * 1024,
            backup_count=config.logs.rotation,
        )

        # Update state to starting
        state.status = InstanceStatus.STARTING
        state.health = HealthStatus.UNKNOWN
        state.error_message = ""
        save_state(state)
        _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error="")

        stdout_file = None
        stderr_file = None
        try:
            if detach:
                detach_result = start_detached(
                    name,
                    cmd,
                    env=env,
                    port=config.server.port,
                    cwd=get_project_root(),
                    rotate_logs=True,
                )
                if not detach_result.success or detach_result.pid is None:
                    error_message = detach_result.error or "Detached start failed"
                    state.status = InstanceStatus.ERROR
                    state.health = HealthStatus.ERROR
                    state.error_message = error_message
                    save_state(state)
                    _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error=error_message)
                    raise ProcessError(name, error_message)

                state.pid = detach_result.pid
                state.start_time = time.time()
                state.status = InstanceStatus.RUNNING
                state.health = HealthStatus.LOADING
                save_state(state)
                _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error="")
                return state

            # Open log files
            stdout_file, stderr_file = log_handler.get_file_handles()

            # Write startup marker
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            stdout_file.write(f"\n{'='*60}\n")
            stdout_file.write(f"Starting instance at {timestamp}\n")
            stdout_file.write(f"Command: {cmdline}\n")
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

            # Release parent-side handles immediately after spawn
            stdout_file.close()
            stderr_file.close()
            stdout_file = None
            stderr_file = None

            # Update state
            started_at = time.time()
            state.pid = proc.pid
            state.start_time = started_at
            state.status = InstanceStatus.RUNNING
            state.health = HealthStatus.LOADING
            save_state(state)
            _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error="")
            log_event(
                event_type="started",
                message=f"Instance started (PID: {proc.pid}, port: {config.server.port})",
                instance_name=name,
                meta={"pid": proc.pid, "port": config.server.port},
            )

            # Brief wait to check if process started successfully
            time.sleep(0.5)

            if proc.poll() is not None:
                # Process exited immediately
                state.status = InstanceStatus.ERROR
                state.health = HealthStatus.ERROR
                state.error_message = f"Process exited with code {proc.returncode}"
                save_state(state)
                _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error=state.error_message)
                log_event(
                    event_type="start_failed",
                    message=state.error_message,
                    instance_name=name,
                    level="error",
                    meta={"exit_code": proc.returncode},
                )
                raise ProcessError(name, state.error_message)

            return state

        except Exception as e:
            # Update state on failure
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = str(e)
            save_state(state)
            _sync_runtime_from_state(state, config=config, cmdline=cmdline, last_error=state.error_message)
            log_event(
                event_type="start_failed",
                message=state.error_message,
                instance_name=name,
                level="error",
            )

            if not isinstance(e, ProcessError):
                raise ProcessError(name, f"Failed to start: {e}", e) from e
            raise
        finally:
            with suppress(Exception):
                if stdout_file is not None:
                    stdout_file.close()
            with suppress(Exception):
                if stderr_file is not None:
                    stderr_file.close()


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
    with instance_lock(name, operation="stop"):
        state = load_state(name)
        runtime = load_runtime(name)

        if state is None:
            if runtime is None:
                raise ProcessError(name, "Instance not found in state")
            state = _runtime_to_state(runtime)

        state = check_stale_state(state)

        if state.status == InstanceStatus.STOPPED:
            _sync_runtime_from_state(state, last_error="")
            return state

        if state.pid is None:
            state.status = InstanceStatus.STOPPED
            state.health = HealthStatus.UNKNOWN
            save_state(state)
            _sync_runtime_from_state(state, last_error="")
            return state

        # Update state to stopping
        state.status = InstanceStatus.STOPPING
        save_state(state)
        _sync_runtime_from_state(state)

        # Kill the process
        kill_process_tree(state.pid, timeout=0 if force else timeout)

        # Update state
        stopped_pid = state.pid
        state.pid = None
        state.status = InstanceStatus.STOPPED
        state.health = HealthStatus.UNKNOWN
        state.error_message = ""
        save_state(state)
        _sync_runtime_from_state(state, last_error="")
        log_event(
            event_type="stopped",
            message=f"Instance stopped (PID: {stopped_pid}, force: {force})",
            instance_name=name,
            meta={"pid": stopped_pid, "force": force},
        )

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
    if state is None:
        runtime = load_runtime(name)
        if runtime is not None:
            state = _runtime_to_state(runtime)

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
    _sync_runtime_from_state(state)
    log_event(
        event_type="restarted",
        message=f"Instance restarted (count: {restart_count})",
        instance_name=name,
        meta={"restart_count": restart_count},
    )
    
    return state


def get_instance_status(name: str) -> InstanceState:
    """
    Get current status of an instance.
    
    Returns a corrected state (checks for stale PIDs).
    """
    state = load_state(name)
    if state is None:
        runtime = load_runtime(name)
        if runtime is None:
            return InstanceState(name=name, status=InstanceStatus.STOPPED)
        state = _runtime_to_state(runtime)
    
    return check_stale_state(state)


def list_instances() -> dict[str, InstanceState]:
    """
    List all instances with their current status.
    
    Returns:
        Dictionary of instance name -> state
    """
    from llama_orchestrator.config import discover_instances
    
    states = load_all_states()
    runtime_states = load_all_runtime()

    for name, runtime in runtime_states.items():
        if name not in states:
            states[name] = _runtime_to_state(runtime)
    
    # Also include instances that have configs but no state yet
    for name, _ in discover_instances():
        if name not in states:
            states[name] = InstanceState(name=name, status=InstanceStatus.STOPPED)
    
    # Check for stale states
    for name, state in states.items():
        states[name] = check_stale_state(state)
    
    return states
