"""
Engine module for llama-orchestrator.

Provides process management, state persistence, and command building.
"""

from llama_orchestrator.engine.command import (
    build_command,
    build_env,
    format_command,
    resolve_model_path,
    validate_executable,
)
from llama_orchestrator.engine.process import (
    ProcessError,
    get_instance_status,
    get_log_files,
    is_process_running,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
    RuntimeState,
    delete_runtime,
    delete_state,
    get_health_history,
    get_recent_events,
    get_schema_version,
    init_db,
    load_all_runtime,
    load_all_states,
    load_runtime,
    load_state,
    log_event,
    record_health_check,
    save_runtime,
    save_state,
    update_runtime_seen,
)
from llama_orchestrator.engine.validator import (
    ProcessValidation,
    ValidationStatus,
    cleanup_stale_runtime,
    find_orphaned_processes,
    get_process_info,
    validate_process,
)
from llama_orchestrator.engine.locking import (
    LockError,
    LockTimeoutError,
    InstanceLockManager,
    get_lock_manager,
    instance_lock,
    multi_instance_lock,
)
from llama_orchestrator.engine.detach import (
    DetachResult,
    LogRotator,
    get_instance_log_dir,
    get_latest_logs,
    setup_log_files,
    start_detached,
    stop_detached,
    tail_log,
)
from llama_orchestrator.engine.logging_config import (
    InstanceLogHandler,
    LogConfig,
    cleanup_old_logs,
    get_instance_log_handler,
    setup_orchestrator_logging,
)
from llama_orchestrator.engine.reconciler import (
    ReconcileAction,
    ReconcileResult,
    ReconcileSummary,
    Reconciler,
    reconcile_all,
    reconcile_instance,
)

__all__ = [
    # Command
    "build_command",
    "build_env",
    "format_command",
    "resolve_model_path",
    "validate_executable",
    # Process
    "ProcessError",
    "start_instance",
    "stop_instance",
    "restart_instance",
    "get_instance_status",
    "list_instances",
    "get_log_files",
    "is_process_running",
    # State
    "InstanceState",
    "InstanceStatus",
    "HealthStatus",
    "RuntimeState",
    "init_db",
    "save_state",
    "load_state",
    "load_all_states",
    "delete_state",
    "record_health_check",
    "get_health_history",
    # V2 State
    "save_runtime",
    "load_runtime",
    "load_all_runtime",
    "update_runtime_seen",
    "delete_runtime",
    "log_event",
    "get_recent_events",
    "get_schema_version",
    # Validator
    "ProcessValidation",
    "ValidationStatus",
    "validate_process",
    "get_process_info",
    "find_orphaned_processes",
    "cleanup_stale_runtime",
    # Locking
    "LockError",
    "LockTimeoutError",
    "InstanceLockManager",
    "get_lock_manager",
    "instance_lock",
    "multi_instance_lock",
    # Detach
    "DetachResult",
    "LogRotator",
    "get_instance_log_dir",
    "get_latest_logs",
    "setup_log_files",
    "start_detached",
    "stop_detached",
    "tail_log",
    # Logging Config
    "InstanceLogHandler",
    "LogConfig",
    "cleanup_old_logs",
    "get_instance_log_handler",
    "setup_orchestrator_logging",
    # Reconciler
    "ReconcileAction",
    "ReconcileResult",
    "ReconcileSummary",
    "Reconciler",
    "reconcile_all",
    "reconcile_instance",
]
