"""
Tests for configuration validator.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from llama_orchestrator.config import (
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
    ValidationResult,
)
from llama_orchestrator.config.validator import (
    lint_config,
    resolve_model_path,
    validate_gpu_config,
    validate_instance,
    validate_log_directory,
    validate_model_exists,
    validate_port_available,
    validate_port_collisions,
)


def make_config(
    name: str = "test",
    model_path: str = "test.gguf",
    port: int = 8001,
    backend: str = "cpu",
) -> InstanceConfig:
    """Helper to create a test config."""
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path(model_path)),
        server=ServerConfig(port=port),
        gpu=GpuConfig(backend=backend),  # type: ignore
    )


class TestValidateModelExists:
    """Tests for model file validation."""
    
    def test_model_exists(self, tmp_path: Path) -> None:
        """Test validation passes when model exists."""
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model content")
        
        config = make_config(model_path=str(model_file))
        result = validate_model_exists(config)
        
        assert result.is_valid
        assert result.error_count == 0
    
    def test_model_not_found(self, tmp_path: Path) -> None:
        """Test validation fails when model is missing."""
        config = make_config(model_path=str(tmp_path / "nonexistent.gguf"))
        result = validate_model_exists(config)
        
        assert not result.is_valid
        assert result.error_count == 1
        assert "not found" in result.issues[0].message.lower()
    
    def test_model_is_directory(self, tmp_path: Path) -> None:
        """Test validation fails when path is a directory."""
        model_dir = tmp_path / "test.gguf"
        model_dir.mkdir()
        
        config = make_config(model_path=str(model_dir))
        result = validate_model_exists(config)
        
        assert not result.is_valid
        assert "not a file" in result.issues[0].message.lower()
    
    def test_model_empty_file(self, tmp_path: Path) -> None:
        """Test validation fails when model file is empty."""
        model_file = tmp_path / "test.gguf"
        model_file.touch()  # Empty file
        
        config = make_config(model_path=str(model_file))
        result = validate_model_exists(config)
        
        assert not result.is_valid
        assert "empty" in result.issues[0].message.lower()


class TestValidatePortAvailable:
    """Tests for port availability validation."""
    
    def test_port_available(self) -> None:
        """Test validation passes for available port."""
        # Use a high port number unlikely to be in use
        config = make_config(port=59999)
        result = validate_port_available(config)
        
        # This should pass (or warn if by chance it's used)
        # We can't guarantee it's not in use, so just check no errors
        assert result.error_count == 0
    
    @patch("llama_orchestrator.config.validator.get_used_ports")
    def test_port_in_use(self, mock_get_ports: any) -> None:
        """Test validation warns when port is in use."""
        mock_get_ports.return_value = {8001, 8002, 8003}
        
        config = make_config(port=8001)
        result = validate_port_available(config)
        
        assert result.warning_count == 1
        assert "in use" in result.issues[0].message.lower()


class TestValidatePortCollisions:
    """Tests for port collision detection across instances."""
    
    def test_no_collision(self) -> None:
        """Test validation passes when ports are unique."""
        configs = {
            "instance-a": make_config(name="instance-a", port=8001),
            "instance-b": make_config(name="instance-b", port=8002),
        }
        
        result = validate_port_collisions(configs)
        
        assert result.is_valid
        assert result.error_count == 0
    
    def test_collision_detected(self) -> None:
        """Test validation fails when ports collide."""
        configs = {
            "instance-a": make_config(name="instance-a", port=8001),
            "instance-b": make_config(name="instance-b", port=8001),  # Same port!
        }
        
        result = validate_port_collisions(configs)
        
        assert not result.is_valid
        assert result.error_count == 2  # One error per instance
        assert "multiple instances" in result.issues[0].message.lower()


class TestValidateLogDirectory:
    """Tests for log directory validation."""
    
    def test_log_dir_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that log directory is created if it doesn't exist."""
        logs_dir = tmp_path / "logs"
        
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_logs_dir",
            lambda: logs_dir
        )
        
        config = make_config()
        result = validate_log_directory(config)
        
        # Should pass (directory created)
        assert result.is_valid


class TestValidateGpuConfig:
    """Tests for GPU configuration validation."""
    
    def test_cpu_with_layers_warning(self) -> None:
        """Test warning when CPU backend has GPU layers."""
        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=Path("test.gguf")),
            gpu=GpuConfig(backend="cpu", layers=30),
        )
        
        result = validate_gpu_config(config)
        
        assert result.warning_count == 1
        assert "layers" in result.issues[0].message.lower()
    
    def test_vulkan_no_warning(self) -> None:
        """Test no warning for valid Vulkan config."""
        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=Path("test.gguf")),
            gpu=GpuConfig(backend="vulkan", device_id=1, layers=30),
        )
        
        result = validate_gpu_config(config)
        
        # Should have no warnings (maybe info about env var)
        assert result.warning_count == 0


class TestValidateInstance:
    """Tests for full instance validation."""
    
    def test_valid_instance(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test validation of a complete valid instance."""
        # Create model file
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")
        
        # Create logs dir
        logs_dir = tmp_path / "logs"
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_logs_dir",
            lambda: logs_dir
        )
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )
        
        config = make_config(model_path=str(model_file))
        result = validate_instance(config, check_runtime=False)
        
        assert result.is_valid
    
    def test_invalid_instance(self) -> None:
        """Test validation catches errors."""
        config = make_config(model_path="/nonexistent/model.gguf")
        result = validate_instance(config, check_runtime=False)
        
        assert not result.is_valid
        assert result.error_count >= 1


class TestLintConfig:
    """Tests for lint_config function."""
    
    def test_lint_high_context_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test lint warns about high context size."""
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")
        
        logs_dir = tmp_path / "logs"
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_logs_dir",
            lambda: logs_dir
        )
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )
        
        config = InstanceConfig(
            name="test",
            model=ModelConfig(
                path=model_file,
                context_size=65536,  # Very high
            ),
        )
        
        result = lint_config(config)
        
        # Should have a warning about large context
        context_warnings = [
            i for i in result.issues 
            if "context" in i.field.lower() and i.severity == "warning"
        ]
        assert len(context_warnings) >= 1
    
    def test_lint_high_threads_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test lint warns when threads exceed CPU count."""
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")
        
        logs_dir = tmp_path / "logs"
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_logs_dir",
            lambda: logs_dir
        )
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )
        
        cpu_count = os.cpu_count() or 8
        
        config = InstanceConfig(
            name="test",
            model=ModelConfig(
                path=model_file,
                threads=cpu_count + 10,  # More than CPU count
            ),
        )
        
        result = lint_config(config)
        
        # Should have a warning about thread count
        thread_warnings = [
            i for i in result.issues 
            if "threads" in i.field.lower() and i.severity == "warning"
        ]
        assert len(thread_warnings) >= 1


class TestValidationResult:
    """Tests for ValidationResult dataclass."""
    
    def test_empty_result_is_valid(self) -> None:
        """Test that empty result is valid."""
        result = ValidationResult()
        assert result.is_valid
        assert result.error_count == 0
        assert result.warning_count == 0
    
    def test_merge_results(self) -> None:
        """Test merging two results."""
        result1 = ValidationResult()
        result1.add(
            __import__("llama_orchestrator.config.validator", fromlist=["ValidationIssue"]).ValidationIssue(
                instance="test",
                field="model",
                severity="error",
                message="Test error",
            )
        )
        
        result2 = ValidationResult()
        result2.add(
            __import__("llama_orchestrator.config.validator", fromlist=["ValidationIssue"]).ValidationIssue(
                instance="test",
                field="server",
                severity="warning",
                message="Test warning",
            )
        )
        
        result1.merge(result2)
        
        assert len(result1.issues) == 2
        assert result1.error_count == 1
        assert result1.warning_count == 1
