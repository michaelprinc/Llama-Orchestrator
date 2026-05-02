"""
Tests for V2 state management functions.

Tests runtime state CRUD, events logging, and schema migration.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

from llama_orchestrator.engine.state import (
    SCHEMA_VERSION,
    DesiredState,
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    cleanup_old_events,
    delete_runtime,
    get_recent_events,
    get_schema_version,
    load_all_runtime,
    load_runtime,
    log_event,
    save_runtime,
    update_runtime_seen,
)


class TestRuntimeState:
    """Tests for RuntimeState CRUD operations."""
    
    def test_save_and_load_runtime(self):
        """Test saving and loading runtime state."""
        name = f"test-runtime-{time.time()}"
        
        runtime = RuntimeState(
            name=name,
            pid=12345,
            port=8080,
            cmdline="llama-server --model test.gguf",
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
            started_at=time.time(),
            last_seen_at=time.time(),
        )
        
        # Save
        save_runtime(runtime)
        
        # Load
        loaded = load_runtime(name)
        
        assert loaded is not None
        assert loaded.name == name
        assert loaded.pid == 12345
        assert loaded.port == 8080
        assert loaded.cmdline == "llama-server --model test.gguf"
        assert loaded.status == InstanceStatus.RUNNING
        assert loaded.health == HealthStatus.HEALTHY
        
        # Cleanup
        delete_runtime(name)
    
    def test_load_nonexistent_runtime(self):
        """Test loading runtime that doesn't exist."""
        loaded = load_runtime("nonexistent-instance-xyz")
        assert loaded is None
    
    def test_update_runtime(self):
        """Test updating existing runtime state."""
        name = f"test-update-{time.time()}"
        
        # Create initial
        runtime = RuntimeState(
            name=name,
            pid=1000,
            port=8080,
            status=InstanceStatus.RUNNING,
            health=HealthStatus.LOADING,
        )
        save_runtime(runtime)
        
        # Update
        runtime.health = HealthStatus.HEALTHY
        runtime.last_seen_at = time.time()
        save_runtime(runtime)
        
        # Verify
        loaded = load_runtime(name)
        assert loaded is not None
        assert loaded.health == HealthStatus.HEALTHY
        
        # Cleanup
        delete_runtime(name)
    
    def test_load_all_runtime(self):
        """Test loading all runtime states."""
        names = [f"test-all-{i}-{time.time()}" for i in range(3)]
        
        # Create multiple
        for i, name in enumerate(names):
            runtime = RuntimeState(
                name=name,
                pid=1000 + i,
                port=8080 + i,
                status=InstanceStatus.RUNNING,
            )
            save_runtime(runtime)
        
        # Load all
        all_runtime = load_all_runtime()
        
        # Verify all exist
        for name in names:
            assert name in all_runtime
            assert all_runtime[name].name == name
        
        # Cleanup
        for name in names:
            delete_runtime(name)
    
    def test_update_runtime_seen(self):
        """Test updating last_seen_at timestamp."""
        name = f"test-seen-{time.time()}"
        
        runtime = RuntimeState(
            name=name,
            pid=1234,
            status=InstanceStatus.RUNNING,
            last_seen_at=time.time() - 100,  # 100 seconds ago
        )
        save_runtime(runtime)
        
        old_seen = runtime.last_seen_at
        
        # Update seen
        update_runtime_seen(name)
        
        # Verify
        loaded = load_runtime(name)
        assert loaded is not None
        assert loaded.last_seen_at > old_seen
        
        # Cleanup
        delete_runtime(name)
    
    def test_delete_runtime(self):
        """Test deleting runtime state."""
        name = f"test-delete-{time.time()}"
        
        runtime = RuntimeState(name=name, pid=1234, status=InstanceStatus.RUNNING)
        save_runtime(runtime)
        
        # Verify exists
        assert load_runtime(name) is not None
        
        # Delete
        result = delete_runtime(name)
        assert result is True
        
        # Verify deleted
        assert load_runtime(name) is None
        
        # Delete again (should return False)
        result = delete_runtime(name)
        assert result is False


class TestEvents:
    """Tests for event logging functions."""
    
    def test_log_event(self):
        """Test logging an event."""
        event_id = log_event(
            event_type="test_event",
            message="Test event message",
            instance_name="test-instance",
            level="info",
            meta={"key": "value"},
        )
        
        assert event_id > 0
    
    def test_log_event_without_instance(self):
        """Test logging a global event."""
        event_id = log_event(
            event_type="system_event",
            message="System message",
            level="warning",
        )
        
        assert event_id > 0
    
    def test_get_recent_events(self):
        """Test retrieving recent events."""
        instance_name = f"test-events-{time.time()}"
        
        # Log several events
        for i in range(5):
            log_event(
                event_type=f"test_{i}",
                message=f"Test message {i}",
                instance_name=instance_name,
                level="info",
            )
        
        # Get events
        events = get_recent_events(instance_name=instance_name, limit=10)
        
        assert len(events) >= 5
        # Verify all events are present (order may vary due to timestamp granularity)
        event_types = {e["event_type"] for e in events}
        for i in range(5):
            assert f"test_{i}" in event_types
    
    def test_get_events_with_level_filter(self):
        """Test filtering events by level."""
        instance_name = f"test-level-{time.time()}"
        
        # Log events with different levels
        log_event("info_event", "Info", instance_name, "info")
        log_event("warning_event", "Warning", instance_name, "warning")
        log_event("error_event", "Error", instance_name, "error")
        
        # Get only errors
        errors = get_recent_events(instance_name=instance_name, level="error")
        
        assert all(e["level"] == "error" for e in errors)
    
    def test_event_meta_json(self):
        """Test that meta is correctly serialized/deserialized."""
        instance_name = f"test-meta-{time.time()}"
        
        meta = {
            "pid": 1234,
            "port": 8080,
            "nested": {"key": "value"},
        }
        
        log_event(
            event_type="meta_test",
            message="Testing meta",
            instance_name=instance_name,
            meta=meta,
        )
        
        events = get_recent_events(instance_name=instance_name, limit=1)
        
        assert len(events) == 1
        assert events[0]["meta"]["pid"] == 1234
        assert events[0]["meta"]["nested"]["key"] == "value"
    
    def test_cleanup_old_events(self):
        """Test cleaning up old events."""
        # This test just verifies the function runs without error
        # Actual cleanup would require manipulating timestamps
        deleted = cleanup_old_events(retention_days=365)
        assert deleted >= 0


class TestSchemaVersion:
    """Tests for schema version management."""
    
    def test_schema_version(self):
        """Test that schema version is correctly reported."""
        version = get_schema_version()
        assert version == SCHEMA_VERSION
        assert version >= 2  # V2 schema


class TestDesiredState:
    """Tests for DesiredState enum."""
    
    def test_desired_state_values(self):
        """Test DesiredState enum values."""
        assert DesiredState.RUNNING.value == "running"
        assert DesiredState.STOPPED.value == "stopped"
        # Verify enum has at least these two states
        assert len(DesiredState) >= 2
