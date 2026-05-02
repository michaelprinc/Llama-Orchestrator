"""
Instance locking module for Llama Orchestrator V2.

Provides cross-process locking to prevent concurrent operations on the same
instance (e.g., starting an instance that is already starting).
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


class LockError(Exception):
    """Exception raised when lock cannot be acquired."""
    pass


class LockTimeoutError(LockError):
    """Exception raised when lock acquisition times out."""
    pass


class InstanceLockManager:
    """
    Manages file-based locks for instance operations.
    
    Uses simple file-based locking with PID files to prevent race conditions
    when multiple processes try to operate on the same instance.
    """
    
    def __init__(self, lock_dir: Path | None = None):
        """
        Initialize the lock manager.
        
        Args:
            lock_dir: Directory for lock files (default: ~/.llama-orchestrator/locks)
        """
        if lock_dir is None:
            lock_dir = Path.home() / ".llama-orchestrator" / "locks"
        
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._held_locks: dict[str, Path] = {}
    
    def _get_lock_path(self, name: str) -> Path:
        """Get path to lock file for an instance."""
        # Sanitize name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self.lock_dir / f"{safe_name}.lock"
    
    def _read_lock_info(self, lock_path: Path) -> dict | None:
        """Read lock info from file."""
        try:
            if not lock_path.exists():
                return None
            
            content = lock_path.read_text().strip()
            if not content:
                return None
            
            lines = content.split("\n")
            info = {}
            for line in lines:
                if "=" in line:
                    key, value = line.split("=", 1)
                    info[key.strip()] = value.strip()
            
            return info if info else None
            
        except (OSError, IOError):
            return None
    
    def _is_lock_stale(self, lock_path: Path, stale_seconds: float = 300.0) -> bool:
        """
        Check if a lock file is stale.
        
        A lock is stale if:
        1. The owning process no longer exists
        2. The lock file is older than stale_seconds
        """
        info = self._read_lock_info(lock_path)
        
        if info is None:
            return True
        
        # Check if owning process still exists
        try:
            pid = int(info.get("pid", 0))
            if pid > 0:
                import psutil
                if not psutil.pid_exists(pid):
                    logger.debug(f"Lock owner PID {pid} no longer exists")
                    return True
        except (ValueError, ImportError):
            pass
        
        # Check lock age
        try:
            created = float(info.get("created", 0))
            if created > 0 and (time.time() - created) > stale_seconds:
                logger.debug(f"Lock is older than {stale_seconds}s")
                return True
        except ValueError:
            pass
        
        return False
    
    def _write_lock_file(self, lock_path: Path, operation: str) -> None:
        """Write lock file with current process info."""
        content = f"pid={os.getpid()}\n"
        content += f"created={time.time()}\n"
        content += f"operation={operation}\n"
        lock_path.write_text(content)
    
    def _remove_lock_file(self, lock_path: Path) -> None:
        """Remove lock file if it exists."""
        try:
            if lock_path.exists():
                lock_path.unlink()
        except (OSError, IOError) as e:
            logger.warning(f"Failed to remove lock file {lock_path}: {e}")
    
    def acquire(
        self,
        name: str,
        operation: str = "unknown",
        timeout: float = 30.0,
        retry_interval: float = 0.5,
        stale_timeout: float = 300.0,
    ) -> bool:
        """
        Acquire a lock for an instance.
        
        Args:
            name: Instance name
            operation: Description of the operation (for logging)
            timeout: Maximum time to wait for lock
            retry_interval: Time between lock acquisition attempts
            stale_timeout: Age after which a lock is considered stale
            
        Returns:
            True if lock was acquired
            
        Raises:
            LockTimeoutError: If lock cannot be acquired within timeout
        """
        lock_path = self._get_lock_path(name)
        start_time = time.time()
        
        while True:
            # Check if we already hold this lock
            if name in self._held_locks:
                logger.debug(f"Already holding lock for '{name}'")
                return True
            
            # Check if lock file exists
            if not lock_path.exists():
                # Try to create lock file
                try:
                    self._write_lock_file(lock_path, operation)
                    self._held_locks[name] = lock_path
                    logger.debug(f"Acquired lock for '{name}' (operation: {operation})")
                    return True
                except (OSError, IOError) as e:
                    logger.debug(f"Failed to create lock file: {e}")
            else:
                # Lock exists, check if stale
                if self._is_lock_stale(lock_path, stale_timeout):
                    logger.info(f"Removing stale lock for '{name}'")
                    self._remove_lock_file(lock_path)
                    continue
                
                # Lock is held by another process
                info = self._read_lock_info(lock_path)
                owner_pid = info.get("pid", "unknown") if info else "unknown"
                owner_op = info.get("operation", "unknown") if info else "unknown"
                logger.debug(
                    f"Lock for '{name}' held by PID {owner_pid} "
                    f"(operation: {owner_op})"
                )
            
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise LockTimeoutError(
                    f"Timeout waiting for lock on '{name}' after {elapsed:.1f}s"
                )
            
            # Wait and retry
            time.sleep(retry_interval)
    
    def release(self, name: str) -> bool:
        """
        Release a lock for an instance.
        
        Args:
            name: Instance name
            
        Returns:
            True if lock was released, False if we didn't hold it
        """
        if name not in self._held_locks:
            logger.debug(f"Not holding lock for '{name}'")
            return False
        
        lock_path = self._held_locks.pop(name)
        
        # Verify we own the lock before removing
        info = self._read_lock_info(lock_path)
        if info:
            try:
                owner_pid = int(info.get("pid", 0))
                if owner_pid != os.getpid():
                    logger.warning(
                        f"Lock for '{name}' owned by different PID: {owner_pid}"
                    )
                    return False
            except ValueError:
                pass
        
        self._remove_lock_file(lock_path)
        logger.debug(f"Released lock for '{name}'")
        return True
    
    def is_locked(self, name: str) -> bool:
        """Check if an instance is locked."""
        lock_path = self._get_lock_path(name)
        
        if not lock_path.exists():
            return False
        
        # Check if lock is stale
        if self._is_lock_stale(lock_path):
            return False
        
        return True
    
    def get_lock_info(self, name: str) -> dict | None:
        """Get info about who holds a lock."""
        lock_path = self._get_lock_path(name)
        return self._read_lock_info(lock_path)
    
    def cleanup_stale_locks(self, stale_timeout: float = 300.0) -> int:
        """
        Remove all stale lock files.
        
        Returns:
            Number of stale locks removed
        """
        removed = 0
        
        for lock_file in self.lock_dir.glob("*.lock"):
            if self._is_lock_stale(lock_file, stale_timeout):
                self._remove_lock_file(lock_file)
                removed += 1
                logger.info(f"Removed stale lock: {lock_file.name}")
        
        return removed


# Global lock manager instance
_lock_manager: InstanceLockManager | None = None


def get_lock_manager() -> InstanceLockManager:
    """Get the global lock manager instance."""
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = InstanceLockManager()
    return _lock_manager


@contextmanager
def instance_lock(
    name: str,
    operation: str = "unknown",
    timeout: float = 30.0,
) -> Generator[None, None, None]:
    """
    Context manager for instance locking.
    
    Usage:
        with instance_lock("my-instance", "start"):
            # Only one process can be here at a time
            start_instance("my-instance", config)
    
    Args:
        name: Instance name to lock
        operation: Description of the operation (for logging/debugging)
        timeout: Maximum time to wait for lock
        
    Raises:
        LockTimeoutError: If lock cannot be acquired within timeout
    """
    manager = get_lock_manager()
    
    try:
        manager.acquire(name, operation, timeout)
        yield
    finally:
        manager.release(name)


@contextmanager
def multi_instance_lock(
    names: list[str],
    operation: str = "unknown",
    timeout: float = 30.0,
) -> Generator[None, None, None]:
    """
    Context manager for locking multiple instances atomically.
    
    Acquires locks in sorted order to prevent deadlocks.
    
    Args:
        names: List of instance names to lock
        operation: Description of the operation
        timeout: Maximum time to wait for each lock
    """
    manager = get_lock_manager()
    acquired: list[str] = []
    
    try:
        # Acquire in sorted order to prevent deadlocks
        for name in sorted(names):
            manager.acquire(name, operation, timeout)
            acquired.append(name)
        yield
    finally:
        # Release in reverse order
        for name in reversed(acquired):
            manager.release(name)
