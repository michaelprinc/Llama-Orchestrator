"""
Daemon module for llama-orchestrator.

Provides background service functionality for health monitoring
and auto-restart of instances.
"""

from llama_orchestrator.daemon.service import (
    DaemonService,
    get_daemon_status,
    is_daemon_running,
    start_daemon,
    stop_daemon,
)

__all__ = [
    "DaemonService",
    "get_daemon_status",
    "is_daemon_running",
    "start_daemon",
    "stop_daemon",
]
