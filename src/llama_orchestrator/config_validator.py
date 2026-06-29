"""Configuration validator for llama-orchestrator.

Validates model configurations for conflicts, missing dependencies,
and consistency between structured config and generated CLI arguments.

This module is the foundation for Phase 3 of the GUI redesign.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(Enum):
    """Validation severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OK = "ok"


class ValidationResultCode(Enum):
    """Standardized validation result codes."""
    MODEL_FILE_MISSING = "MODEL_FILE_MISSING"
    PORT_CONFLICT = "PORT_CONFLICT"
    GPU_DEVICE_CONFLICT = "GPU_DEVICE_CONFLICT"
    GPU_BINDING_UNRESOLVED = "GPU_BINDING_UNRESOLVED"
    PARALLEL_CONFLICT = "PARALLEL_CONFLICT"
    DUPLICATE_ARGS = "DUPLICATE_ARGS"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    RESTART_REQUIRED = "RESTART_REQUIRED"
    ARGS_CONFLICT = "ARGS_CONFLICT"
    VALID = "VALID"


@dataclass(frozen=True)
class ValidationResult:
    """A single validation result."""
    severity: Severity
    code: ValidationResultCode
    message: str
    suggested_fix: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "severity": self.severity.value,
            "code": self.code.value,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
            "field": self.field,
        }


@dataclass
class ConfigValidator:
    """Validates model configurations for conflicts and consistency.

    Attributes:
        detected_gpus: Dict mapping device IDs to GPU info.
        hardware_aliases: Dict mapping alias names to GPU device IDs.
    """
    detected_gpus: dict[str, dict[str, Any]] = field(default_factory=dict)
    hardware_aliases: dict[str, str] = field(default_factory=dict)

    def validate(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Run all validation checks on a configuration.

        Args:
            config: The model configuration dict to validate.

        Returns:
            List of validation results.
        """
        results: list[ValidationResult] = []

        # Run all validation checks
        results.extend(self._check_model_file(config))
        results.extend(self._check_port(config))
        results.extend(self._check_gpu_binding(config))
        results.extend(self._check_gpu_device_conflict(config))
        results.extend(self._check_parallel_conflict(config))
        results.extend(self._check_duplicate_args(config))
        results.extend(self._check_backend_available(config))
        results.extend(self._check_args_conflicts(config))

        return results

    def get_status(self, config: dict[str, Any]) -> str:
        """Get a human-readable status string for a configuration.

        Args:
            config: The model configuration dict.

        Returns:
            Status string like 'ready', 'config warning', 'gpu missing', etc.
        """
        results = self.validate(config)

        # Check for critical errors first
        for result in results:
            if result.severity == Severity.ERROR:
                if result.code == ValidationResultCode.GPU_BINDING_UNRESOLVED:
                    return "gpu missing"
                if result.code == ValidationResultCode.MODEL_FILE_MISSING:
                    return "model missing"
                if result.code == ValidationResultCode.PORT_CONFLICT:
                    return "port conflict"

        # Check for warnings
        for result in results:
            if result.severity == Severity.WARNING:
                if result.code == ValidationResultCode.GPU_DEVICE_CONFLICT:
                    return "config warning"
                if result.code == ValidationResultCode.PARALLEL_CONFLICT:
                    return "args conflict"
                if result.code == ValidationResultCode.DUPLICATE_ARGS:
                    return "args conflict"

        return "ready"

    def _check_model_file(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check if the model file exists."""
        model_path = config.get("model", {}).get("path", "")
        if model_path and not os.path.exists(model_path):
            return [ValidationResult(
                severity=Severity.ERROR,
                code=ValidationResultCode.MODEL_FILE_MISSING,
                message=f"Model file not found: {model_path}",
                suggested_fix="verify_model_path",
                field="model.path",
            )]
        return []

    def _check_port(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check if the port is available."""
        port = config.get("server", {}).get("port")
        if port is not None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
            except OSError:
                return [ValidationResult(
                    severity=Severity.ERROR,
                    code=ValidationResultCode.PORT_CONFLICT,
                    message=f"Port {port} is already in use",
                    suggested_fix="change_port",
                    field="server.port",
                )]
        return []

    def _check_gpu_binding(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check if GPU binding can be resolved."""
        gpu_config = config.get("gpu", {})
        binding = gpu_config.get("binding")

        if binding and binding.get("type") == "alias":
            alias_value = binding.get("value")
            if alias_value and alias_value not in self.hardware_aliases:
                return [ValidationResult(
                    severity=Severity.ERROR,
                    code=ValidationResultCode.GPU_BINDING_UNRESOLVED,
                    message=f"GPU alias '{alias_value}' cannot be resolved",
                    suggested_fix="remap_gpu",
                    field="gpu.binding",
                )]

        return []

    def _check_gpu_device_conflict(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check for conflicts between gpu.device_id and --device arg."""
        gpu_config = config.get("gpu", {})
        device_id = gpu_config.get("device_id")
        args = config.get("args", [])

        if device_id is not None and args:
            # Parse args to find --device
            from llama_orchestrator.runtime_args import parse_args_list
            parsed = parse_args_list(args)
            device_arg = parsed.get("--device")

            if device_arg is not None:
                try:
                    expected_label = f"Vulkan{device_id}"
                    if device_arg != expected_label:
                        return [ValidationResult(
                            severity=Severity.WARNING,
                            code=ValidationResultCode.GPU_DEVICE_CONFLICT,
                            message=f"gpu.device_id={device_id} but args contain --device {device_arg}",
                            suggested_fix="sync_gpu_from_args",
                            field="gpu.device_id",
                        )]
                except (ValueError, TypeError):
                    pass

        return []

    def _check_parallel_conflict(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check for conflicts between server.parallel and --parallel arg."""
        server_config = config.get("server", {})
        server_parallel = server_config.get("parallel")
        args = config.get("args", [])

        if server_parallel is not None and args:
            from llama_orchestrator.runtime_args import parse_args_list
            parsed = parse_args_list(args)
            parallel_arg = parsed.get("--parallel")

            if parallel_arg is not None:
                if str(server_parallel) != parallel_arg:
                    return [ValidationResult(
                        severity=Severity.WARNING,
                        code=ValidationResultCode.PARALLEL_CONFLICT,
                        message=f"server.parallel={server_parallel} but args contain --parallel {parallel_arg}",
                        suggested_fix="sync_parallel_from_server",
                        field="server.parallel",
                    )]

        return []

    def _check_duplicate_args(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check for duplicate arguments in args list."""
        args = config.get("args", [])
        if args:
            from llama_orchestrator.runtime_args import find_duplicates
            duplicates = find_duplicates(args)
            if duplicates:
                flag_name, first_idx, second_idx = duplicates[0]
                return [ValidationResult(
                    severity=Severity.WARNING,
                    code=ValidationResultCode.DUPLICATE_ARGS,
                    message=f"Duplicate argument {flag_name} at indices {first_idx} and {second_idx}",
                    suggested_fix="remove_duplicate",
                    field="args",
                )]
        return []

    def _check_backend_available(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check if the selected backend is available."""
        gpu_config = config.get("gpu", {})
        backend = gpu_config.get("backend")

        if backend and backend != "auto":
            # Check if backend is supported
            supported_backends = {"vulkan", "cuda", "rocm", "cpu"}
            if backend not in supported_backends:
                return [ValidationResult(
                    severity=Severity.WARNING,
                    code=ValidationResultCode.BACKEND_UNAVAILABLE,
                    message=f"Backend '{backend}' is not available",
                    suggested_fix="change_backend",
                    field="gpu.backend",
                )]

        return []

    def _check_args_conflicts(self, config: dict[str, Any]) -> list[ValidationResult]:
        """Check for conflicts between structured config and args."""
        args = config.get("args", [])
        if args:
            from llama_orchestrator.runtime_args import find_conflicts
            conflicts = find_conflicts(config, args)
            for conflict in conflicts:
                return [ValidationResult(
                    severity=Severity.WARNING,
                    code=ValidationResultCode.ARGS_CONFLICT,
                    message=conflict["message"],
                    suggested_fix=conflict["suggested_fix"],
                    field=conflict.get("code", "args"),
                )]
        return []

    def validate_port_availability(self, port: int) -> bool:
        """Check if a port is available.

        Args:
            port: The port number to check.

        Returns:
            True if the port is available, False otherwise.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
            return True
        except OSError:
            return False

    def validate_gpu_binding(self, binding: dict[str, Any]) -> bool:
        """Validate that a GPU binding can be resolved.

        Args:
            binding: The GPU binding dict.

        Returns:
            True if the binding can be resolved, False otherwise.
        """
        if binding.get("type") == "alias":
            alias_value = binding.get("value")
            return alias_value in self.hardware_aliases
        return True

    def resolve_gpu_binding(self, binding: dict[str, Any]) -> str | None:
        """Resolve a GPU binding to a runtime device ID.

        Args:
            binding: The GPU binding dict.

        Returns:
            The resolved device ID, or None if resolution fails.
        """
        if binding.get("type") == "alias":
            alias_value = binding.get("value")
            return self.hardware_aliases.get(alias_value)
        elif binding.get("type") == "runtime_id":
            return binding.get("value")
        return None
