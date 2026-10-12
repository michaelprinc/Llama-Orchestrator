"""
Port management module for Llama Orchestrator V2.

Provides utilities for checking port availability, detecting collisions,
and finding free ports for instance allocation.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psutil

from llama_orchestrator.engine.state import load_all_runtime, log_event

logger = logging.getLogger(__name__)

# R13: owner lookups can degrade (psutil AccessDenied). Warn once per process
# instead of spamming on every poll; the degraded flag carries the signal.
_degraded_warned = False


def _note_owner_lookup_degraded(reason: str) -> None:
    """Record that owner lookup degraded, emitting exactly one warning."""
    global _degraded_warned
    if not _degraded_warned:
        logger.warning(
            f"Port owner lookup degraded ({reason}); absence of an owner is "
            "now uncertain, not proof the port is free"
        )
        _degraded_warned = True


@dataclass
class PortInfo:
    """Information about a port's status."""

    port: int
    is_available: bool
    owner_pid: int | None = None
    owner_name: str | None = None
    owner_cmdline: str | None = None
    instance_name: str | None = None
    # R13: True when ownership could not be determined (e.g. psutil
    # AccessDenied). Callers must treat "no owner" as uncertain, not as proof
    # the port is free.
    degraded: bool = False

    def is_owned_by_us(self) -> bool:
        """Check if port is owned by a known instance."""
        return self.instance_name is not None


def check_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """
    Check if a port is available for binding (SO_REUSEADDR probe).

    Known limitations (R13):
      * TOCTOU window: even when this returns True, the port can be taken by
        another process before a spawned server binds it. The start path must
        treat the child's own bind as authoritative — see
        ``acquire_port_for_spawn`` and the post-spawn re-probe in
        ``engine.process.start_instance``.
      * Linux blind spot: a socket that is bound but not yet listening is
        invisible to /proc/net/tcp (empirically verified), so this probe can
        report True while such a socket exists. That state is harmless for
        spawn: a second SO_REUSEADDR bind+listen on the same addr:port
        succeeds and reliably receives all connections, so llama-server
        starts correctly anyway. It cannot be detected from outside the
        owning process without ptrace.

    Args:
        port: Port number to check
        host: Host to check (default localhost)

    Returns:
        True if a SO_REUSEADDR bind succeeds
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def get_port_owner(port: int) -> tuple[dict[str, Any] | None, bool]:
    """
    Get information about the process using a port.

    Args:
        port: Port number to check

    Returns:
        Tuple (owner, degraded). owner is a dict with pid/name/cmdline/status,
        or None if no LISTEN connection was found OR ownership could not be
        determined. degraded is True when the lookup hit AccessDenied or an
        unattributable connection — callers must treat that absence as
        uncertain rather than proof the port is free. A one-time warning is
        logged per process on degradation.
    """
    try:
        for conn in psutil.net_connections(kind='inet'):
            # types-psutil declares laddr as a heterogeneous tuple union, but at
            # runtime it is an addr(ip=..., port=...) namedtuple exposing .port.
            if conn.laddr.port == port and conn.status == 'LISTEN':  # type: ignore[union-attr]
                if conn.pid is None:
                    _note_owner_lookup_degraded("LISTEN connection has no owner pid")
                    return (
                        {"pid": None, "name": None, "cmdline": None, "status": conn.status},
                        True,
                    )
                try:
                    proc = psutil.Process(conn.pid)
                    try:
                        cmdline = " ".join(proc.cmdline())
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        cmdline = proc.name()

                    return (
                        {
                            "pid": conn.pid,
                            "name": proc.name(),
                            "cmdline": cmdline,
                            "status": conn.status,
                        },
                        False,
                    )
                except psutil.NoSuchProcess:
                    return ({"pid": conn.pid, "name": None, "cmdline": None}, False)
    except psutil.AccessDenied:
        _note_owner_lookup_degraded("psutil.net_connections access denied")
        return None, True

    return None, False


def get_port_info(port: int, host: str = "127.0.0.1") -> PortInfo:
    """
    Get detailed information about a port's status.

    Args:
        port: Port number to check
        host: Host to check

    Returns:
        PortInfo with details about the port
    """
    is_available = check_port_available(port, host)

    if is_available:
        return PortInfo(port=port, is_available=True)

    # Port is in use, find owner (R13: lookup may degrade — flag propagates)
    owner, degraded = get_port_owner(port)

    if owner is None:
        return PortInfo(port=port, is_available=False, degraded=degraded)

    # Check if this is one of our instances
    instance_name = None
    runtime_states = load_all_runtime()

    for name, runtime in runtime_states.items():
        if runtime.port == port and runtime.pid == owner.get("pid"):
            instance_name = name
            break

    return PortInfo(
        port=port,
        is_available=False,
        owner_pid=owner.get("pid"),
        owner_name=owner.get("name"),
        owner_cmdline=owner.get("cmdline"),
        instance_name=instance_name,
        degraded=degraded,
    )


def find_free_port(
    start_port: int = 8080,
    end_port: int = 9000,
    host: str = "127.0.0.1",
    exclude_ports: set[int] | None = None,
) -> int | None:
    """
    Find a free port in the specified range.

    Args:
        start_port: Start of port range
        end_port: End of port range (inclusive)
        host: Host to check
        exclude_ports: Ports to skip

    Returns:
        First available port or None if none found
    """
    exclude = exclude_ports or set()

    for port in range(start_port, end_port + 1):
        if port in exclude:
            continue

        if check_port_available(port, host):
            logger.debug(f"Found free port: {port}")
            return port

    logger.warning(f"No free port found in range {start_port}-{end_port}")
    return None


def iter_free_ports(
    start_port: int = 8080,
    end_port: int = 9000,
    host: str = "127.0.0.1",
    exclude_ports: set[int] | None = None,
) -> Iterator[int]:
    """
    Iterate over free ports in the specified range.

    Args:
        start_port: Start of port range
        end_port: End of port range (inclusive)
        host: Host to check
        exclude_ports: Ports to skip

    Yields:
        Available port numbers
    """
    exclude = exclude_ports or set()

    for port in range(start_port, end_port + 1):
        if port in exclude:
            continue

        if check_port_available(port, host):
            yield port


def get_used_ports_by_instances() -> dict[str, int]:
    """
    Get mapping of instance names to their ports.

    Returns:
        Dictionary mapping instance name -> port number
    """
    runtime_states = load_all_runtime()
    return {name: rt.port for name, rt in runtime_states.items() if rt.port}


def validate_port_for_instance(
    port: int,
    instance_name: str,
    host: str = "127.0.0.1",
) -> tuple[bool, str]:
    """
    Validate that a port can be used for an instance.

    Args:
        port: Port to validate
        instance_name: Name of the instance that wants to use the port
        host: Host to check

    Returns:
        Tuple of (is_valid, message)
    """
    port_info = get_port_info(port, host)

    if port_info.is_available:
        return True, f"Port {port} is available"

    # Check if it's already owned by this instance (restarting)
    if port_info.instance_name == instance_name:
        return True, f"Port {port} is already owned by this instance"

    # Port is in use by something else
    if port_info.instance_name:
        msg = f"Port {port} is in use by instance '{port_info.instance_name}'"
    elif port_info.owner_pid:
        msg = f"Port {port} is in use by PID {port_info.owner_pid}"
        if port_info.owner_name:
            msg += f" ({port_info.owner_name})"
    else:
        msg = f"Port {port} is in use by unknown process"
        if port_info.degraded:
            # R13: the owner lookup degraded (e.g. AccessDenied); say so
            # instead of implying a confident "unknown process".
            msg += " (owner lookup degraded — see warning log)"

    log_event(
        event_type="port_collision",
        message=msg,
        instance_name=instance_name,
        level="warning",
        meta={
            "port": port,
            "owner_pid": port_info.owner_pid,
            "owner_instance": port_info.instance_name,
        },
    )

    return False, msg


def suggest_port_for_instance(
    instance_name: str,
    preferred_port: int | None = None,
    port_range: tuple[int, int] = (8080, 9000),
    host: str = "127.0.0.1",
) -> int | None:
    """
    Suggest an available port for a new instance.

    If preferred_port is available, returns that. Otherwise finds a free port.

    Args:
        instance_name: Name of the instance
        preferred_port: Preferred port if available
        port_range: Range to search for free ports
        host: Host to check

    Returns:
        Available port number or None if none found
    """
    # Check preferred port first
    if preferred_port:
        is_valid, _ = validate_port_for_instance(preferred_port, instance_name, host)
        if is_valid:
            return preferred_port

    # Get ports already used by our instances
    used_ports = set(get_used_ports_by_instances().values())

    # Find a free port
    return find_free_port(
        start_port=port_range[0],
        end_port=port_range[1],
        host=host,
        exclude_ports=used_ports,
    )


def wait_for_port(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 30.0,
    check_interval: float = 0.5,
) -> bool:
    """
    Wait for a port to become available (listening).

    Args:
        port: Port to wait for
        host: Host to check
        timeout: Maximum time to wait
        check_interval: Time between checks

    Returns:
        True if port is listening, False if timeout
    """
    import time

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                if result == 0:
                    return True
        except OSError:
            pass

        time.sleep(check_interval)

    return False


def wait_for_port_release(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 10.0,
    check_interval: float = 0.5,
) -> bool:
    """
    Wait for a port to be released (available for binding).

    Args:
        port: Port to wait for
        host: Host to check
        timeout: Maximum time to wait
        check_interval: Time between checks

    Returns:
        True if port is available, False if timeout
    """
    import time

    start_time = time.time()

    while time.time() - start_time < timeout:
        if check_port_available(port, host):
            return True

        time.sleep(check_interval)

    return False


def acquire_port_for_spawn(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 10.0,
    check_interval: float = 0.1,
) -> bool:
    """
    Wait until the port is actually bindable, immediately before spawning.

    R13: the pre-check in ``validate_port_for_instance`` has a TOCTOU window,
    and on a restart the instance's own previous owner may still hold the
    listening socket while it shuts down (validation passes via "already
    owned by this instance", but the child would race the dying process).
    This bounded SO_REUSEADDR-aware retry loop closes that window for
    just-freed ports: only spawn when the port is provably bindable.

    Args:
        port: Port the spawned server will bind
        host: Host to check
        timeout: Maximum time to wait for the port to become bindable
        check_interval: Seconds between probes

    Returns:
        True once the port is bindable; False if it is still held after
        timeout (caller must treat that as a collision and fail closed).
    """
    deadline = time.monotonic() + timeout

    while True:
        if check_port_available(port, host):
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                f"Port {port} still not bindable after {timeout:.1f}s "
                "(previous owner may still be shutting down)"
            )
            return False
        time.sleep(check_interval)
