"""
Tests for llama_orchestrator.engine module.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llama_orchestrator.config.schema import (
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
)
from llama_orchestrator.engine.command import build_env
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
)


# Fixtures
@pytest.fixture
def sample_config() -> InstanceConfig:
    """Create a sample instance configuration."""
    return InstanceConfig(
        name="test-instance",
        model=ModelConfig(
            path=Path("models/test-model.gguf"),
            context_size=4096,
            batch_size=512,
        ),
        server=ServerConfig(
            port=8001,
            host="127.0.0.1",
        ),
        gpu=GpuConfig(
            backend="vulkan",
            device_id=0,
            layers=32,
        ),
    )


@pytest.fixture
def cpu_config() -> InstanceConfig:
    """Create a CPU-only instance configuration."""
    return InstanceConfig(
        name="cpu-instance",
        model=ModelConfig(
            path=Path("models/test-model.gguf"),
            context_size=2048,
        ),
        server=ServerConfig(
            port=8002,
        ),
        gpu=GpuConfig(
            backend="cpu",
            layers=0,
        ),
    )


@pytest.fixture
def cuda_config() -> InstanceConfig:
    """Create a CUDA instance configuration."""
    return InstanceConfig(
        name="cuda-instance",
        model=ModelConfig(
            path=Path("models/test-model.gguf"),
        ),
        gpu=GpuConfig(
            backend="cuda",
            device_id=1,
            layers=64,
        ),
    )


# Environment Variable Tests
class TestBuildEnv:
    """Tests for build_env function."""

    def test_vulkan_env_vars(self, sample_config):
        """Test Vulkan environment variables are set."""
        env = build_env(sample_config)
        
        assert "GGML_VULKAN_DEVICE" in env
        assert env["GGML_VULKAN_DEVICE"] == "0"

    def test_cuda_env_vars(self, cuda_config):
        """Test CUDA environment variables are set."""
        env = build_env(cuda_config)
        
        assert "CUDA_VISIBLE_DEVICES" in env
        assert env["CUDA_VISIBLE_DEVICES"] == "1"

    def test_cpu_no_gpu_env(self, cpu_config):
        """Test CPU backend has no GPU-specific env vars."""
        env = build_env(cpu_config)
        
        assert "GGML_VULKAN_DEVICE" not in env
        assert "CUDA_VISIBLE_DEVICES" not in env


# State Management Tests
class TestInstanceState:
    """Tests for InstanceState dataclass."""

    def test_create_initial_state(self):
        """Test creating an initial instance state."""
        state = InstanceState(
            name="test",
            pid=1234,
            status=InstanceStatus.RUNNING,
            health=HealthStatus.UNKNOWN,
        )
        
        assert state.name == "test"
        assert state.pid == 1234
        assert state.status == InstanceStatus.RUNNING
        assert state.health == HealthStatus.UNKNOWN
        assert state.restart_count == 0

    def test_status_symbol(self):
        """Test status symbol property."""
        running = InstanceState(
            name="test",
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
        )
        assert running.status_symbol == "●"
        
        stopped = InstanceState(
            name="test",
            status=InstanceStatus.STOPPED,
            health=HealthStatus.UNKNOWN,
        )
        assert stopped.status_symbol == "○"

    def test_health_symbol(self):
        """Test health symbol property."""
        healthy = InstanceState(
            name="test",
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
        )
        # Healthy uses filled circle
        assert healthy.health_symbol == "●"
        
        unhealthy = InstanceState(
            name="test",
            status=InstanceStatus.ERROR,
            health=HealthStatus.ERROR,
        )
        assert unhealthy.health_symbol == "✗"

    def test_uptime_str_no_start_time(self):
        """Test uptime string when not started."""
        state = InstanceState(
            name="test",
            status=InstanceStatus.STOPPED,
        )
        assert state.uptime_str == "-"

    def test_uptime_seconds(self):
        """Test uptime calculation with start_time."""
        import time
        state = InstanceState(
            name="test",
            status=InstanceStatus.RUNNING,
            start_time=time.time() - 45,  # 45 seconds ago
        )
        assert state.uptime is not None
        assert 44 <= state.uptime <= 46  # Allow small drift

    def test_uptime_str_format_seconds(self):
        """Test uptime string format for seconds."""
        import time
        state = InstanceState(
            name="test",
            status=InstanceStatus.RUNNING,
            start_time=time.time() - 30,
        )
        assert "s" in state.uptime_str
        assert "30" in state.uptime_str or "29" in state.uptime_str or "31" in state.uptime_str

    def test_uptime_str_format_minutes(self):
        """Test uptime string format for minutes."""
        import time
        state = InstanceState(
            name="test",
            status=InstanceStatus.RUNNING,
            start_time=time.time() - 125,  # 2m 5s
        )
        assert "m" in state.uptime_str


class TestInstanceStatus:
    """Tests for InstanceStatus enum."""

    def test_all_statuses_exist(self):
        """Test all expected statuses are defined."""
        assert InstanceStatus.STOPPED
        assert InstanceStatus.STARTING
        assert InstanceStatus.RUNNING
        assert InstanceStatus.STOPPING
        assert InstanceStatus.ERROR

    def test_status_values(self):
        """Test status enum values."""
        assert InstanceStatus.STOPPED.value == "stopped"
        assert InstanceStatus.RUNNING.value == "running"
        assert InstanceStatus.ERROR.value == "error"


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_all_health_statuses_exist(self):
        """Test all expected health statuses are defined."""
        assert HealthStatus.UNKNOWN
        assert HealthStatus.HEALTHY
        assert HealthStatus.UNHEALTHY
        assert HealthStatus.LOADING
        assert HealthStatus.ERROR

    def test_health_values(self):
        """Test health status enum values."""
        assert HealthStatus.UNKNOWN.value == "unknown"
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.ERROR.value == "error"

