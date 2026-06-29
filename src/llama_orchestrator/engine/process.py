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
    delete_runtime,
    delete_state,
    get_health_history,
    get_recent_events,
    list_divergent_instances,
    load_all_runtime,
    load_all_states,
    load_runtime,
    load_state,
    log_event,
    record_health_check,
    reconcile_state,
    save_runtime,
    save_state,
    save_state_atomic,
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


def _build_runtime_from_state(
    state: InstanceState,
    config: "InstanceConfig | None" = None,
    cmdline: str = "",
    last_error: str | None = None,
) -> RuntimeState:
    """Build a RuntimeState from an InstanceState without persisting."""
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
    return runtime


def _persist_health_update(
    state: InstanceState,
    config: "InstanceConfig",
    cmdline: str,
    health: HealthStatus,
    response_time_ms: float | None = None,
    error_message: str = "",
    checked_at: float | None = None,
) -> None:
    """Persist health consistently to legacy state, runtime, and history."""
    checked_at = time.time() if checked_at is None else checked_at
    state.health = health
    state.last_health_check = checked_at

    runtime = _build_runtime_from_state(
        state,
        config=config,
        cmdline=cmdline,
        last_error="" if health == HealthStatus.HEALTHY else (error_message or None),
    )
    runtime.last_seen_at = checked_at
    runtime.started_at = runtime.started_at or state.start_time
    if health == HealthStatus.HEALTHY:
        runtime.last_health_ok_at = checked_at
    elif error_message:
        runtime.last_error = error_message

    save_state_atomic(state, runtime)
    record_health_check(
        state.name,
        health,
        response_time_ms=response_time_ms,
        error_message=error_message,
    )


def _wait_for_instance_ready(
    proc: subprocess.Popen,
    state: InstanceState,
    config: "InstanceConfig",
    cmdline: str,
) -> InstanceState:
    """Wait for a started instance to become healthy within the startup budget."""
    from llama_orchestrator.health.checker import check_instance_health

    total_budget = float(max(config.healthcheck.start_period, config.healthcheck.timeout))
    deadline = time.monotonic() + total_budget

    while True:
        if proc.poll() is not None:
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = f"Process exited with code {proc.returncode}"
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=state.error_message
            )
            save_state_atomic(state, runtime)
            log_event(
                event_type="start_failed",
                message=state.error_message,
                instance_name=state.name,
                level="error",
                meta={"exit_code": proc.returncode},
            )
            raise ProcessError(state.name, state.error_message)

        remaining_budget = max(0.0, deadline - time.monotonic())
        if remaining_budget <= 0:
            break

        result = check_instance_health(
            state.name,
            timeout=min(float(config.healthcheck.timeout), remaining_budget),
        )

        if proc.poll() is not None:
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = f"Process exited with code {proc.returncode}"
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=state.error_message
            )
            save_state_atomic(state, runtime)
            log_event(
                event_type="start_failed",
                message=state.error_message,
                instance_name=state.name,
                level="error",
                meta={"exit_code": proc.returncode},
            )
            raise ProcessError(state.name, state.error_message)

        persisted_health = HealthStatus.HEALTHY if result.is_healthy else HealthStatus.LOADING
        _persist_health_update(
            state,
            config,
            cmdline,
            health=persisted_health,
            response_time_ms=result.response_time_ms,
            error_message="" if result.is_healthy else (result.error_message or ""),
        )

        if result.is_healthy:
            return state

        remaining_budget = max(0.0, deadline - time.monotonic())
        if remaining_budget <= 0:
            break

        time.sleep(min(float(config.healthcheck.retry_delay), remaining_budget))

    state.status = InstanceStatus.RUNNING
    state.health = HealthStatus.LOADING
    runtime = _build_runtime_from_state(state, config=config, cmdline=cmdline)
    save_state_atomic(state, runtime)
    return state


def _wait_for_detached_instance_ready(
    state: InstanceState,
    config: "InstanceConfig",
    cmdline: str,
) -> InstanceState:
    """Wait for a detached instance to become healthy within the startup budget."""
    from llama_orchestrator.health.checker import check_instance_health

    if state.pid is None:
        raise ProcessError(state.name, "Detached process did not return a PID")

    total_budget = float(max(config.healthcheck.start_period, config.healthcheck.timeout))
    deadline = time.monotonic() + total_budget

    while True:
        if not is_process_running(state.pid):
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = "Detached process exited before readiness completed"
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=state.error_message
            )
            save_state_atomic(state, runtime)
            log_event(
                event_type="start_failed",
                message=state.error_message,
                instance_name=state.name,
                level="error",
                meta={"pid": state.pid},
            )
            raise ProcessError(state.name, state.error_message)

        remaining_budget = max(0.0, deadline - time.monotonic())
        if remaining_budget <= 0:
            break

        result = check_instance_health(
            state.name,
            timeout=min(float(config.healthcheck.timeout), remaining_budget),
        )

        if not is_process_running(state.pid):
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = "Detached process exited before readiness completed"
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=state.error_message
            )
            save_state_atomic(state, runtime)
            log_event(
                event_type="start_failed",
                message=state.error_message,
                instance_name=state.name,
                level="error",
                meta={"pid": state.pid},
            )
            raise ProcessError(state.name, state.error_message)

        persisted_health = HealthStatus.HEALTHY if result.is_healthy else HealthStatus.LOADING
        _persist_health_update(
            state,
            config,
            cmdline,
            health=persisted_health,
            response_time_ms=result.response_time_ms,
            error_message="" if result.is_healthy else (result.error_message or ""),
        )

        if result.is_healthy:
            return state

        remaining_budget = max(0.0, deadline - time.monotonic())
        if remaining_budget <= 0:
            break

        time.sleep(min(float(config.healthcheck.retry_delay), remaining_budget))

    state.status = InstanceStatus.RUNNING
    state.health = HealthStatus.LOADING
    runtime = _build_runtime_from_state(state, config=config, cmdline=cmdline)
    save_state_atomic(state, runtime)
    return state


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


def _send_windows_ctrl_c(pid: int) -> bool:
    """
    Send Ctrl+C to a Windows process group.

    Args:
        pid: Process ID to send Ctrl+C to

    Returns:
        True if Ctrl+C was sent successfully
    """
    import sys

    if sys.platform != "win32":
        return False

    try:
        import ctypes
        import ctypes.wintypes

        # CTRL_C_EVENT cannot be scoped to a process group on Windows. A
        # CTRL_BREAK_EVENT can, and start_instance creates a new process group.
        # Using Ctrl+C here could also interrupt the orchestrator itself.
        kernel32 = ctypes.windll.kernel32
        ctrl_break_event = 1
        result = kernel32.GenerateConsoleCtrlEvent(ctrl_break_event, pid)

        if result == 0:
            # Failed to send event - might not be a console process
            return False
        return True
    except (ImportError, AttributeError, OSError):
        # ctypes or kernel32 not available
        return False


def _try_http_shutdown(port: int, timeout: float = 2.0) -> bool:
    """
    Try to send HTTP shutdown request to llama-server.

    Args:
        port: Port number to send shutdown request to
        timeout: Timeout for the HTTP request

    Returns:
        True if shutdown request was sent successfully
    """
    import httpx

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"http://127.0.0.1:{port}/shutdown")
            return response.status_code in (200, 404)  # 404 means no endpoint (still ok)
    except (httpx.RequestError, httpx.ConnectError):
        return False


def graceful_shutdown(
    pid: int,
    port: int | None = None,
    timeout: float = 10.0,
    force: bool = False,
) -> dict:
    """
    Gracefully shut down a process with Windows-first approach.

    Implements a multi-stage shutdown sequence:
    1. On Windows: Try Ctrl+C to process group
    2. Try HTTP /shutdown endpoint if port is available
    3. Send SIGTERM/terminate() to process tree
    4. Wait for graceful shutdown
    5. Force kill if needed

    Args:
        pid: Process ID to shut down
        port: Optional port for HTTP shutdown
        timeout: Total timeout for graceful shutdown
        force: If True, skip graceful steps and force kill immediately

    Returns:
        Dictionary with shutdown details:
        - method: How the process was stopped
        - duration: Time taken to stop
        - children_killed: Number of children killed
    """
    import sys
    import time

    start_time = time.monotonic()
    result = {
        "method": "unknown",
        "duration": 0.0,
        "children_killed": 0,
    }

    if force:
        # Force kill immediately
        _force_kill_process_tree(pid)
        result["method"] = "force_kill"
        result["duration"] = time.monotonic() - start_time
        return result

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        result["method"] = "not_found"
        result["duration"] = time.monotonic() - start_time
        return result

    # Get all children first
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    result["children_killed"] = len(children)

    # Stage 1: Windows-first - Try Ctrl+C
    if sys.platform == "win32":
        if _send_windows_ctrl_c(pid):
            # Wait a bit to see if Ctrl+C worked
            gone, alive = psutil.wait_procs([parent] + children, timeout=3.0)
            if not alive:
                result["method"] = "ctrl_c"
                result["duration"] = time.monotonic() - start_time
                return result

    # Stage 2: Try HTTP /shutdown endpoint
    if port is not None:
        if _try_http_shutdown(port, timeout=2.0):
            # Wait to see if HTTP shutdown worked
            gone, alive = psutil.wait_procs([parent] + children, timeout=3.0)
            if not alive:
                result["method"] = "http_shutdown"
                result["duration"] = time.monotonic() - start_time
                return result

    # Stage 3: Send SIGTERM/terminate()
    try:
        parent.terminate()
    except psutil.NoSuchProcess:
        pass

    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass

    # Stage 4: Wait for graceful shutdown
    gone, alive = psutil.wait_procs([parent] + children, timeout=timeout)

    # Stage 5: Force kill remaining
    if alive:
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        result["method"] = "terminate_then_kill"
    else:
        result["method"] = "terminate"

    result["duration"] = time.monotonic() - start_time
    return result


def _force_kill_process_tree(pid: int) -> bool:
    """
    Force kill a process and all its children immediately.

    Args:
        pid: Process ID to kill

    Returns:
        True if process was killed
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

    # Kill parent first
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass

    # Kill all children
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass

    return True


def kill_process_tree(pid: int, timeout: float = 10.0, port: int | None = None) -> bool:
    """
    Kill a process and all its children.

    Uses graceful_shutdown internally for Windows-first graceful shutdown.

    Args:
        pid: Process ID to kill
        timeout: Timeout for graceful shutdown before force kill
        port: Optional port for HTTP shutdown attempt

    Returns:
        True if process was killed, False if not found
    """
    result = graceful_shutdown(pid, port=port, timeout=timeout, force=False)
    return result["method"] != "not_found"


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
            runtime = _build_runtime_from_state(state, last_error=state.error_message)
            save_state_atomic(state, runtime)

    return state


def start_instance(
    name: str,
    wait_for_ready: bool = True,
    detach: bool = False,
    config_override: "InstanceConfig | None" = None,
) -> InstanceState:
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
        if config_override is not None:
            config = config_override
        else:
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
            runtime = _build_runtime_from_state(state, config=config, last_error=port_message)
            save_state_atomic(state, runtime)
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
        runtime = _build_runtime_from_state(state, config=config, cmdline=cmdline, last_error="")
        save_state_atomic(state, runtime)

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
                    runtime = _build_runtime_from_state(
                        state, config=config, cmdline=cmdline, last_error=error_message
                    )
                    save_state_atomic(state, runtime)
                    raise ProcessError(name, error_message)

                state.pid = detach_result.pid
                state.start_time = time.time()
                state.status = InstanceStatus.RUNNING
                state.health = HealthStatus.LOADING
                runtime = _build_runtime_from_state(
                    state, config=config, cmdline=cmdline, last_error=""
                )
                save_state_atomic(state, runtime)
                log_event(
                    event_type="started",
                    message=f"Instance started (PID: {detach_result.pid}, port: {config.server.port})",
                    instance_name=name,
                    meta={"pid": detach_result.pid, "port": config.server.port},
                )
                if wait_for_ready:
                    return _wait_for_detached_instance_ready(state, config, cmdline)
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
                **({
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                } if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else {}),
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
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=""
            )
            save_state_atomic(state, runtime)
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
                runtime = _build_runtime_from_state(
                    state, config=config, cmdline=cmdline, last_error=state.error_message
                )
                save_state_atomic(state, runtime)
                log_event(
                    event_type="start_failed",
                    message=state.error_message,
                    instance_name=name,
                    level="error",
                    meta={"exit_code": proc.returncode},
                )
                raise ProcessError(name, state.error_message)

            if wait_for_ready:
                return _wait_for_instance_ready(proc, state, config, cmdline)

            return state

        except Exception as e:
            # Update state on failure
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = str(e)
            runtime = _build_runtime_from_state(
                state, config=config, cmdline=cmdline, last_error=state.error_message
            )
            save_state_atomic(state, runtime)
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
    Stop a llama-server instance with Windows-first graceful shutdown.

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
            save_state_atomic(state)
            return state

        if state.pid is None:
            state.status = InstanceStatus.STOPPED
            state.health = HealthStatus.UNKNOWN
            save_state_atomic(state)
            return state

        # Update state to stopping
        state.status = InstanceStatus.STOPPING
        save_state_atomic(state)

        # Get port for HTTP shutdown attempt
        port = None
        if runtime is not None:
            port = runtime.port

        # Kill the process with graceful shutdown
        kill_process_tree(state.pid, timeout=0 if force else timeout, port=port)

        # Update state
        stopped_pid = state.pid
        state.pid = None
        state.status = InstanceStatus.STOPPED
        state.health = HealthStatus.UNKNOWN
        state.error_message = ""
        save_state_atomic(state)
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


def restart_instance(
    name: str,
    force: bool = False,
    wait_for_ready: bool = True,
    config_override: "InstanceConfig | None" = None,
) -> InstanceState:
    """
    Restart a llama-server instance.

    Args:
        name: Instance name to restart
        force: Force kill without graceful shutdown
        wait_for_ready: Wait for readiness after restart completes

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
    if config_override is None:
        state = start_instance(name, wait_for_ready=wait_for_ready)
    else:
        state = start_instance(name, wait_for_ready=wait_for_ready, config_override=config_override)
    state.restart_count = restart_count
    save_state_atomic(state)
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
