"""
Configuration module for llama-orchestrator.
"""

from llama_orchestrator.config.loader import (
    ConfigLoadError,
    discover_instances,
    get_bin_dir,
    get_bins_dir,
    get_instance_config,
    get_instances_dir,
    get_llama_server_path,
    get_logs_dir,
    get_project_root,
    get_state_dir,
    load_all_instances,
    load_config,
    save_config,
)
from llama_orchestrator.config.schema import (
    EXAMPLE_CONFIG,
    BinaryConfig,
    GpuConfig,
    HealthcheckConfig,
    InstanceConfig,
    LogsConfig,
    ModelConfig,
    RestartPolicy,
    ServerConfig,
)
from llama_orchestrator.config.validator import (
    ValidationIssue,
    ValidationResult,
    lint_config,
    validate_all_instances,
    validate_instance,
)

__all__ = [
    # Schema
    "InstanceConfig",
    "BinaryConfig",
    "ModelConfig",
    "ServerConfig",
    "GpuConfig",
    "HealthcheckConfig",
    "RestartPolicy",
    "LogsConfig",
    "EXAMPLE_CONFIG",
    # Loader
    "ConfigLoadError",
    "load_config",
    "load_all_instances",
    "get_instance_config",
    "save_config",
    "discover_instances",
    "get_project_root",
    "get_instances_dir",
    "get_bin_dir",
    "get_bins_dir",
    "get_llama_server_path",
    "get_state_dir",
    "get_logs_dir",
    # Validator
    "ValidationResult",
    "ValidationIssue",
    "validate_instance",
    "validate_all_instances",
    "lint_config",
]
