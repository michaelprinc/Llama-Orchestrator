"""
State reconciler for Llama Orchestrator V2.

Provides automatic reconciliation between database state and actual
running processes to handle orphans, stale state, and crashes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    delete_runtime,
    load_all_runtime,
    load_runtime,
    log_event,
    save_runtime,
)
from llama_orchestrator.engine.validator import (
    ProcessValidation,
    ValidationStatus,
    find_orphaned_processes,
    validate_process,
)

logger = logging.getLogger(__name__)


class ReconcileAction(Enum):
    """Action taken during reconciliation."""
    
    NONE = "none"                    # No action needed
    MARKED_STOPPED = "marked_stopped"  # Process gone, marked as stopped
    MARKED_ERROR = "marked_error"    # Process error detected
    CLEANED_UP = "cleaned_up"        # Stale state cleaned up
    ORPHAN_DETECTED = "orphan_detected"  # Orphan process found
    PID_CORRECTED = "pid_corrected"  # PID mismatch corrected


@dataclass
class ReconcileResult:
    """Result of a single instance reconciliation."""
    
    name: str
    action: ReconcileAction
    previous_status: InstanceStatus | None
    new_status: InstanceStatus | None
    message: str
    validation: ProcessValidation | None = None


@dataclass
class ReconcileSummary:
    """Summary of reconciliation batch."""
    
    timestamp: float = field(default_factory=time.time)
    total_checked: int = 0
    actions_taken: int = 0
    stopped_count: int = 0
    error_count: int = 0
    orphan_count: int = 0
    results: list[ReconcileResult] = field(default_factory=list)
    
    def add_result(self, result: ReconcileResult) -> None:
        """Add a result to the summary."""
        self.results.append(result)
        self.total_checked += 1
        
        if result.action != ReconcileAction.NONE:
            self.actions_taken += 1
        
        if result.action == ReconcileAction.MARKED_STOPPED:
            self.stopped_count += 1
        elif result.action == ReconcileAction.MARKED_ERROR:
            self.error_count += 1
        elif result.action == ReconcileAction.ORPHAN_DETECTED:
            self.orphan_count += 1


def reconcile_instance(
    name: str,
    auto_cleanup: bool = True,
    stale_threshold: float = 300.0,
) -> ReconcileResult:
    """
    Reconcile a single instance's state with actual process.
    
    Checks if the database state matches reality and corrects if needed.
    
    Args:
        name: Instance name to reconcile
        auto_cleanup: Whether to automatically fix issues
        stale_threshold: Seconds before considering state stale
        
    Returns:
        ReconcileResult with action taken
    """
    runtime = load_runtime(name)
    
    if runtime is None:
        return ReconcileResult(
            name=name,
            action=ReconcileAction.NONE,
            previous_status=None,
            new_status=None,
            message="No runtime state found",
        )
    
    previous_status = runtime.status
    
    # Skip if already stopped
    if runtime.status == InstanceStatus.STOPPED:
        return ReconcileResult(
            name=name,
            action=ReconcileAction.NONE,
            previous_status=previous_status,
            new_status=runtime.status,
            message="Instance already stopped",
        )
    
    # Validate the process
    validation = validate_process(
        name=name,
        stale_threshold_seconds=stale_threshold,
    )
    
    # Handle based on validation status
    if validation.status == ValidationStatus.VALID:
        # All good, update last seen
        runtime.last_seen_at = time.time()
        if auto_cleanup:
            save_runtime(runtime)
        
        return ReconcileResult(
            name=name,
            action=ReconcileAction.NONE,
            previous_status=previous_status,
            new_status=runtime.status,
            message="Process is running correctly",
            validation=validation,
        )
    
    elif validation.status == ValidationStatus.MISSING:
        # Process is gone
        if auto_cleanup:
            runtime.status = InstanceStatus.STOPPED
            runtime.health = HealthStatus.UNKNOWN
            runtime.pid = None
            runtime.last_error = "Process died unexpectedly"
            save_runtime(runtime)
            
            log_event(
                event_type="process_died",
                message=f"Process for '{name}' is no longer running",
                instance_name=name,
                level="warning",
            )
        
        return ReconcileResult(
            name=name,
            action=ReconcileAction.MARKED_STOPPED,
            previous_status=previous_status,
            new_status=InstanceStatus.STOPPED,
            message="Process no longer running",
            validation=validation,
        )
    
    elif validation.status == ValidationStatus.PID_MISMATCH:
        # Different process on this PID
        if auto_cleanup:
            runtime.status = InstanceStatus.ERROR
            runtime.health = HealthStatus.ERROR
            runtime.last_error = "PID reused by different process"
            save_runtime(runtime)
            
            log_event(
                event_type="pid_mismatch",
                message=f"PID {runtime.pid} is now a different process",
                instance_name=name,
                level="error",
            )
        
        return ReconcileResult(
            name=name,
            action=ReconcileAction.MARKED_ERROR,
            previous_status=previous_status,
            new_status=InstanceStatus.ERROR,
            message="PID mismatch - different process",
            validation=validation,
        )
    
    elif validation.status == ValidationStatus.ZOMBIE:
        # Zombie process
        if auto_cleanup:
            runtime.status = InstanceStatus.ERROR
            runtime.health = HealthStatus.ERROR
            runtime.last_error = "Process is zombie"
            save_runtime(runtime)
            
            log_event(
                event_type="zombie_process",
                message=f"Process {runtime.pid} is a zombie",
                instance_name=name,
                level="error",
            )
        
        return ReconcileResult(
            name=name,
            action=ReconcileAction.MARKED_ERROR,
            previous_status=previous_status,
            new_status=InstanceStatus.ERROR,
            message="Zombie process detected",
            validation=validation,
        )
    
    elif validation.status == ValidationStatus.STALE:
        # Process not responding
        logger.warning(f"Instance '{name}' appears stale")
        
        return ReconcileResult(
            name=name,
            action=ReconcileAction.NONE,
            previous_status=previous_status,
            new_status=runtime.status,
            message=f"Process stale (not seen for {validation.last_seen_age_seconds:.0f}s)",
            validation=validation,
        )
    
    # Unknown status
    return ReconcileResult(
        name=name,
        action=ReconcileAction.NONE,
        previous_status=previous_status,
        new_status=runtime.status,
        message=f"Unknown validation status: {validation.status}",
        validation=validation,
    )


def reconcile_all(
    auto_cleanup: bool = True,
    stale_threshold: float = 300.0,
    detect_orphans: bool = True,
) -> ReconcileSummary:
    """
    Reconcile all instances and optionally detect orphans.
    
    Args:
        auto_cleanup: Whether to automatically fix issues
        stale_threshold: Seconds before considering state stale
        detect_orphans: Whether to detect orphan processes
        
    Returns:
        ReconcileSummary with all results
    """
    summary = ReconcileSummary()
    
    # Load all runtime states
    all_runtime = load_all_runtime()
    known_names = list(all_runtime.keys())
    
    # Reconcile each instance
    for name in known_names:
        result = reconcile_instance(
            name=name,
            auto_cleanup=auto_cleanup,
            stale_threshold=stale_threshold,
        )
        summary.add_result(result)
    
    # Detect orphan processes
    if detect_orphans:
        orphans = find_orphaned_processes(known_names)
        
        for orphan in orphans:
            result = ReconcileResult(
                name=f"orphan-{orphan['pid']}",
                action=ReconcileAction.ORPHAN_DETECTED,
                previous_status=None,
                new_status=None,
                message=f"Orphan llama-server: PID {orphan['pid']}",
            )
            summary.add_result(result)
    
    # Log summary
    if summary.actions_taken > 0:
        log_event(
            event_type="reconciliation",
            message=f"Reconciled {summary.total_checked} instances: "
                    f"{summary.stopped_count} stopped, {summary.error_count} errors, "
                    f"{summary.orphan_count} orphans",
            level="info",
            meta={
                "total_checked": summary.total_checked,
                "actions_taken": summary.actions_taken,
                "stopped_count": summary.stopped_count,
                "error_count": summary.error_count,
                "orphan_count": summary.orphan_count,
            },
        )
    
    return summary


class Reconciler:
    """
    Background reconciler that periodically checks state consistency.
    
    Can be used by the daemon to automatically detect and handle
    process issues.
    """
    
    def __init__(
        self,
        interval: float = 30.0,
        auto_cleanup: bool = True,
        stale_threshold: float = 300.0,
        detect_orphans: bool = True,
        on_reconcile: Callable[[ReconcileSummary], None] | None = None,
    ):
        """
        Initialize the reconciler.
        
        Args:
            interval: Seconds between reconciliation runs
            auto_cleanup: Whether to automatically fix issues
            stale_threshold: Seconds before considering state stale
            detect_orphans: Whether to detect orphan processes
            on_reconcile: Callback after each reconciliation
        """
        self.interval = interval
        self.auto_cleanup = auto_cleanup
        self.stale_threshold = stale_threshold
        self.detect_orphans = detect_orphans
        self.on_reconcile = on_reconcile
        
        self._last_run: float = 0
        self._run_count: int = 0
    
    def should_run(self) -> bool:
        """Check if reconciliation should run based on interval."""
        return time.time() - self._last_run >= self.interval
    
    def run(self) -> ReconcileSummary:
        """Run a reconciliation pass."""
        self._last_run = time.time()
        self._run_count += 1
        
        summary = reconcile_all(
            auto_cleanup=self.auto_cleanup,
            stale_threshold=self.stale_threshold,
            detect_orphans=self.detect_orphans,
        )
        
        if self.on_reconcile:
            self.on_reconcile(summary)
        
        return summary
    
    def run_if_due(self) -> ReconcileSummary | None:
        """Run reconciliation if interval has passed."""
        if self.should_run():
            return self.run()
        return None
    
    @property
    def run_count(self) -> int:
        """Number of times reconciliation has run."""
        return self._run_count
