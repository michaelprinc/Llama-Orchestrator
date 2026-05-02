"""
Tests for reconciler module.

Tests state/process reconciliation logic.
"""

import time

import pytest

from llama_orchestrator.engine.reconciler import (
    ReconcileAction,
    ReconcileResult,
    ReconcileSummary,
    Reconciler,
    reconcile_all,
    reconcile_instance,
)
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    delete_runtime,
    save_runtime,
)


class TestReconcileResult:
    """Tests for ReconcileResult and ReconcileSummary."""
    
    def test_reconcile_result_creation(self):
        """Test creating a reconcile result."""
        result = ReconcileResult(
            name="test-instance",
            action=ReconcileAction.MARKED_STOPPED,
            previous_status=InstanceStatus.RUNNING,
            new_status=InstanceStatus.STOPPED,
            message="Process stopped",
        )
        
        assert result.name == "test-instance"
        assert result.action == ReconcileAction.MARKED_STOPPED
    
    def test_reconcile_summary(self):
        """Test ReconcileSummary aggregation."""
        summary = ReconcileSummary()
        
        # Add several results
        summary.add_result(ReconcileResult(
            name="inst1",
            action=ReconcileAction.NONE,
            previous_status=InstanceStatus.RUNNING,
            new_status=InstanceStatus.RUNNING,
            message="OK",
        ))
        
        summary.add_result(ReconcileResult(
            name="inst2",
            action=ReconcileAction.MARKED_STOPPED,
            previous_status=InstanceStatus.RUNNING,
            new_status=InstanceStatus.STOPPED,
            message="Stopped",
        ))
        
        summary.add_result(ReconcileResult(
            name="inst3",
            action=ReconcileAction.MARKED_ERROR,
            previous_status=InstanceStatus.RUNNING,
            new_status=InstanceStatus.ERROR,
            message="Error",
        ))
        
        assert summary.total_checked == 3
        assert summary.actions_taken == 2
        assert summary.stopped_count == 1
        assert summary.error_count == 1


class TestReconcileInstance:
    """Tests for reconcile_instance function."""
    
    def test_reconcile_nonexistent(self):
        """Test reconciling a non-existent instance."""
        result = reconcile_instance("nonexistent-xyz-123")
        
        assert result.action == ReconcileAction.NONE
        assert "No runtime state" in result.message
    
    def test_reconcile_stopped_instance(self):
        """Test reconciling an already stopped instance."""
        name = f"test-stopped-{time.time()}"
        
        runtime = RuntimeState(
            name=name,
            status=InstanceStatus.STOPPED,
        )
        save_runtime(runtime)
        
        try:
            result = reconcile_instance(name)
            
            assert result.action == ReconcileAction.NONE
            assert "already stopped" in result.message.lower()
        finally:
            delete_runtime(name)
    
    def test_reconcile_missing_process(self):
        """Test reconciling instance with missing process."""
        name = f"test-missing-{time.time()}"
        
        # Create runtime with non-existent PID
        runtime = RuntimeState(
            name=name,
            pid=999999999,  # Invalid PID
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
        )
        save_runtime(runtime)
        
        try:
            result = reconcile_instance(name, auto_cleanup=True)
            
            assert result.action == ReconcileAction.MARKED_STOPPED
            assert result.new_status == InstanceStatus.STOPPED
        finally:
            delete_runtime(name)


class TestReconcileAll:
    """Tests for reconcile_all function."""
    
    def test_reconcile_all_empty(self):
        """Test reconciling with no instances."""
        summary = reconcile_all(detect_orphans=False)
        
        assert isinstance(summary, ReconcileSummary)
        assert summary.total_checked >= 0


class TestReconciler:
    """Tests for Reconciler class."""
    
    def test_reconciler_interval(self):
        """Test reconciler interval checking."""
        reconciler = Reconciler(interval=1.0)
        
        # Should run immediately (never run before)
        assert reconciler.should_run() is True
        
        # Run it
        reconciler.run()
        
        # Should not run again immediately
        assert reconciler.should_run() is False
        
        # Wait and check again
        time.sleep(1.1)
        assert reconciler.should_run() is True
    
    def test_reconciler_run_count(self):
        """Test reconciler tracks run count."""
        reconciler = Reconciler(interval=0.1)
        
        assert reconciler.run_count == 0
        
        reconciler.run()
        assert reconciler.run_count == 1
        
        time.sleep(0.2)
        reconciler.run()
        assert reconciler.run_count == 2
    
    def test_reconciler_callback(self):
        """Test reconciler callback is called."""
        results = []
        
        def on_reconcile(summary):
            results.append(summary)
        
        reconciler = Reconciler(
            interval=0.1,
            on_reconcile=on_reconcile,
        )
        
        reconciler.run()
        
        assert len(results) == 1
        assert isinstance(results[0], ReconcileSummary)
