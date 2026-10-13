"""
Tests for instance locking module.

Tests file-based locking, timeout handling, and stale lock cleanup.
"""

import os
import threading
import time
from pathlib import Path

import pytest

from llama_orchestrator.engine.locking import (
    InstanceLockManager,
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
        """Test that lock acquisition times out.

        Deterministic (F3/R7): the injected oracle reports the foreign owner as
        alive, so no host PID-space assumption is involved — a fresh lock held
        by a live owner must always time out.
        """
        name = "test-instance"
        manager = InstanceLockManager(
            lock_dir=lock_manager.lock_dir, pid_oracle=lambda pid: True
        )

        # Create lock file manually (simulating another process)
        lock_path = manager._get_lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"pid={os.getpid() + 1}\ncreated={time.time()}\n")

        # Try to acquire with short timeout
        with pytest.raises(LockTimeoutError):
            manager.acquire(name, timeout=0.5, retry_interval=0.1)

        # The foreign lock must not have been deleted.
        assert lock_path.exists()

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


class TestLockStalenessMatrix:
    """R7/F3 staleness matrix — fully deterministic via injected pid oracle.

    Policy under test: stale iff (age > max_age) OR (owner PID dead AND age > grace).
    Fresh locks are never deleted; no reliance on live host PIDs.
    """

    @pytest.fixture
    def lock_dir(self, tmp_path):
        return tmp_path / "locks"

    def _manager(self, lock_dir, owner_alive: bool) -> InstanceLockManager:
        return InstanceLockManager(lock_dir=lock_dir, pid_oracle=lambda pid: owner_alive)

    def _write_lock(
        self, manager: InstanceLockManager, name: str, age_seconds: float, with_pid: bool = True
    ) -> Path:
        lock_path = manager._get_lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        created = time.time() - age_seconds
        content = f"pid={os.getpid() + 7}\ncreated={created}\n" if with_pid else f"created={created}\n"
        lock_path.write_text(content)
        return lock_path

    def test_fresh_foreign_lock_alive_owner_times_out(self, lock_dir):
        """Fresh foreign lock (live owner) → LockTimeoutError, file untouched."""
        manager = self._manager(lock_dir, owner_alive=True)
        name = "matrix-alive-fresh"
        lock_path = self._write_lock(manager, name, age_seconds=0.0)

        with pytest.raises(LockTimeoutError):
            manager.acquire(name, timeout=0.3, retry_interval=0.05)

        assert lock_path.exists()

    def test_fresh_foreign_lock_dead_owner_grace_protects(self, lock_dir):
        """F3 case A: fresh lock whose owner just died → grace period prevents
        immediate takeover (times out, file untouched)."""
        manager = self._manager(lock_dir, owner_alive=False)
        name = "matrix-dead-fresh"
        lock_path = self._write_lock(manager, name, age_seconds=0.0)

        with pytest.raises(LockTimeoutError):
            manager.acquire(name, timeout=0.3, retry_interval=0.05)

        assert lock_path.exists()

    def test_dead_owner_older_than_grace_reclaimed(self, lock_dir):
        """AC: dead-PID lock older than grace → acquired (stale via rule 2)."""
        manager = self._manager(lock_dir, owner_alive=False)
        name = "matrix-dead-old"
        # Age 10 s > default grace 5 s, < max age 300 s.
        self._write_lock(manager, name, age_seconds=10.0)

        assert manager.acquire(name, timeout=2.0, retry_interval=0.05) is True
        info = manager.get_lock_info(name)
        assert info is not None and int(info["pid"]) == os.getpid()
        manager.release(name)

    def test_alive_owner_older_than_max_age_reclaimed(self, lock_dir):
        """AC: alive-PID lock older than max_age → reclaimed (stale via rule 1)."""
        manager = self._manager(lock_dir, owner_alive=True)
        name = "matrix-alive-ancient"
        # Age 400 s > default max age 300 s; owner still reported alive.
        self._write_lock(manager, name, age_seconds=400.0)

        assert manager.acquire(name, timeout=2.0, retry_interval=0.05) is True
        info = manager.get_lock_info(name)
        assert info is not None and int(info["pid"]) == os.getpid()
        manager.release(name)

    def test_pidless_lock_age_only_rule(self, lock_dir):
        """Lock without pid field: only the age rule can apply."""
        # Fresh, no pid → not stale → timeout.
        fresh = self._manager(lock_dir, owner_alive=False)
        name_fresh = "matrix-nopid-fresh"
        path_fresh = self._write_lock(fresh, name_fresh, age_seconds=0.0, with_pid=False)
        with pytest.raises(LockTimeoutError):
            fresh.acquire(name_fresh, timeout=0.3, retry_interval=0.05)
        assert path_fresh.exists()

        # Old, no pid → stale via age rule → acquired.
        old = self._manager(lock_dir, owner_alive=True)
        name_old = "matrix-nopid-old"
        self._write_lock(old, name_old, age_seconds=400.0, with_pid=False)
        assert old.acquire(name_old, timeout=2.0, retry_interval=0.05) is True
        old.release(name_old)

    def test_empty_lock_file_is_stale(self, lock_dir):
        """Empty/unreadable lock file → stale → acquired."""
        manager = self._manager(lock_dir, owner_alive=True)
        name = "matrix-empty"
        lock_path = manager._get_lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("")

        assert manager.acquire(name, timeout=2.0, retry_interval=0.05) is True
        manager.release(name)

    def test_is_locked_follows_matrix(self, lock_dir):
        """is_locked() agrees with the staleness policy."""
        manager = self._manager(lock_dir, owner_alive=True)
        name = "matrix-islocked"
        path = self._write_lock(manager, name, age_seconds=0.0)
        assert manager.is_locked(name) is True

        # Owner dies and lock ages past grace → no longer locked.
        dead_manager = self._manager(lock_dir, owner_alive=False)
        path.write_text(f"pid={os.getpid() + 7}\ncreated={time.time() - 10}\n")
        assert dead_manager.is_locked(name) is False


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
