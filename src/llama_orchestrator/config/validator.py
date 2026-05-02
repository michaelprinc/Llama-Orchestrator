"""
Configuration validator for llama-orchestrator.

Provides validation logic beyond Pydantic schema validation:
- Model file existence
- Port availability and collision detection
- Log directory permissions
- GPU device availability
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import psutil

from llama_orchestrator.config.loader import (
    get_logs_dir,
    get_project_root,
    load_all_instances,
)

if TYPE_CHECKING:
    from llama_orchestrator.config.schema import InstanceConfig


@dataclass
class ValidationIssue:
    """A single validation issue."""
    
    instance: str
    field: str
    severity: Literal["error", "warning", "info"]
    message: str
    suggestion: str = ""
    
    def __str__(self) -> str:
        prefix = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}[self.severity]
        msg = f"{prefix} [{self.instance}] {self.field}: {self.message}"
        if self.suggestion:
            msg += f"\n   → {self.suggestion}"
        return msg


@dataclass
class ValidationResult:
    """Result of validating one or more configs."""
    
    issues: list[ValidationIssue] = field(default_factory=list)
    
    @property
    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return not any(i.severity == "error" for i in self.issues)
    
    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return any(i.severity == "warning" for i in self.issues)
    
    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for i in self.issues if i.severity == "error")
    
    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for i in self.issues if i.severity == "warning")
    
    def add(self, issue: ValidationIssue) -> None:
        """Add an issue to the result."""
        self.issues.append(issue)
    
    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.issues.extend(other.issues)


def resolve_model_path(config: InstanceConfig) -> Path:
    """Resolve the model path relative to project root."""
    model_path = config.model.path
    if not model_path.is_absolute():
        # Resolve relative to project root
        model_path = get_project_root() / model_path
    return model_path.resolve()


def validate_model_exists(config: InstanceConfig) -> ValidationResult:
    """Validate that the model file exists."""
    result = ValidationResult()
    
    model_path = resolve_model_path(config)
    
    if not model_path.exists():
        result.add(ValidationIssue(
            instance=config.name,
            field="model.path",
            severity="error",
            message=f"Model file not found: {model_path}",
            suggestion="Check the path or download the model file"
        ))
    elif not model_path.is_file():
        result.add(ValidationIssue(
            instance=config.name,
            field="model.path",
            severity="error",
            message=f"Path is not a file: {model_path}",
        ))
    elif model_path.stat().st_size == 0:
        result.add(ValidationIssue(
            instance=config.name,
            field="model.path",
            severity="error",
            message=f"Model file is empty: {model_path}",
        ))
    
    return result


def get_used_ports() -> set[int]:
    """Get set of currently used TCP ports."""
    used = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == "LISTEN":
                used.add(conn.laddr.port)
    except (psutil.AccessDenied, PermissionError):
        # On Windows, may need admin rights to see all connections
        pass
    return used


def validate_port_available(config: InstanceConfig) -> ValidationResult:
    """Validate that the configured port is not in use."""
    result = ValidationResult()
    
    used_ports = get_used_ports()
    port = config.server.port
    
    if port in used_ports:
        result.add(ValidationIssue(
            instance=config.name,
            field="server.port",
            severity="warning",
            message=f"Port {port} is currently in use",
            suggestion="Choose a different port or stop the process using it"
        ))
    
    return result


def validate_port_collisions(configs: dict[str, InstanceConfig]) -> ValidationResult:
    """Validate that no two instances use the same port."""
    result = ValidationResult()
    
    port_to_instances: dict[int, list[str]] = {}
    
    for name, config in configs.items():
        port = config.server.port
        if port not in port_to_instances:
            port_to_instances[port] = []
        port_to_instances[port].append(name)
    
    for port, instances in port_to_instances.items():
        if len(instances) > 1:
            for instance in instances:
                result.add(ValidationIssue(
                    instance=instance,
                    field="server.port",
                    severity="error",
                    message=f"Port {port} is used by multiple instances: {', '.join(instances)}",
                    suggestion="Each instance must use a unique port"
                ))
    
    return result


def validate_log_directory(config: InstanceConfig) -> ValidationResult:
    """Validate that log directory is writable."""
    result = ValidationResult()
    
    stdout_path, stderr_path = config.get_log_paths()
    logs_dir = get_logs_dir()
    
    # Resolve paths relative to logs dir
    stdout_full = logs_dir.parent / stdout_path
    stderr_full = logs_dir.parent / stderr_path
    
    for log_path in [stdout_full, stderr_full]:
        log_dir = log_path.parent
        
        try:
            # Try to create the directory
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # Check if we can write to it
            test_file = log_dir / ".write_test"
            try:
                test_file.touch()
                test_file.unlink()
            except OSError as e:
                result.add(ValidationIssue(
                    instance=config.name,
                    field="logs",
                    severity="error",
                    message=f"Cannot write to log directory: {log_dir}",
                    suggestion=f"Check permissions: {e}"
                ))
        except OSError as e:
            result.add(ValidationIssue(
                instance=config.name,
                field="logs",
                severity="error",
                message=f"Cannot create log directory: {log_dir}",
                suggestion=f"Check permissions: {e}"
            ))
    
    return result


def validate_gpu_config(config: InstanceConfig) -> ValidationResult:
    """Validate GPU configuration."""
    result = ValidationResult()
    
    if config.gpu.backend == "cpu":
        if config.gpu.layers > 0:
            result.add(ValidationIssue(
                instance=config.name,
                field="gpu.layers",
                severity="warning",
                message=f"GPU layers ({config.gpu.layers}) set but backend is 'cpu'",
                suggestion="Set backend to 'vulkan' or 'cuda' to use GPU, or set layers to 0"
            ))
    
    if config.gpu.backend == "vulkan":
        # Check GGML_VULKAN_DEVICE env var
        env_device = os.environ.get("GGML_VULKAN_DEVICE")
        if env_device is not None and env_device != str(config.gpu.device_id):
            result.add(ValidationIssue(
                instance=config.name,
                field="gpu.device_id",
                severity="info",
                message=f"GGML_VULKAN_DEVICE env var ({env_device}) differs from config ({config.gpu.device_id})",
                suggestion="The config value will be used when starting the instance"
            ))
    
    return result


def validate_instance(config: InstanceConfig, check_runtime: bool = True) -> ValidationResult:
    """
    Validate a single instance configuration.
    
    Args:
        config: Instance configuration to validate
        check_runtime: If True, check runtime conditions (port availability)
        
    Returns:
        ValidationResult with any issues found
    """
    result = ValidationResult()
    
    # Always check model
    result.merge(validate_model_exists(config))
    
    # Always check log directory
    result.merge(validate_log_directory(config))
    
    # Check GPU config
    result.merge(validate_gpu_config(config))
    
    # Runtime checks
    if check_runtime:
        result.merge(validate_port_available(config))
    
    return result


def validate_all_instances(check_runtime: bool = True) -> ValidationResult:
    """
    Validate all configured instances.
    
    Args:
        check_runtime: If True, check runtime conditions
        
    Returns:
        ValidationResult with all issues found
    """
    result = ValidationResult()
    
    try:
        configs = load_all_instances()
    except Exception as e:
        result.add(ValidationIssue(
            instance="*",
            field="config",
            severity="error",
            message=f"Failed to load instance configs: {e}",
        ))
        return result
    
    if not configs:
        result.add(ValidationIssue(
            instance="*",
            field="instances",
            severity="info",
            message="No instances configured",
            suggestion="Use 'llama-orch init <name>' to create an instance"
        ))
        return result
    
    # Validate each instance
    for config in configs.values():
        result.merge(validate_instance(config, check_runtime))
    
    # Check for port collisions across instances
    result.merge(validate_port_collisions(configs))
    
    return result


def lint_config(config: InstanceConfig) -> ValidationResult:
    """
    Run comprehensive linting on a configuration.
    
    This includes all validation checks plus additional best practice checks.
    """
    result = validate_instance(config, check_runtime=True)
    
    # Additional lint checks
    
    # Check for reasonable context size
    if config.model.context_size > 32768:
        result.add(ValidationIssue(
            instance=config.name,
            field="model.context_size",
            severity="warning",
            message=f"Large context size ({config.model.context_size}) may require significant memory",
            suggestion="Consider reducing if you experience OOM errors"
        ))
    
    # Check for reasonable thread count
    cpu_count = os.cpu_count() or 8
    if config.model.threads > cpu_count:
        result.add(ValidationIssue(
            instance=config.name,
            field="model.threads",
            severity="warning",
            message=f"Thread count ({config.model.threads}) exceeds CPU cores ({cpu_count})",
            suggestion=f"Consider reducing to {cpu_count} or less"
        ))
    
    # Check healthcheck interval
    if config.healthcheck.interval < 5:
        result.add(ValidationIssue(
            instance=config.name,
            field="healthcheck.interval",
            severity="warning",
            message=f"Very short healthcheck interval ({config.healthcheck.interval}s)",
            suggestion="Consider increasing to reduce overhead"
        ))
    
    return result
