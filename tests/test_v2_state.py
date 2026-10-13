"""
Tests for V2 state management functions.

Tests runtime state CRUD, events logging, and schema migration.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from llama_orchestrator.engine.state import (
    SCHEMA_VERSION,
    DesiredState,
    HealthStatus,
    InstanceState,
    InstanceStatus,
    RuntimeState,
    check_state_divergence,
    cleanup_old_events,
    delete_runtime,
    get_db_connection,
    get_recent_events,
    get_schema_version,
    list_divergent_instances,
    load_all_runtime,
    load_runtime,
    load_state,
    log_event,
    reconcile_state,
    save_runtime,
    save_state_atomic,
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


class TestImportSideEffects:
    """R15/D2: importing engine.state must have no filesystem side effects."""

    def test_clean_import_creates_no_sqlite_file(self, tmp_path):
        """A fresh interpreter importing engine.state in an empty cwd must not
        create any state directory or SQLite file (init_db is explicit now)."""
        src = Path(__file__).resolve().parent.parent / "src"
        code = "import llama_orchestrator.engine.state as s; assert callable(s.init_db)"
        env = dict(os.environ, PYTHONPATH=str(src))
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert result.returncode == 0, f"import failed: {result.stderr}"
        assert not (tmp_path / "state").exists()
        assert not list(tmp_path.rglob("*.sqlite*"))


class TestDualTableConsistency:
    """R15/D3: start writes both tables atomically; the post-stop runtime-only
    divergence is detected and reconciled in either preferred direction.

    See docs/STATE_TABLE_AUTHORITY.md for the normative model.
    """

    @staticmethod
    def _cleanup(name):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM instances WHERE name = ?", (name,))
            conn.execute("DELETE FROM runtime WHERE name = ?", (name,))
            conn.commit()

    @staticmethod
    def _seed_start(name):
        """Mirror start_instance's write path: one atomic dual-table save."""
        now = time.time()
        state = InstanceState(
            name=name,
            pid=4321,
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
            start_time=now,
        )
        runtime = RuntimeState(
            name=name,
            pid=4321,
            port=8080,
            cmdline="llama-server --model test.gguf",
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
            started_at=now,
            last_seen_at=now,
        )
        save_state_atomic(state, runtime)

    @staticmethod
    def _simulate_stop(name):
        """Mirror stop_instance's write path: instances-table-only save."""
        state = InstanceState(
            name=name,
            pid=None,
            status=InstanceStatus.STOPPED,
            health=HealthStatus.UNKNOWN,
        )
        save_state_atomic(state)

    def test_start_writes_both_tables_atomically(self):
        """Both tables are written in one transaction on start and agree."""
        name = f"test-dual-{time.time()}"
        try:
            self._seed_start(name)
            loaded_state = load_state(name)
            loaded_runtime = load_runtime(name)
            assert loaded_state is not None
            assert loaded_runtime is not None
            assert loaded_state.status == loaded_runtime.status == InstanceStatus.RUNNING
            assert loaded_state.pid == loaded_runtime.pid == 4321
            assert check_state_divergence(name) is None
        finally:
            self._cleanup(name)

    def test_post_stop_runtime_only_divergence_detected_and_reconciled(self):
        """After stop (instances-only write), the stale runtime row is flagged
        as divergent and reconcile_state(prefer='state') converges it — the
        manual equivalent of what the Reconciler's liveness probe does."""
        name = f"test-diverge-{time.time()}"
        try:
            self._seed_start(name)
            self._simulate_stop(name)

            divergence = check_state_divergence(name)
            assert divergence is not None
            assert set(divergence["divergent_fields"]) == {"status", "pid", "health"}
            assert divergence["state_status"] == "stopped"
            assert divergence["runtime_status"] == "running"
            assert any(d["name"] == name for d in list_divergent_instances())

            result = reconcile_state(name, prefer="state")
            assert result["action"] == "synced_to_runtime"
            assert check_state_divergence(name) is None
            loaded_runtime = load_runtime(name)
            assert loaded_runtime.status == InstanceStatus.STOPPED
            assert loaded_runtime.pid is None
        finally:
            self._cleanup(name)

    def test_reconcile_prefers_runtime_when_legacy_row_corrupted(self):
        """An out-of-band corruption of only the legacy row is repaired by
        reconcile_state(prefer='runtime'), which trusts process truth."""
        name = f"test-repair-{time.time()}"
        try:
            self._seed_start(name)
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE instances SET status = ?, pid = NULL, health = ? WHERE name = ?",
                    ("stopped", "unknown", name),
                )
                conn.commit()

            divergence = check_state_divergence(name)
            assert divergence is not None
            assert "status" in divergence["divergent_fields"]

            result = reconcile_state(name, prefer="runtime")
            assert result["action"] == "synced_to_instances"
            assert check_state_divergence(name) is None
            loaded_state = load_state(name)
            assert loaded_state.status == InstanceStatus.RUNNING
            assert loaded_state.pid == 4321
        finally:
            self._cleanup(name)
