"""
Tests for configuration schema validation.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from llama_orchestrator.config import (
    EXAMPLE_CONFIG,
    GpuConfig,
    HealthcheckConfig,
    InstanceConfig,
    ModelConfig,
    RestartPolicy,
    ServerConfig,
)


class TestModelConfig:
    """Tests for ModelConfig schema."""
    
    def test_valid_config(self) -> None:
        """Test valid model configuration."""
        config = ModelConfig(
            path=Path("models/test.gguf"),
            context_size=4096,
            batch_size=512,
            threads=8,
        )
        assert config.path == Path("models/test.gguf")
        assert config.context_size == 4096
    
    def test_invalid_extension(self) -> None:
        """Test that non-GGUF files are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ModelConfig(path=Path("models/test.bin"))
        assert "gguf extension" in str(exc_info.value).lower()
    
    def test_default_values(self) -> None:
        """Test default values are applied."""
        config = ModelConfig(path=Path("test.gguf"))
        assert config.context_size == 4096
        assert config.batch_size == 512
        assert config.threads == 8
    
    def test_invalid_context_size(self) -> None:
        """Test context size validation."""
        with pytest.raises(ValidationError):
            ModelConfig(path=Path("test.gguf"), context_size=100)  # Too small


class TestServerConfig:
    """Tests for ServerConfig schema."""
    
    def test_valid_config(self) -> None:
        """Test valid server configuration."""
        config = ServerConfig(
            host="127.0.0.1",
            port=8001,
            timeout=600,
            parallel=4,
        )
        assert config.host == "127.0.0.1"
        assert config.port == 8001
    
    def test_localhost_variants(self) -> None:
        """Test various localhost representations."""
        for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            config = ServerConfig(host=host)
            assert config.host == host
    
    def test_invalid_port_low(self) -> None:
        """Test that ports below 1024 are rejected."""
        with pytest.raises(ValidationError):
            ServerConfig(port=80)
    
    def test_invalid_port_high(self) -> None:
        """Test that ports above 65535 are rejected."""
        with pytest.raises(ValidationError):
            ServerConfig(port=70000)
    
    def test_default_values(self) -> None:
        """Test default values are applied."""
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8001
        assert config.parallel == 1


class TestGpuConfig:
    """Tests for GpuConfig schema."""
    
    def test_valid_vulkan(self) -> None:
        """Test valid Vulkan configuration."""
        config = GpuConfig(
            backend="vulkan",
            device_id=1,
            layers=30,
        )
        assert config.backend == "vulkan"
        assert config.device_id == 1
    
    def test_valid_backends(self) -> None:
        """Test all valid backend options."""
        for backend in ("cpu", "vulkan", "cuda", "metal", "hip"):
            config = GpuConfig(backend=backend)
            assert config.backend == backend
    
    def test_invalid_backend(self) -> None:
        """Test invalid backend is rejected."""
        with pytest.raises(ValidationError):
            GpuConfig(backend="invalid")
    
    def test_default_values(self) -> None:
        """Test default values are applied."""
        config = GpuConfig()
        assert config.backend == "cpu"
        assert config.device_id == 0
        assert config.layers == 0


class TestHealthcheckConfig:
    """Tests for HealthcheckConfig schema."""
    
    def test_valid_config(self) -> None:
        """Test valid healthcheck configuration."""
        config = HealthcheckConfig(
            path="/health",
            interval=10,
            timeout=5,
            retries=3,
        )
        assert config.path == "/health"
        assert config.interval == 10
    
    def test_default_values(self) -> None:
        """Test default values are applied."""
        config = HealthcheckConfig()
        assert config.path == "/health"
        assert config.interval == 10
        assert config.retries == 3


class TestRestartPolicy:
    """Tests for RestartPolicy schema."""
    
    def test_valid_config(self) -> None:
        """Test valid restart policy configuration."""
        config = RestartPolicy(
            enabled=True,
            max_retries=5,
            backoff_multiplier=2.0,
        )
        assert config.enabled is True
        assert config.max_retries == 5
    
    def test_backoff_bounds(self) -> None:
        """Test backoff multiplier bounds."""
        with pytest.raises(ValidationError):
            RestartPolicy(backoff_multiplier=0.5)  # Too low
        
        with pytest.raises(ValidationError):
            RestartPolicy(backoff_multiplier=15.0)  # Too high


class TestInstanceConfig:
    """Tests for InstanceConfig schema."""
    
    def test_valid_config(self) -> None:
        """Test valid instance configuration."""
        config = InstanceConfig(
            name="test-instance",
            model=ModelConfig(path=Path("test.gguf")),
        )
        assert config.name == "test-instance"
    
    def test_example_config_valid(self) -> None:
        """Test that EXAMPLE_CONFIG is valid."""
        assert EXAMPLE_CONFIG.name == "gpt-oss"
        assert EXAMPLE_CONFIG.model.path == Path("models/gpt-oss-20b-Q4_K_S.gguf")
    
    def test_invalid_name_start(self) -> None:
        """Test that names starting with special chars are rejected."""
        with pytest.raises(ValidationError):
            InstanceConfig(
                name="-invalid",
                model=ModelConfig(path=Path("test.gguf")),
            )
    
    def test_invalid_name_uppercase(self) -> None:
        """Test that uppercase names are rejected."""
        with pytest.raises(ValidationError):
            InstanceConfig(
                name="Invalid",
                model=ModelConfig(path=Path("test.gguf")),
            )
    
    def test_valid_name_patterns(self) -> None:
        """Test various valid name patterns."""
        valid_names = ["a", "ab", "test", "test-1", "test_1", "my-model-v2"]
        for name in valid_names:
            config = InstanceConfig(
                name=name,
                model=ModelConfig(path=Path("test.gguf")),
            )
            assert config.name == name
    
    def test_get_env_vars_vulkan(self) -> None:
        """Test environment variable generation for Vulkan."""
        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=Path("test.gguf")),
            gpu=GpuConfig(backend="vulkan", device_id=1),
        )
        env = config.get_env_vars()
        assert env["GGML_VULKAN_DEVICE"] == "1"
    
    def test_get_env_vars_cpu(self) -> None:
        """Test environment variable generation for CPU."""
        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=Path("test.gguf")),
            gpu=GpuConfig(backend="cpu"),
        )
        env = config.get_env_vars()
        assert "GGML_VULKAN_DEVICE" not in env
    
    def test_get_log_paths(self) -> None:
        """Test log path resolution."""
        config = InstanceConfig(
            name="mymodel",
            model=ModelConfig(path=Path("test.gguf")),
        )
        stdout, stderr = config.get_log_paths()
        assert "mymodel" in str(stdout)
        assert "mymodel" in str(stderr)
