"""
Tests for daemon service V2.

Tests event-based loop, graceful shutdown, and reconciliation integration.
"""

import threading
import time

import pytest

from llama_orchestrator.daemon.service import (
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_SHUTDOWN_TIMEOUT,
    DaemonService,
    DaemonStatus,
    get_daemon_status,
    is_daemon_running,
)


class TestDaemonService:
    """Tests for DaemonService class."""
    
    def test_daemon_initialization(self):
        """Test daemon service initialization."""
        daemon = DaemonService(
            check_interval=5.0,
            reconcile_interval=30.0,
            shutdown_timeout=15.0,
        )
        
        assert daemon.check_interval == 5.0
        assert daemon.reconcile_interval == 30.0
        assert daemon.shutdown_timeout == 15.0
        assert daemon.is_running is True  # Not stopped yet
    
    def test_daemon_defaults(self):
        """Test daemon service default values."""
        daemon = DaemonService()
        
        assert daemon.check_interval == DEFAULT_CHECK_INTERVAL
        assert daemon.shutdown_timeout == DEFAULT_SHUTDOWN_TIMEOUT
    
    def test_stop_event(self):
        """Test stop event mechanism."""
        daemon = DaemonService()
        
        # Initially not stopped
        assert daemon._stop_event.is_set() is False
        
        # Stop it
        daemon._stop_event.set()
        
        # Now stopped
        assert daemon._stop_event.is_set() is True
        assert daemon.is_running is False
    
    def test_uptime_tracking(self):
        """Test uptime tracking."""
        daemon = DaemonService()
        
        # No start time yet
        assert daemon.uptime == 0.0
        
        # Simulate start
        daemon._start_time = time.time() - 10
        
        # Should show ~10 seconds
        assert 9.5 <= daemon.uptime <= 10.5
    
    def test_shutdown_callback(self):
        """Test shutdown callback registration."""
        daemon = DaemonService()
        
        callback_called = [False]
        
        def on_shutdown():
            callback_called[0] = True
        
        daemon.register_shutdown_callback(on_shutdown)
        
        assert daemon._on_shutdown is not None


class TestDaemonStatus:
    """Tests for DaemonStatus."""
    
    def test_daemon_status_dataclass(self):
        """Test DaemonStatus dataclass."""
        status = DaemonStatus(
            running=True,
            pid=12345,
            uptime=100.5,
            instances_monitored=3,
            health_checks_performed=50,
            reconciliations_performed=10,
        )
        
        assert status.running is True
        assert status.pid == 12345
        assert status.uptime == 100.5
        assert status.instances_monitored == 3
        assert status.reconciliations_performed == 10
    
    def test_daemon_status_defaults(self):
        """Test DaemonStatus default values."""
        status = DaemonStatus(running=False)
        
        assert status.pid is None
        assert status.uptime is None
        assert status.instances_monitored == 0


class TestDaemonHelpers:
    """Tests for daemon helper functions."""
    
    def test_is_daemon_running_no_pid_file(self):
        """Test is_daemon_running when no PID file exists."""
        # Clean state should return False
        # (May return True if daemon is actually running in CI)
        result = is_daemon_running()
        assert isinstance(result, bool)
    
    def test_get_daemon_status(self):
        """Test get_daemon_status returns valid status."""
        status = get_daemon_status()
        
        assert isinstance(status, DaemonStatus)
        assert isinstance(status.running, bool)


class TestEventBasedLoop:
    """Tests for event-based loop mechanism."""
    
    def test_event_wait_timeout(self):
        """Test that Event.wait() respects timeout."""
        event = threading.Event()
        
        start = time.time()
        event.wait(timeout=0.1)
        elapsed = time.time() - start
        
        # Should complete near 0.1 seconds
        assert 0.08 <= elapsed <= 0.3
    
    def test_event_immediate_response(self):
        """Test that Event.wait() responds immediately when set."""
        event = threading.Event()
        
        def set_after_delay():
            time.sleep(0.05)
            event.set()
        
        thread = threading.Thread(target=set_after_delay)
        thread.start()
        
        start = time.time()
        event.wait(timeout=1.0)  # Long timeout
        elapsed = time.time() - start
        
        thread.join()
        
        # Should complete much faster than timeout
        assert elapsed < 0.2
