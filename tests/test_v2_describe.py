"""
Tests for describe command utilities.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from llama_orchestrator.cli_describe import (
    InstanceDescription,
    build_description,
    format_description_rich,
)


class TestInstanceDescription:
    """Tests for InstanceDescription dataclass."""
    
    def test_default_values(self):
        """Test default values."""
        desc = InstanceDescription(name="test")
        
        assert desc.name == "test"
        assert desc.status == "unknown"
        assert desc.health == "unknown"
        assert desc.pid is None
        assert desc.restart_count == 0
        assert desc.recent_events == []
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        desc = InstanceDescription(
            name="test",
            port=8080,
            host="localhost",
            status="running",
            health="healthy",
            pid=12345,
        )
        
        result = desc.to_dict()
        
        assert result["name"] == "test"
        assert result["configuration"]["port"] == 8080
        assert result["runtime"]["status"] == "running"
        assert result["runtime"]["pid"] == 12345
    
    def test_uptime_str_zero(self):
        """Test uptime string when zero."""
        desc = InstanceDescription(name="test", uptime_seconds=0)
        assert desc.uptime_str == "-"
    
    def test_uptime_str_seconds(self):
        """Test uptime string with seconds."""
        desc = InstanceDescription(name="test", uptime_seconds=45)
        assert "45s" in desc.uptime_str
    
    def test_uptime_str_minutes(self):
        """Test uptime string with minutes."""
        desc = InstanceDescription(name="test", uptime_seconds=125)
        assert "2m" in desc.uptime_str
    
    def test_uptime_str_hours(self):
        """Test uptime string with hours."""
        desc = InstanceDescription(name="test", uptime_seconds=3665)  # 1h 1m 5s
        assert "1h" in desc.uptime_str
    
    def test_uptime_str_days(self):
        """Test uptime string with days."""
        desc = InstanceDescription(name="test", uptime_seconds=90000)  # ~1d
        assert "1d" in desc.uptime_str
    
    def test_status_color_running(self):
        """Test status color for running."""
        desc = InstanceDescription(name="test", status="running")
        assert desc.status_color == "green"
    
    def test_status_color_stopped(self):
        """Test status color for stopped."""
        desc = InstanceDescription(name="test", status="stopped")
        assert desc.status_color == "dim"
    
    def test_status_color_crashed(self):
        """Test status color for crashed."""
        desc = InstanceDescription(name="test", status="crashed")
        assert desc.status_color == "red"
    
    def test_health_color_healthy(self):
        """Test health color for healthy."""
        desc = InstanceDescription(name="test", health="healthy")
        assert desc.health_color == "green"
    
    def test_health_color_unhealthy(self):
        """Test health color for unhealthy."""
        desc = InstanceDescription(name="test", health="unhealthy")
        assert desc.health_color == "red"
    
    def test_health_color_degraded(self):
        """Test health color for degraded."""
        desc = InstanceDescription(name="test", health="degraded")
        assert desc.health_color == "yellow"


class TestBuildDescription:
    """Tests for build_description function."""
    
    @patch("llama_orchestrator.engine.state.load_runtime")
    @patch("llama_orchestrator.engine.state.get_recent_events")
    def test_build_with_name_only(self, mock_events, mock_load_runtime):
        """Test building description with just name."""
        mock_load_runtime.return_value = None
        mock_events.return_value = []
        
        desc = build_description("test")
        
        assert desc.name == "test"
        assert desc.status == "unknown"
    
    @patch("llama_orchestrator.engine.state.load_runtime")
    @patch("llama_orchestrator.engine.state.get_recent_events")
    def test_build_with_config(self, mock_events, mock_load_runtime):
        """Test building description with config."""
        mock_load_runtime.return_value = None
        mock_events.return_value = []
        
        # Create mock config
        config = MagicMock()
        config.model.path = "/path/to/model.gguf"
        config.model.context_size = 4096
        config.model.batch_size = 512
        config.model.threads = 8
        config.server.port = 8080
        config.server.host = "localhost"
        config.gpu.backend = "vulkan"
        config.gpu.device_id = 0
        config.gpu.layers = 32
        config.logs.stdout = "logs/stdout.log"
        config.logs.stderr = "logs/stderr.log"
        
        desc = build_description("test", config=config)
        
        assert desc.model_path == "/path/to/model.gguf"
        assert desc.context_size == 4096
        assert desc.port == 8080
        assert desc.gpu_backend == "vulkan"
    
    @patch("llama_orchestrator.engine.validator.validate_process")
    @patch("llama_orchestrator.engine.state.load_runtime")
    @patch("llama_orchestrator.engine.state.get_recent_events")
    def test_build_with_runtime(self, mock_events, mock_load_runtime, mock_validate):
        """Test building description with runtime state."""
        mock_events.return_value = []
        
        # Create mock runtime
        runtime = MagicMock()
        runtime.pid = 12345
        runtime.status = "running"
        runtime.health = "healthy"
        runtime.started_at = datetime.now() - timedelta(hours=1)
        runtime.restart_count = 2
        runtime.config_hash = "abc123"
        runtime.binary_version = "b1234"
        runtime.last_health_check = datetime.now()
        runtime.last_health_latency_ms = 50.5
        
        mock_load_runtime.return_value = runtime
        
        # Mock process validation
        mock_validate.return_value = MagicMock(
            is_valid=True,
            exists=True,
            cmdline="llama-server --port 8080",
        )
        
        desc = build_description("test", runtime=runtime)
        
        assert desc.pid == 12345
        assert desc.status == "running"
        assert desc.health == "healthy"
        assert desc.restart_count == 2
        assert desc.process_valid is True
    
    @patch("llama_orchestrator.engine.state.load_runtime")
    @patch("llama_orchestrator.engine.state.get_recent_events")
    def test_build_with_events(self, mock_events, mock_load_runtime):
        """Test building description with events."""
        mock_load_runtime.return_value = None
        
        # Create mock events
        mock_events.return_value = [
            MagicMock(
                timestamp=datetime.now(),
                event_type="started",
                message="Instance started",
            ),
            MagicMock(
                timestamp=datetime.now(),
                event_type="health_check",
                message="Health check passed",
            ),
        ]
        
        desc = build_description("test", include_events=True)
        
        assert len(desc.recent_events) == 2
        assert desc.recent_events[0]["type"] == "started"
    
    @patch("llama_orchestrator.engine.state.load_runtime")
    @patch("llama_orchestrator.engine.state.get_recent_events")
    def test_build_without_events(self, mock_events, mock_load_runtime):
        """Test building description without events."""
        mock_load_runtime.return_value = None
        
        desc = build_description("test", include_events=False)
        
        mock_events.assert_not_called()
        assert desc.recent_events == []


class TestFormatDescriptionRich:
    """Tests for format_description_rich function."""
    
    def test_format_basic(self):
        """Test basic formatting."""
        desc = InstanceDescription(
            name="test",
            model_path="/path/to/model.gguf",
            port=8080,
            host="localhost",
            status="running",
            health="healthy",
        )
        
        output = format_description_rich(desc)
        
        assert "Configuration" in output
        assert "Runtime Status" in output
        assert "/path/to/model.gguf" in output
        assert "8080" in output
    
    def test_format_with_v2_details(self):
        """Test formatting with V2 details."""
        desc = InstanceDescription(
            name="test",
            config_hash="abc123def456",
            binary_version="b1234",
            last_health_check=datetime.now(),
            last_health_latency_ms=50.5,
        )
        
        output = format_description_rich(desc)
        
        assert "Runtime Details (V2)" in output
        assert "Config Hash" in output
        assert "Binary" in output
    
    def test_format_with_process_validation(self):
        """Test formatting with process validation."""
        desc = InstanceDescription(
            name="test",
            pid=12345,
            process_valid=True,
            process_exists=True,
            process_cmdline="llama-server --port 8080",
        )
        
        output = format_description_rich(desc)
        
        assert "Process Validation" in output
        assert "Valid" in output
    
    def test_format_with_events(self):
        """Test formatting with events."""
        desc = InstanceDescription(
            name="test",
            recent_events=[
                {
                    "timestamp": "2024-01-01T12:00:00",
                    "type": "started",
                    "message": "Instance started",
                },
            ],
        )
        
        output = format_description_rich(desc)
        
        assert "Recent Events" in output
        assert "started" in output
    
    def test_format_paths(self):
        """Test that paths are included."""
        desc = InstanceDescription(
            name="test",
            stdout_log="logs/test/stdout.log",
            stderr_log="logs/test/stderr.log",
            state_db_path="state/test.db",
        )
        
        output = format_description_rich(desc)
        
        assert "Paths" in output
        assert "stdout.log" in output
        assert "state/test.db" in output


class TestDescribeIntegration:
    """Integration tests for describe functionality."""
    
    def test_full_description_roundtrip(self):
        """Test creating description and converting to dict."""
        desc = InstanceDescription(
            name="integration-test",
            model_path="/models/test.gguf",
            context_size=4096,
            batch_size=512,
            threads=8,
            port=8080,
            host="localhost",
            gpu_backend="vulkan",
            gpu_device=0,
            gpu_layers=32,
            pid=12345,
            status="running",
            health="healthy",
            started_at=datetime.now() - timedelta(hours=2),
            uptime_seconds=7200,
            restart_count=0,
            config_hash="abc123",
            binary_version="b1234",
            process_valid=True,
            process_exists=True,
            recent_events=[
                {"timestamp": "2024-01-01T12:00:00", "type": "started", "message": "OK"},
            ],
            stdout_log="logs/test/stdout.log",
            stderr_log="logs/test/stderr.log",
        )
        
        # Convert to dict
        data = desc.to_dict()
        
        # Verify structure
        assert data["name"] == "integration-test"
        assert data["configuration"]["port"] == 8080
        assert data["runtime"]["pid"] == 12345
        assert data["process"]["valid"] is True
        assert len(data["events"]) == 1
        
        # Format for Rich
        output = format_description_rich(desc)
        
        # Verify output contains key information
        assert "running" in output.lower()
        assert "healthy" in output.lower()
        assert "8080" in output
