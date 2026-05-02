"""
Tests for llama_orchestrator.health module.
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from llama_orchestrator.health.checker import (
    HealthCheckResult,
    HealthCheckStatus,
    check_health,
    check_health_with_fallback,
)
from llama_orchestrator.engine.state import HealthStatus


class TestHealthCheckStatus:
    """Tests for HealthCheckStatus enum."""

    def test_all_statuses_exist(self):
        """Test all expected statuses are defined."""
        assert HealthCheckStatus.OK
        assert HealthCheckStatus.LOADING
        assert HealthCheckStatus.ERROR
        assert HealthCheckStatus.UNREACHABLE
        assert HealthCheckStatus.TIMEOUT


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_is_healthy_ok(self):
        """Test is_healthy returns True for OK status."""
        result = HealthCheckResult(status=HealthCheckStatus.OK)
        assert result.is_healthy is True

    def test_is_healthy_error(self):
        """Test is_healthy returns False for ERROR status."""
        result = HealthCheckResult(status=HealthCheckStatus.ERROR)
        assert result.is_healthy is False

    def test_is_loading(self):
        """Test is_loading property."""
        loading = HealthCheckResult(status=HealthCheckStatus.LOADING)
        assert loading.is_loading is True
        
        ok = HealthCheckResult(status=HealthCheckStatus.OK)
        assert ok.is_loading is False

    def test_to_health_status_mapping(self):
        """Test conversion to HealthStatus enum."""
        cases = [
            (HealthCheckStatus.OK, HealthStatus.HEALTHY),
            (HealthCheckStatus.LOADING, HealthStatus.LOADING),
            (HealthCheckStatus.ERROR, HealthStatus.ERROR),
            (HealthCheckStatus.UNREACHABLE, HealthStatus.UNHEALTHY),
            (HealthCheckStatus.TIMEOUT, HealthStatus.UNHEALTHY),
        ]
        
        for check_status, expected_health in cases:
            result = HealthCheckResult(status=check_status)
            assert result.to_health_status == expected_health


class TestCheckHealth:
    """Tests for check_health function."""

    def test_healthy_response(self):
        """Test parsing a healthy response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "slots_idle": 1, "slots_processing": 0}
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.OK
            assert result.is_healthy is True
            assert result.slots_idle == 1
            assert result.slots_processing == 0

    def test_loading_response(self):
        """Test parsing a loading response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "loading model"}
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.LOADING
            assert result.is_loading is True

    def test_503_service_unavailable(self):
        """Test handling 503 response."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.LOADING

    def test_connection_refused(self):
        """Test handling connection refused."""
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("Connection refused")
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.UNREACHABLE
            assert "refused" in result.error_message.lower()

    def test_timeout(self):
        """Test handling request timeout."""
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = httpx.TimeoutException("Timed out")
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.TIMEOUT

    def test_response_time_recorded(self):
        """Test that response time is recorded."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            
            result = check_health("127.0.0.1", 8001)
            
            assert result.response_time_ms is not None
            assert result.response_time_ms >= 0

    def test_custom_path(self):
        """Test using a custom health path."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        with patch("httpx.Client") as mock_client:
            mock_get = mock_client.return_value.__enter__.return_value.get
            mock_get.return_value = mock_response
            
            check_health("127.0.0.1", 8001, path="/v1/health")
            
            mock_get.assert_called_once()
            call_url = mock_get.call_args[0][0]
            assert "/v1/health" in call_url


class TestCheckHealthWithFallback:
    """Tests for check_health_with_fallback function."""

    def test_primary_succeeds(self):
        """Test that primary endpoint is used when it succeeds."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            
            result = check_health_with_fallback("127.0.0.1", 8001)
            
            assert result.status == HealthCheckStatus.OK

    def test_fallback_on_unreachable(self):
        """Test fallback to /v1/health when primary fails."""
        call_count = 0
        
        def mock_get(url):
            nonlocal call_count
            call_count += 1
            
            if "/health" in url and "/v1" not in url:
                raise httpx.ConnectError("Connection refused")
            else:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "ok"}
                return mock_response
        
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = mock_get
            
            result = check_health_with_fallback("127.0.0.1", 8001)
            
            # Should have tried both endpoints
            assert call_count == 2
            assert result.status == HealthCheckStatus.OK
