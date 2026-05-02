"""
Logging configuration for Llama Orchestrator V2.

Provides configurable logging with rotation support for both
the orchestrator itself and managed instances.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from llama_orchestrator.config import get_logs_dir

logger = logging.getLogger(__name__)


# Default configuration
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 3
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class LogConfig:
    """Configuration for instance logging."""
    
    max_bytes: int = DEFAULT_MAX_BYTES
    backup_count: int = DEFAULT_BACKUP_COUNT
    log_format: str = DEFAULT_LOG_FORMAT
    date_format: str = DEFAULT_DATE_FORMAT
    encoding: str = "utf-8"


class InstanceLogHandler:
    """
    Manages rotating log handlers for an instance.
    
    Provides separate handlers for stdout and stderr with
    automatic rotation based on file size.
    """
    
    def __init__(
        self,
        name: str,
        log_dir: Path | None = None,
        config: LogConfig | None = None,
    ):
        """
        Initialize instance log handler.
        
        Args:
            name: Instance name
            log_dir: Log directory (default: ~/.llama-orchestrator/logs/<name>)
            config: Log configuration
        """
        self.name = name
        self.config = config or LogConfig()
        
        if log_dir is None:
            log_dir = get_logs_dir() / name
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self._stdout_handler: RotatingFileHandler | None = None
        self._stderr_handler: RotatingFileHandler | None = None
        self._stdout_path: Path | None = None
        self._stderr_path: Path | None = None
    
    @property
    def stdout_path(self) -> Path:
        """Path to stdout log file."""
        if self._stdout_path is None:
            self._stdout_path = self.log_dir / "stdout.log"
        return self._stdout_path
    
    @property
    def stderr_path(self) -> Path:
        """Path to stderr log file."""
        if self._stderr_path is None:
            self._stderr_path = self.log_dir / "stderr.log"
        return self._stderr_path
    
    def get_stdout_handler(self) -> RotatingFileHandler:
        """Get or create rotating handler for stdout."""
        if self._stdout_handler is None:
            self._stdout_handler = RotatingFileHandler(
                self.stdout_path,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding=self.config.encoding,
            )
            # No formatter for raw process output
        return self._stdout_handler
    
    def get_stderr_handler(self) -> RotatingFileHandler:
        """Get or create rotating handler for stderr."""
        if self._stderr_handler is None:
            self._stderr_handler = RotatingFileHandler(
                self.stderr_path,
                maxBytes=self.config.max_bytes,
                backupCount=self.config.backup_count,
                encoding=self.config.encoding,
            )
        return self._stderr_handler
    
    def get_file_handles(self) -> tuple[TextIO, TextIO]:
        """
        Get file handles for subprocess stdout/stderr.
        
        Returns:
            Tuple of (stdout_file, stderr_file) open for appending
        """
        # Ensure handlers are created (creates files if needed)
        self.get_stdout_handler()
        self.get_stderr_handler()
        
        # Open files for subprocess
        stdout_file = open(
            self.stdout_path, "a",
            encoding=self.config.encoding,
            buffering=1,  # Line buffering
        )
        stderr_file = open(
            self.stderr_path, "a",
            encoding=self.config.encoding,
            buffering=1,
        )
        
        return stdout_file, stderr_file
    
    def rotate_if_needed(self) -> None:
        """Force rotation check on handlers."""
        if self._stdout_handler:
            self._stdout_handler.doRollover()
        if self._stderr_handler:
            self._stderr_handler.doRollover()
    
    def close(self) -> None:
        """Close all handlers."""
        if self._stdout_handler:
            self._stdout_handler.close()
            self._stdout_handler = None
        if self._stderr_handler:
            self._stderr_handler.close()
            self._stderr_handler = None
    
    def get_log_files(self) -> dict[str, list[Path]]:
        """
        Get all log files for this instance.
        
        Returns:
            Dictionary with 'stdout' and 'stderr' keys containing lists of log paths
        """
        result = {"stdout": [], "stderr": []}
        
        # Current logs
        if self.stdout_path.exists():
            result["stdout"].append(self.stdout_path)
        if self.stderr_path.exists():
            result["stderr"].append(self.stderr_path)
        
        # Rotated logs (stdout.log.1, stdout.log.2, etc.)
        for i in range(1, self.config.backup_count + 1):
            stdout_backup = self.log_dir / f"stdout.log.{i}"
            stderr_backup = self.log_dir / f"stderr.log.{i}"
            
            if stdout_backup.exists():
                result["stdout"].append(stdout_backup)
            if stderr_backup.exists():
                result["stderr"].append(stderr_backup)
        
        return result


def setup_orchestrator_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
    console: bool = True,
) -> None:
    """
    Configure logging for the orchestrator itself.
    
    Args:
        level: Log level
        log_file: Optional file to log to
        console: Whether to also log to console
    """
    root_logger = logging.getLogger("llama_orchestrator")
    root_logger.setLevel(level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    formatter = logging.Formatter(
        DEFAULT_LOG_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
    )
    
    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)
    
    # File handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=DEFAULT_MAX_BYTES,
            backupCount=DEFAULT_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)


def get_instance_log_handler(
    name: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> InstanceLogHandler:
    """
    Get a log handler for an instance.
    
    Args:
        name: Instance name
        max_bytes: Maximum log file size before rotation
        backup_count: Number of backup files to keep
        
    Returns:
        InstanceLogHandler configured for the instance
    """
    config = LogConfig(
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    return InstanceLogHandler(name, config=config)


def cleanup_old_logs(name: str, keep_rotated: int = 3) -> int:
    """
    Clean up old rotated logs beyond the keep limit.
    
    Args:
        name: Instance name
        keep_rotated: Number of rotated logs to keep
        
    Returns:
        Number of files removed
    """
    log_dir = get_logs_dir() / name
    
    if not log_dir.exists():
        return 0
    
    removed = 0
    
    for log_type in ["stdout", "stderr"]:
        # Find rotated logs (e.g., stdout.log.1, stdout.log.2, ...)
        for i in range(keep_rotated + 1, 100):  # Check up to .99
            rotated = log_dir / f"{log_type}.log.{i}"
            if rotated.exists():
                try:
                    rotated.unlink()
                    removed += 1
                    logger.debug(f"Removed old log: {rotated}")
                except OSError as e:
                    logger.warning(f"Failed to remove {rotated}: {e}")
            else:
                break  # No more rotated logs
    
    return removed
