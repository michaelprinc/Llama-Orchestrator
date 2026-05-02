"""
Tests for pluggable health probes (V2).
"""

import socket
import subprocess
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from llama_orchestrator.health.probes import (
    CustomProbe,
    HealthProbe,
    HTTPProbe,
    ProbeConfig,
    ProbeFactory,
    ProbeResult,
    ProbeType,
    TCPProbe,
    get_default_probe,
)


# =============================================================================
# ProbeResult Tests
# =============================================================================

class TestProbeResult:
    """Tests for ProbeResult dataclass."""
    
    def test_success_result(self):
        """Test successful probe result."""
        result = ProbeResult(
            success=True,
            response_time_ms=50.5,
            status_code=200,
            message="OK",
        )
        
        assert result.success is True
        assert result.is_healthy is True
        assert result.response_time_ms == 50.5
        assert result.status_code == 200
        assert result.message == "OK"
    
    def test_failure_result(self):
        """Test failed probe result."""
        result = ProbeResult(
            success=False,
            response_time_ms=100.0,
            message="Connection failed",
        )
        
        assert result.success is False
        assert result.is_healthy is False
        assert result.status_code is None
    
    def test_details_default(self):
        """Test that details defaults to empty dict."""
        result = ProbeResult(success=True, response_time_ms=10.0)
        assert result.details == {}


# =============================================================================
# HTTPProbe Tests
# =============================================================================

class TestHTTPProbe:
    """Tests for HTTPProbe."""
    
    def test_probe_type(self):
        """Test probe type is HTTP."""
        probe = HTTPProbe()
        assert probe.probe_type == ProbeType.HTTP
    
    def test_default_settings(self):
        """Test default probe settings."""
        probe = HTTPProbe()
        assert probe.path == "/health"
        assert probe.expected_status == [200]
        assert probe.expected_body is None
        assert probe.timeout == 5.0
    
    def test_custom_settings(self):
        """Test custom probe settings."""
        probe = HTTPProbe(
            path="/api/health",
            expected_status=[200, 204],
            expected_body="ok",
            timeout=10.0,
        )
        assert probe.path == "/api/health"
        assert probe.expected_status == [200, 204]
        assert probe.expected_body == "ok"
        assert probe.timeout == 10.0
    
    @patch.object(httpx.Client, "get")
    def test_successful_check(self, mock_get):
        """Test successful HTTP health check."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"
        mock_get.return_value = mock_response
        
        probe = HTTPProbe()
        
        with patch.object(httpx, "Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            result = probe.check("localhost", 8080)
        
        assert result.success is True
        assert result.status_code == 200
    
    def test_timeout_handling(self):
        """Test timeout is properly handled."""
        probe = HTTPProbe(timeout=0.1)
        
        with patch.object(httpx, "Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = httpx.TimeoutException("timeout")
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "Timeout" in result.message
    
    def test_connection_error_handling(self):
        """Test connection error is properly handled."""
        probe = HTTPProbe()
        
        with patch.object(httpx, "Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("failed")
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "Connection failed" in result.message
    
    def test_unexpected_status_code(self):
        """Test handling of unexpected status code."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        
        probe = HTTPProbe(expected_status=[200])
        
        with patch.object(httpx, "Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert result.status_code == 500
        assert "Unexpected status" in result.message
    
    def test_expected_body_not_found(self):
        """Test handling when expected body is not found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "healthy"
        
        probe = HTTPProbe(expected_body="OK")
        
        with patch.object(httpx, "Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "Expected body not found" in result.message


# =============================================================================
# TCPProbe Tests
# =============================================================================

class TestTCPProbe:
    """Tests for TCPProbe."""
    
    def test_probe_type(self):
        """Test probe type is TCP."""
        probe = TCPProbe()
        assert probe.probe_type == ProbeType.TCP
    
    def test_default_settings(self):
        """Test default probe settings."""
        probe = TCPProbe()
        assert probe.timeout == 5.0
        assert probe.retries == 0
    
    def test_successful_connection(self):
        """Test successful TCP connection."""
        probe = TCPProbe(timeout=1.0)
        
        with patch("socket.socket") as mock_socket:
            mock_sock_instance = MagicMock()
            mock_sock_instance.connect_ex.return_value = 0
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            
            result = probe.check("localhost", 8080)
        
        assert result.success is True
        assert "TCP connection successful" in result.message
    
    def test_connection_refused(self):
        """Test connection refused."""
        probe = TCPProbe(timeout=1.0)
        
        with patch("socket.socket") as mock_socket:
            mock_sock_instance = MagicMock()
            mock_sock_instance.connect_ex.return_value = 111  # Connection refused
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "TCP connection failed" in result.message
    
    def test_timeout(self):
        """Test TCP timeout."""
        probe = TCPProbe(timeout=0.1)
        
        with patch("socket.socket") as mock_socket:
            mock_sock_instance = MagicMock()
            mock_sock_instance.connect_ex.side_effect = socket.timeout()
            mock_socket.return_value.__enter__.return_value = mock_sock_instance
            
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "timeout" in result.message.lower()


# =============================================================================
# CustomProbe Tests
# =============================================================================

class TestCustomProbe:
    """Tests for CustomProbe."""
    
    def test_probe_type(self):
        """Test probe type is CUSTOM."""
        probe = CustomProbe(script="echo ok")
        assert probe.probe_type == ProbeType.CUSTOM
    
    def test_placeholder_substitution(self):
        """Test host and port placeholder substitution."""
        probe = CustomProbe(script="curl http://{host}:{port}/health")
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="OK",
                stderr="",
            )
            probe.check("localhost", 8080)
            
            # Check that placeholders were replaced
            call_args = mock_run.call_args
            assert "localhost" in call_args[0][0]
            assert "8080" in call_args[0][0]
    
    def test_successful_script(self):
        """Test successful script execution."""
        probe = CustomProbe(script="echo ok", timeout=5.0)
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="OK",
                stderr="",
            )
            result = probe.check("localhost", 8080)
        
        assert result.success is True
        assert result.status_code == 0
    
    def test_failed_script(self):
        """Test failed script execution."""
        probe = CustomProbe(script="exit 1")
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error occurred",
            )
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert result.status_code == 1
        assert "Error occurred" in result.message
    
    def test_script_timeout(self):
        """Test script timeout."""
        probe = CustomProbe(script="sleep 10", timeout=0.1)
        
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 10", timeout=0.1)
            result = probe.check("localhost", 8080)
        
        assert result.success is False
        assert "timeout" in result.message.lower()


# =============================================================================
# HealthProbe Retry Tests
# =============================================================================

class TestHealthProbeRetry:
    """Tests for retry functionality."""
    
    def test_retry_on_failure(self):
        """Test that probe retries on failure."""
        probe = HTTPProbe(retries=2, retry_delay=0.01)
        
        call_count = 0
        
        def mock_check(*args):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return ProbeResult(success=False, response_time_ms=10, message="fail")
            return ProbeResult(success=True, response_time_ms=10, message="ok")
        
        with patch.object(probe, "check", side_effect=mock_check):
            result = probe.check_with_retry("localhost", 8080)
        
        assert call_count == 3
        assert result.success is True
    
    def test_no_retry_on_success(self):
        """Test that probe doesn't retry on success."""
        probe = HTTPProbe(retries=2, retry_delay=0.01)
        
        call_count = 0
        
        def mock_check(*args):
            nonlocal call_count
            call_count += 1
            return ProbeResult(success=True, response_time_ms=10, message="ok")
        
        with patch.object(probe, "check", side_effect=mock_check):
            result = probe.check_with_retry("localhost", 8080)
        
        assert call_count == 1
        assert result.success is True
    
    def test_returns_last_failure(self):
        """Test that last failure result is returned after all retries."""
        probe = HTTPProbe(retries=1, retry_delay=0.01)
        
        with patch.object(probe, "check") as mock_check:
            mock_check.return_value = ProbeResult(
                success=False, 
                response_time_ms=10, 
                message="always fail"
            )
            result = probe.check_with_retry("localhost", 8080)
        
        assert result.success is False
        assert "always fail" in result.message
        assert mock_check.call_count == 2  # Initial + 1 retry


# =============================================================================
# ProbeConfig Tests
# =============================================================================

class TestProbeConfig:
    """Tests for ProbeConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = ProbeConfig()
        
        assert config.type == ProbeType.HTTP
        assert config.path == "/health"
        assert config.expected_status == [200]
        assert config.expected_body is None
        assert config.custom_script is None
        assert config.timeout == 5.0
        assert config.retries == 0
        assert config.retry_delay == 1.0
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = ProbeConfig(
            type=ProbeType.TCP,
            timeout=10.0,
            retries=3,
        )
        
        assert config.type == ProbeType.TCP
        assert config.timeout == 10.0
        assert config.retries == 3


# =============================================================================
# ProbeFactory Tests
# =============================================================================

class TestProbeFactory:
    """Tests for ProbeFactory."""
    
    def test_create_http_probe(self):
        """Test creating HTTP probe from config."""
        config = ProbeConfig(
            type=ProbeType.HTTP,
            path="/api/health",
            expected_status=[200, 204],
            timeout=10.0,
        )
        
        probe = ProbeFactory.create(config)
        
        assert isinstance(probe, HTTPProbe)
        assert probe.path == "/api/health"
        assert probe.expected_status == [200, 204]
        assert probe.timeout == 10.0
    
    def test_create_tcp_probe(self):
        """Test creating TCP probe from config."""
        config = ProbeConfig(
            type=ProbeType.TCP,
            timeout=3.0,
        )
        
        probe = ProbeFactory.create(config)
        
        assert isinstance(probe, TCPProbe)
        assert probe.timeout == 3.0
    
    def test_create_custom_probe(self):
        """Test creating custom probe from config."""
        config = ProbeConfig(
            type=ProbeType.CUSTOM,
            custom_script="echo ok",
            timeout=5.0,
        )
        
        probe = ProbeFactory.create(config)
        
        assert isinstance(probe, CustomProbe)
        assert probe.script == "echo ok"
    
    def test_custom_probe_requires_script(self):
        """Test that custom probe requires script."""
        config = ProbeConfig(type=ProbeType.CUSTOM)
        
        with pytest.raises(ValueError, match="custom_script is required"):
            ProbeFactory.create(config)
    
    def test_from_dict_http(self):
        """Test creating probe from dictionary - HTTP."""
        data = {
            "type": "http",
            "path": "/ready",
            "expected_status": [200],
            "timeout": 3.0,
        }
        
        probe = ProbeFactory.from_dict(data)
        
        assert isinstance(probe, HTTPProbe)
        assert probe.path == "/ready"
    
    def test_from_dict_tcp(self):
        """Test creating probe from dictionary - TCP."""
        data = {
            "type": "tcp",
            "timeout": 2.0,
        }
        
        probe = ProbeFactory.from_dict(data)
        
        assert isinstance(probe, TCPProbe)
        assert probe.timeout == 2.0
    
    def test_from_dict_defaults(self):
        """Test from_dict with minimal input."""
        data = {}
        
        probe = ProbeFactory.from_dict(data)
        
        assert isinstance(probe, HTTPProbe)
        assert probe.path == "/health"
    
    def test_from_instance_config_with_healthcheck(self):
        """Test creating probe from instance config."""
        mock_config = MagicMock()
        mock_config.healthcheck = {
            "type": "tcp",
            "timeout": 3.0,
        }
        
        probe = ProbeFactory.from_instance_config(mock_config)
        
        assert isinstance(probe, TCPProbe)
        assert probe.timeout == 3.0
    
    def test_from_instance_config_no_healthcheck(self):
        """Test creating probe when no healthcheck config."""
        mock_config = MagicMock()
        mock_config.healthcheck = None
        
        probe = ProbeFactory.from_instance_config(mock_config)
        
        assert isinstance(probe, HTTPProbe)
        assert probe.path == "/health"


# =============================================================================
# get_default_probe Tests
# =============================================================================

class TestGetDefaultProbe:
    """Tests for get_default_probe function."""
    
    def test_returns_http_probe(self):
        """Test that default probe is HTTP."""
        probe = get_default_probe()
        
        assert isinstance(probe, HTTPProbe)
        assert probe.path == "/health"
        assert probe.expected_status == [200]
        assert probe.timeout == 5.0


# =============================================================================
# ProbeType Tests
# =============================================================================

class TestProbeType:
    """Tests for ProbeType enum."""
    
    def test_enum_values(self):
        """Test enum values."""
        assert ProbeType.HTTP.value == "http"
        assert ProbeType.TCP.value == "tcp"
        assert ProbeType.CUSTOM.value == "custom"
    
    def test_from_string(self):
        """Test creating enum from string."""
        assert ProbeType("http") == ProbeType.HTTP
        assert ProbeType("tcp") == ProbeType.TCP
        assert ProbeType("custom") == ProbeType.CUSTOM
