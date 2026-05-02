"""
Exit code standards for llama-orchestrator CLI.

Provides consistent exit codes across all CLI commands for
scripting and automation compatibility.

Exit Code Ranges:
    0:      Success
    1-9:    General errors
    10-19:  Configuration errors  
    20-29:  Instance errors
    30-39:  Process/Runtime errors
    40-49:  Network errors
    50-59:  Binary/Dependency errors
    60-69:  Daemon errors
    100+:   Reserved for future use
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


class ExitCode(IntEnum):
    """
    Standard exit codes for CLI commands.
    
    Follows common Unix conventions with project-specific extensions.
    """
    
    # Success (0)
    SUCCESS = 0
    
    # General errors (1-9)
    GENERAL_ERROR = 1
    USAGE_ERROR = 2
    KEYBOARD_INTERRUPT = 3
    TIMEOUT = 4
    PERMISSION_DENIED = 5
    
    # Configuration errors (10-19)
    CONFIG_NOT_FOUND = 10
    CONFIG_INVALID = 11
    CONFIG_PARSE_ERROR = 12
    INSTANCE_NOT_FOUND = 13
    INSTANCE_ALREADY_EXISTS = 14
    
    # Instance state errors (20-29)
    INSTANCE_NOT_RUNNING = 20
    INSTANCE_ALREADY_RUNNING = 21
    INSTANCE_UNHEALTHY = 22
    INSTANCE_STARTING = 23
    INSTANCE_STOPPING = 24
    INSTANCE_CRASHED = 25
    
    # Process/Runtime errors (30-39)
    PROCESS_START_FAILED = 30
    PROCESS_STOP_FAILED = 31
    PROCESS_NOT_FOUND = 32
    LOCK_ACQUIRE_FAILED = 33
    STATE_CORRUPTION = 34
    
    # Network errors (40-49)
    PORT_IN_USE = 40
    PORT_UNAVAILABLE = 41
    HEALTH_CHECK_FAILED = 42
    CONNECTION_REFUSED = 43
    CONNECTION_TIMEOUT = 44
    
    # Binary/Dependency errors (50-59)
    BINARY_NOT_FOUND = 50
    BINARY_INVALID = 51
    BINARY_DOWNLOAD_FAILED = 52
    BINARY_INSTALL_FAILED = 53
    MODEL_NOT_FOUND = 54
    MODEL_INVALID = 55
    
    # Daemon errors (60-69)
    DAEMON_NOT_RUNNING = 60
    DAEMON_ALREADY_RUNNING = 61
    DAEMON_START_FAILED = 62
    DAEMON_STOP_FAILED = 63
    DAEMON_UNREACHABLE = 64
    
    @classmethod
    def from_exception(cls, exc: Exception) -> "ExitCode":
        """
        Map an exception to an appropriate exit code.
        
        Args:
            exc: Exception to map
            
        Returns:
            Appropriate ExitCode for the exception
        """
        exc_type = type(exc).__name__
        
        # Map common exceptions to exit codes
        mapping = {
            "FileNotFoundError": cls.CONFIG_NOT_FOUND,
            "PermissionError": cls.PERMISSION_DENIED,
            "TimeoutError": cls.TIMEOUT,
            "ConnectionRefusedError": cls.CONNECTION_REFUSED,
            "ConnectionError": cls.CONNECTION_REFUSED,
            "ValidationError": cls.CONFIG_INVALID,
            "ProcessError": cls.PROCESS_START_FAILED,
            "LockError": cls.LOCK_ACQUIRE_FAILED,
            "KeyboardInterrupt": cls.KEYBOARD_INTERRUPT,
        }
        
        return mapping.get(exc_type, cls.GENERAL_ERROR)
    
    @property
    def description(self) -> str:
        """Get a human-readable description of the exit code."""
        descriptions = {
            ExitCode.SUCCESS: "Operation completed successfully",
            ExitCode.GENERAL_ERROR: "An unexpected error occurred",
            ExitCode.USAGE_ERROR: "Invalid command usage",
            ExitCode.KEYBOARD_INTERRUPT: "Operation interrupted by user",
            ExitCode.TIMEOUT: "Operation timed out",
            ExitCode.PERMISSION_DENIED: "Permission denied",
            ExitCode.CONFIG_NOT_FOUND: "Configuration file not found",
            ExitCode.CONFIG_INVALID: "Configuration is invalid",
            ExitCode.CONFIG_PARSE_ERROR: "Failed to parse configuration",
            ExitCode.INSTANCE_NOT_FOUND: "Instance not found",
            ExitCode.INSTANCE_ALREADY_EXISTS: "Instance already exists",
            ExitCode.INSTANCE_NOT_RUNNING: "Instance is not running",
            ExitCode.INSTANCE_ALREADY_RUNNING: "Instance is already running",
            ExitCode.INSTANCE_UNHEALTHY: "Instance is unhealthy",
            ExitCode.INSTANCE_STARTING: "Instance is still starting",
            ExitCode.INSTANCE_STOPPING: "Instance is still stopping",
            ExitCode.INSTANCE_CRASHED: "Instance crashed unexpectedly",
            ExitCode.PROCESS_START_FAILED: "Failed to start process",
            ExitCode.PROCESS_STOP_FAILED: "Failed to stop process",
            ExitCode.PROCESS_NOT_FOUND: "Process not found",
            ExitCode.LOCK_ACQUIRE_FAILED: "Failed to acquire lock",
            ExitCode.STATE_CORRUPTION: "State database is corrupted",
            ExitCode.PORT_IN_USE: "Port is already in use",
            ExitCode.PORT_UNAVAILABLE: "Port is not available",
            ExitCode.HEALTH_CHECK_FAILED: "Health check failed",
            ExitCode.CONNECTION_REFUSED: "Connection was refused",
            ExitCode.CONNECTION_TIMEOUT: "Connection timed out",
            ExitCode.BINARY_NOT_FOUND: "Binary executable not found",
            ExitCode.BINARY_INVALID: "Binary is invalid or corrupted",
            ExitCode.BINARY_DOWNLOAD_FAILED: "Failed to download binary",
            ExitCode.BINARY_INSTALL_FAILED: "Failed to install binary",
            ExitCode.MODEL_NOT_FOUND: "Model file not found",
            ExitCode.MODEL_INVALID: "Model file is invalid",
            ExitCode.DAEMON_NOT_RUNNING: "Daemon is not running",
            ExitCode.DAEMON_ALREADY_RUNNING: "Daemon is already running",
            ExitCode.DAEMON_START_FAILED: "Failed to start daemon",
            ExitCode.DAEMON_STOP_FAILED: "Failed to stop daemon",
            ExitCode.DAEMON_UNREACHABLE: "Cannot communicate with daemon",
        }
        return descriptions.get(self, f"Exit code {self.value}")
    
    @property
    def is_success(self) -> bool:
        """Check if this is a success code."""
        return self == ExitCode.SUCCESS
    
    @property
    def is_error(self) -> bool:
        """Check if this is an error code."""
        return self != ExitCode.SUCCESS
    
    @property
    def category(self) -> str:
        """Get the error category."""
        if self == ExitCode.SUCCESS:
            return "success"
        elif 1 <= self <= 9:
            return "general"
        elif 10 <= self <= 19:
            return "config"
        elif 20 <= self <= 29:
            return "instance"
        elif 30 <= self <= 39:
            return "process"
        elif 40 <= self <= 49:
            return "network"
        elif 50 <= self <= 59:
            return "binary"
        elif 60 <= self <= 69:
            return "daemon"
        else:
            return "unknown"


def exit_with_code(
    code: ExitCode,
    message: str | None = None,
    console: "Console | None" = None,
) -> None:
    """
    Exit with a standard exit code and optional message.
    
    Args:
        code: Exit code to use
        message: Optional message to display
        console: Rich console for output (uses default if None)
    """
    import sys
    
    if message and console:
        if code.is_error:
            console.print(f"[red]Error:[/red] {message}")
        else:
            console.print(f"[green]✓[/green] {message}")
    elif message:
        if code.is_error:
            print(f"Error: {message}", file=sys.stderr)
        else:
            print(message)
    
    sys.exit(code.value)


def handle_cli_error(
    exc: Exception,
    console: "Console | None" = None,
    verbose: bool = False,
) -> ExitCode:
    """
    Handle a CLI exception and return appropriate exit code.
    
    Args:
        exc: Exception to handle
        console: Rich console for output
        verbose: Show stack trace if True
        
    Returns:
        Appropriate exit code
    """
    import traceback
    
    code = ExitCode.from_exception(exc)
    
    if console:
        console.print(f"[red]Error:[/red] {exc}")
        if verbose:
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
    
    return code


# Convenience type alias
EXIT_SUCCESS = ExitCode.SUCCESS
EXIT_ERROR = ExitCode.GENERAL_ERROR
