"""
Tests for configuration validator.
"""

import contextlib
import os
import socket
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
    validate_gpu_config,
    validate_host_exposure,
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
    host: str = "127.0.0.1",
) -> InstanceConfig:
    """Helper to create a test config."""
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path(model_path)),
        server=ServerConfig(host=host, port=port),
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
        """Test validation errors when port is in use (R10/F17)."""
        mock_get_ports.return_value = {8001, 8002, 8003}

        config = make_config(port=8001)
        result = validate_port_available(config)

        assert not result.is_valid
        assert result.error_count == 1
        assert "in use" in result.issues[0].message.lower()

    def test_occupied_port_fixture_socket_is_error(self) -> None:
        """R10 AC: a port bound by a real fixture socket makes validate_instance invalid."""
        import socket as _socket

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            config = make_config(port=port)

            result = validate_instance(config, check_runtime=True)

            assert not result.is_valid
            port_issues = [i for i in result.issues if i.field == "server.port"]
            assert len(port_issues) == 1
            assert port_issues[0].severity == "error"
            assert str(port) in port_issues[0].message

        # After the fixture socket is closed, no server.port issue remains.
        result_free = validate_instance(config, check_runtime=True)
        assert not [i for i in result_free.issues if i.field == "server.port"]


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
    """Tests for log directory validation (R19: side-effect-free)."""

    def test_missing_log_dir_passes_without_creating(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A not-yet-created log dir passes if the ancestor is writable — and nothing is created."""
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )

        result = validate_log_directory(make_config())

        assert result.is_valid
        # R19 acceptance: the validated path is untouched — zero new files or dirs.
        assert list(tmp_path.rglob("*")) == []

    def test_existing_writable_dir_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An existing writable log directory passes."""
        (tmp_path / "logs" / "test").mkdir(parents=True)
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )

        result = validate_log_directory(make_config())

        assert result.is_valid

    def test_non_writable_dir_reports_error_no_probe_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-writable log directory fails with an error and no probe file is written."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )
        monkeypatch.setattr("llama_orchestrator.config.validator.os.access", lambda _p, _m: False)

        result = validate_log_directory(make_config())

        assert not result.is_valid
        assert result.error_count == 2  # stdout + stderr share the non-writable dir
        # R19 acceptance: no .write_test probe file left behind (or ever created).
        assert not (logs_dir / ".write_test").exists()

    def test_lint_read_only_checkout_creates_zero_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """R19 acceptance: a lint run on a read-only checkout creates zero new files under the project."""
        (tmp_path / "instances" / "00000021_ro-uid").mkdir(parents=True)
        model_file = tmp_path / "models" / "test.gguf"
        model_file.parent.mkdir()
        model_file.write_bytes(b"fake model")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        config = make_config(model_path=str(model_file), port=port)
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )

        before = {str(p) for p in tmp_path.rglob("*")}
        # Simulate a read-only checkout (best effort; the rglob diff is authoritative).
        for p in sorted(tmp_path.rglob("*"), reverse=True):
            with contextlib.suppress(OSError):
                os.chmod(p, 0o444 if p.is_file() else 0o555)
        os.chmod(tmp_path, 0o555)

        result = lint_config(config)

        # Restore permissions so pytest's tmp teardown works.
        for p in sorted(tmp_path.rglob("*"), reverse=True):
            with contextlib.suppress(OSError):
                os.chmod(p, 0o755 if p.is_dir() else 0o644)
        os.chmod(tmp_path, 0o755)

        after = {str(p) for p in tmp_path.rglob("*")}
        # R19 acceptance: zero new files or directories under the project.
        assert after - before == set(), f"lint created files: {sorted(after - before)}"
        # Pass/fail parity with the old side-effecting probe: on a truly
        # read-only checkout the old code's mkdir would have raised PermissionError
        # for both log paths, so lint must fail identically (2 errors) — without
        # creating anything.
        assert not result.is_valid
        assert result.error_count == 2
        assert all(i.field == "logs" and i.severity == "error" for i in result.issues)


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

    def test_cpu_layers_single_issue_r20(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """R20: exactly one issue for cpu+layers>0; the rule lives only in config/validator.py."""
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")
        monkeypatch.setattr(
            "llama_orchestrator.config.validator.get_project_root",
            lambda: tmp_path
        )

        # Schema-level construction stays permissive (no duplicated rule in GpuConfig).
        assert GpuConfig(backend="cpu", layers=30).layers == 30

        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=model_file),
            gpu=GpuConfig(backend="cpu", layers=30),
        )

        result = validate_instance(config, check_runtime=False)

        gpu_issues = [i for i in result.issues if i.field == "gpu.layers"]
        assert len(gpu_issues) == 1
        assert gpu_issues[0].severity == "warning"


class TestValidateInstance:
    """Tests for full instance validation."""

    def test_valid_instance(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test validation of a complete valid instance."""
        # Create model file
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")

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


class TestValidateHostExposure:
    """Tests for host exposure warning behavior."""

    def test_wide_bind_warning(self) -> None:
        """Test warning is emitted when binding to all interfaces."""
        config = make_config(host="0.0.0.0")

        result = validate_host_exposure(config)

        assert result.warning_count == 1
        assert "all network interfaces" in result.issues[0].message.lower()

    def test_localhost_has_no_warning(self) -> None:
        """Test localhost binding stays warning-free."""
        config = make_config(host="127.0.0.1")

        result = validate_host_exposure(config)

        assert result.warning_count == 0


class TestLintConfig:
    """Tests for lint_config function."""

    def test_lint_high_context_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test lint warns about high context size."""
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"fake model")

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
