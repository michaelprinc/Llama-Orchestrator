"""
Tests for instance locking module.

Tests file-based locking, timeout handling, and stale lock cleanup.
"""

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from llama_orchestrator.engine.locking import (
    InstanceLockManager,
    LockError,
    LockTimeoutError,
    get_lock_manager,
    instance_lock,
    multi_instance_lock,
)


class TestInstanceLockManager:
    """Tests for InstanceLockManager class."""
    
    @pytest.fixture
    def lock_manager(self, tmp_path):
        """Create a lock manager with temp directory."""
        return InstanceLockManager(lock_dir=tmp_path / "locks")
    
    def test_acquire_and_release(self, lock_manager):
        """Test basic acquire and release."""
        name = "test-instance"
        
        # Acquire
        result = lock_manager.acquire(name, operation="test")
        assert result is True
        assert lock_manager.is_locked(name)
        
        # Release
        result = lock_manager.release(name)
        assert result is True
        assert not lock_manager.is_locked(name)
    
    def test_acquire_already_held(self, lock_manager):
        """Test acquiring a lock we already hold."""
        name = "test-instance"
        
        lock_manager.acquire(name, operation="test1")
        
        # Acquiring again should succeed (we already hold it)
        result = lock_manager.acquire(name, operation="test2")
        assert result is True
        
        lock_manager.release(name)
    
    def test_lock_timeout(self, lock_manager):
        """Test that lock acquisition times out."""
        name = "test-instance"
        
        # Create lock file manually (simulating another process)
        lock_path = lock_manager._get_lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"pid={os.getpid() + 1}\ncreated={time.time()}\n")
        
        # Try to acquire with short timeout
        with pytest.raises(LockTimeoutError):
            lock_manager.acquire(name, timeout=0.5, retry_interval=0.1)
    
    def test_stale_lock_cleanup(self, lock_manager):
        """Test that stale locks are cleaned up."""
        name = "test-instance"
        
        # Create an old lock file with non-existent PID
        lock_path = lock_manager._get_lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use a very old timestamp and invalid PID
        old_time = time.time() - 1000
        lock_path.write_text(f"pid=999999999\ncreated={old_time}\n")
        
        # Should be able to acquire (stale lock cleaned up)
        result = lock_manager.acquire(name, stale_timeout=100)
        assert result is True
        
        lock_manager.release(name)
    
    def test_get_lock_info(self, lock_manager):
        """Test getting lock info."""
        name = "test-instance"
        
        lock_manager.acquire(name, operation="my-operation")
        
        info = lock_manager.get_lock_info(name)
        
        assert info is not None
        assert info["pid"] == str(os.getpid())
        assert info["operation"] == "my-operation"
        
        lock_manager.release(name)
    
    def test_cleanup_stale_locks(self, lock_manager):
        """Test bulk cleanup of stale locks."""
        # Create several stale lock files
        for i in range(3):
            lock_path = lock_manager._get_lock_path(f"stale-{i}")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            old_time = time.time() - 1000
            lock_path.write_text(f"pid=999999999\ncreated={old_time}\n")
        
        # Cleanup
        removed = lock_manager.cleanup_stale_locks(stale_timeout=100)
        
        assert removed == 3


class TestInstanceLockContextManager:
    """Tests for instance_lock context manager."""
    
    def test_context_manager_basic(self):
        """Test basic context manager usage."""
        name = f"test-ctx-{time.time()}"
        manager = get_lock_manager()
        
        with instance_lock(name, operation="test"):
            assert manager.is_locked(name)
        
        assert not manager.is_locked(name)
    
    def test_context_manager_exception(self):
        """Test that lock is released on exception."""
        name = f"test-exc-{time.time()}"
        manager = get_lock_manager()
        
        try:
            with instance_lock(name, operation="test"):
                assert manager.is_locked(name)
                raise ValueError("Test exception")
        except ValueError:
            pass
        
        # Lock should be released
        assert not manager.is_locked(name)


class TestMultiInstanceLock:
    """Tests for multi_instance_lock context manager."""
    
    def test_multi_lock_basic(self):
        """Test locking multiple instances."""
        names = [f"multi-{i}-{time.time()}" for i in range(3)]
        manager = get_lock_manager()
        
        with multi_instance_lock(names, operation="batch"):
            for name in names:
                assert manager.is_locked(name)
        
        for name in names:
            assert not manager.is_locked(name)
    
    def test_multi_lock_order(self):
        """Test that locks are acquired in sorted order."""
        # This prevents deadlocks when multiple processes try to lock
        # the same set of instances in different orders
        names = ["z-instance", "a-instance", "m-instance"]
        
        with multi_instance_lock(names, operation="test"):
            pass  # Just verify no deadlock


class TestLockingThreadSafety:
    """Tests for thread safety of locking."""
    
    def test_concurrent_lock_attempts(self, tmp_path):
        """Test concurrent lock attempts from multiple threads."""
        manager = InstanceLockManager(lock_dir=tmp_path / "locks")
        name = "concurrent-test"
        
        results = []
        errors = []
        
        def try_lock(thread_id):
            try:
                manager.acquire(name, operation=f"thread-{thread_id}", timeout=2)
                time.sleep(0.1)  # Hold lock briefly
                manager.release(name)
                results.append(thread_id)
            except LockTimeoutError:
                errors.append(thread_id)
        
        threads = [threading.Thread(target=try_lock, args=(i,)) for i in range(3)]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # At least one should succeed
        assert len(results) >= 1
