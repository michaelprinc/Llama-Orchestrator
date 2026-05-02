"""
Port management module for Llama Orchestrator V2.

Provides utilities for checking port availability, detecting collisions,
and finding free ports for instance allocation.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Iterator

import psutil

from llama_orchestrator.engine.state import load_all_runtime, log_event

logger = logging.getLogger(__name__)


@dataclass
class PortInfo:
    """Information about a port's status."""
    
    port: int
    is_available: bool
    owner_pid: int | None = None
    owner_name: str | None = None
    owner_cmdline: str | None = None
    instance_name: str | None = None
    
    def is_owned_by_us(self) -> bool:
        """Check if port is owned by a known instance."""
        return self.instance_name is not None


def check_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """
    Check if a port is available for binding.
    
    Args:
        port: Port number to check
        host: Host to check (default localhost)
        
    Returns:
        True if port is available
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def get_port_owner(port: int) -> dict | None:
    """
    Get information about the process using a port.
    
    Args:
        port: Port number to check
        
    Returns:
        Dictionary with process info or None if port is free
    """
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    proc = psutil.Process(conn.pid)
                    try:
                        cmdline = " ".join(proc.cmdline())
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        cmdline = proc.name()
                    
                    return {
                        "pid": conn.pid,
                        "name": proc.name(),
                        "cmdline": cmdline,
                        "status": conn.status,
                    }
                except psutil.NoSuchProcess:
                    return {"pid": conn.pid, "name": None, "cmdline": None}
    except psutil.AccessDenied:
        logger.warning("Access denied when checking network connections")
    
    return None


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
    
    # Port is in use, find owner
    owner = get_port_owner(port)
    
    if owner is None:
        return PortInfo(port=port, is_available=False)
    
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
        except socket.error:
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
