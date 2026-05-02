"""
Configuration schemas for llama-orchestrator.

Uses Pydantic v2 for validation and serialization.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class BinaryConfig(BaseModel):
    """
    Binary configuration for llama.cpp server executable.
    
    The binary_id (UUID) is the PRIMARY identifier that joins to
    bins/registry.json. Version and variant are optional hints
    for resolution when binary_id is not set.
    
    This enables:
    - Unambiguous binary identification via UUID
    - Database-like joins between config.json and registry.json
    - Multiple installations of the same version+variant
    """
    
    binary_id: Optional[UUID] = Field(
        default=None,
        description="Primary identifier - UUID of installed binary. Joins to registry.json"
    )
    version: Optional[str] = Field(
        default=None,
        description="llama.cpp version tag (e.g., 'b7572', 'latest'). Used when binary_id is None"
    )
    variant: Literal[
        "win-cpu-x64",
        "win-cpu-arm64",
        "win-vulkan-x64",
        "win-cuda-12.4-x64",
        "win-cuda-13.1-x64",
        "win-hip-radeon-x64",
        "win-sycl-x64",
    ] = Field(
        default="win-vulkan-x64",
        description="Platform/GPU variant. Used when binary_id is None"
    )
    source_url: Optional[HttpUrl] = Field(
        default=None,
        description="Custom download URL (overrides auto-generated URL)"
    )
    sha256: Optional[str] = Field(
        default=None,
        description="Expected SHA256 checksum for verification"
    )
    
    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: Optional[str]) -> Optional[str]:
        """Validate SHA256 format if provided."""
        if v is not None:
            v = v.lower().strip()
            if len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
                raise ValueError("SHA256 must be 64 hex characters")
        return v


class ModelConfig(BaseModel):
    """Configuration for the LLM model."""
    
    path: Path = Field(..., description="Path to the GGUF model file")
    context_size: int = Field(default=4096, ge=512, le=131072, description="Context window size")
    batch_size: int = Field(default=512, ge=1, le=8192, description="Batch size for processing")
    threads: int = Field(default=8, ge=1, le=256, description="Number of CPU threads")
    
    @field_validator("path")
    @classmethod
    def validate_path_extension(cls, v: Path) -> Path:
        """Ensure model file has .gguf extension."""
        if v.suffix.lower() != ".gguf":
            raise ValueError(f"Model file must have .gguf extension, got: {v.suffix}")
        return v


class ServerConfig(BaseModel):
    """Configuration for the llama.cpp server."""
    
    host: str = Field(default="127.0.0.1", description="Server bind address")
    port: int = Field(default=8001, ge=1024, le=65535, description="Server port")
    timeout: int = Field(default=600, ge=0, description="Request timeout in seconds")
    parallel: int = Field(default=1, ge=1, le=64, description="Parallel request slots")
    
    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate host is a valid IP or hostname."""
        # Allow localhost variants and IP addresses
        if v in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return v
        # Basic IP pattern check
        ip_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
        if re.match(ip_pattern, v):
            return v
        # Allow hostnames
        hostname_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$"
        if re.match(hostname_pattern, v):
            return v
        raise ValueError(f"Invalid host: {v}")


class GpuConfig(BaseModel):
    """Configuration for GPU acceleration."""
    
    backend: Literal["cpu", "vulkan", "cuda", "metal", "hip"] = Field(
        default="cpu", 
        description="GPU backend to use"
    )
    device_id: int = Field(default=0, ge=0, description="GPU device index")
    layers: int = Field(default=0, ge=0, description="Number of layers to offload to GPU")
    
    @model_validator(mode="after")
    def validate_gpu_config(self) -> "GpuConfig":
        """Validate GPU configuration consistency."""
        if self.backend == "cpu" and self.layers > 0:
            # Allow layers > 0 with CPU, just warn (handled at runtime)
            pass
        return self


class HealthcheckConfig(BaseModel):
    """Configuration for health monitoring with pluggable probe support."""
    
    # Probe type configuration (V2)
    type: Literal["http", "tcp", "custom"] = Field(
        default="http",
        description="Type of health probe: http, tcp, or custom"
    )
    path: str = Field(default="/health", description="Health check endpoint path (for HTTP probe)")
    expected_status: list[int] = Field(
        default_factory=lambda: [200],
        description="Expected HTTP status codes (for HTTP probe)"
    )
    expected_body: Optional[str] = Field(
        default=None,
        description="Expected substring in response body (for HTTP probe)"
    )
    custom_script: Optional[str] = Field(
        default=None,
        description="Custom script to execute (for custom probe). Use {host} and {port} placeholders"
    )
    
    # Timing configuration
    interval: int = Field(default=10, ge=1, le=3600, description="Check interval in seconds")
    timeout: int = Field(default=5, ge=1, le=60, description="Request timeout in seconds")
    retries: int = Field(default=3, ge=1, le=10, description="Retries before marking unhealthy")
    retry_delay: float = Field(default=1.0, ge=0.1, le=60.0, description="Delay between retries in seconds")
    start_period: int = Field(default=60, ge=0, le=600, description="Grace period after start")
    
    # Backoff with jitter configuration (V2)
    backoff_enabled: bool = Field(default=True, description="Enable exponential backoff on failures")
    backoff_base: float = Field(default=1.0, ge=0.1, le=60.0, description="Base delay for backoff in seconds")
    backoff_max: float = Field(default=60.0, ge=1.0, le=600.0, description="Maximum backoff delay in seconds")
    backoff_jitter: float = Field(default=0.1, ge=0.0, le=1.0, description="Jitter factor (0-1) to add randomness")
    
    @model_validator(mode="after")
    def validate_healthcheck_config(self) -> "HealthcheckConfig":
        """Validate healthcheck configuration consistency."""
        if self.type == "custom" and not self.custom_script:
            raise ValueError("custom_script is required when type is 'custom'")
        return self
    
    def to_probe_dict(self) -> dict:
        """Convert to probe configuration dictionary."""
        return {
            "type": self.type,
            "path": self.path,
            "expected_status": self.expected_status,
            "expected_body": self.expected_body,
            "custom_script": self.custom_script,
            "timeout": float(self.timeout),
            "retries": self.retries,
            "retry_delay": self.retry_delay,
        }


class RestartPolicy(BaseModel):
    """Configuration for automatic restart behavior."""
    
    enabled: bool = Field(default=True, description="Enable auto-restart")
    max_retries: int = Field(default=5, ge=0, le=100, description="Maximum restart attempts")
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0, description="Exponential backoff multiplier")
    initial_delay: float = Field(default=1.0, ge=0.1, le=60.0, description="Initial delay in seconds")
    max_delay: float = Field(default=300.0, ge=1.0, le=3600.0, description="Maximum delay in seconds")


class LogsConfig(BaseModel):
    """Configuration for logging."""
    
    stdout: str = Field(default="logs/{name}/stdout.log", description="Stdout log path")
    stderr: str = Field(default="logs/{name}/stderr.log", description="Stderr log path")
    max_size_mb: int = Field(default=100, ge=1, le=10000, description="Max log file size in MB")
    rotation: int = Field(default=5, ge=1, le=100, description="Number of rotated files to keep")


class InstanceConfig(BaseModel):
    """
    Complete configuration for a llama.cpp server instance.
    
    This is the root schema that combines all sub-configurations.
    
    The `binary` field is optional for backward compatibility.
    If not set, the system falls back to the legacy bin/ directory.
    """
    
    name: str = Field(..., min_length=1, max_length=64, description="Unique instance name")
    binary: Optional[BinaryConfig] = Field(
        default=None,
        description="Binary version configuration. UUID joins to bins/registry.json"
    )
    model: ModelConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    args: list[str] = Field(default_factory=list, description="Additional CLI arguments")
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)
    restart_policy: RestartPolicy = Field(default_factory=RestartPolicy)
    logs: LogsConfig = Field(default_factory=LogsConfig)
    
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate instance name format."""
        pattern = r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Name must start/end with alphanumeric, contain only lowercase letters, "
                f"numbers, hyphens, and underscores. Got: {v}"
            )
        return v
    
    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables including GPU settings."""
        env = dict(self.env)
        
        # Add Vulkan device if applicable
        if self.gpu.backend == "vulkan":
            env["GGML_VULKAN_DEVICE"] = str(self.gpu.device_id)
        
        return env
    
    def get_log_paths(self) -> tuple[Path, Path]:
        """Get resolved log file paths."""
        stdout_path = Path(self.logs.stdout.format(name=self.name))
        stderr_path = Path(self.logs.stderr.format(name=self.name))
        return stdout_path, stderr_path


# =============================================================================
# Example config for testing/documentation
# =============================================================================

EXAMPLE_CONFIG = InstanceConfig(
    name="gpt-oss",
    model=ModelConfig(
        path=Path("models/gpt-oss-20b-Q4_K_S.gguf"),
        context_size=4096,
        batch_size=512,
        threads=16,
    ),
    server=ServerConfig(
        host="127.0.0.1",
        port=8001,
        parallel=4,
    ),
    gpu=GpuConfig(
        backend="vulkan",
        device_id=1,
        layers=30,
    ),
    env={
        "GGML_VULKAN_DEVICE": "1",
    },
    healthcheck=HealthcheckConfig(
        interval=10,
        timeout=5,
        retries=3,
    ),
    restart_policy=RestartPolicy(
        enabled=True,
        max_retries=5,
    ),
)
