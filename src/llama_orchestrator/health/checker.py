"""
Health check client for llama.cpp server.

Uses httpx to perform HTTP health checks against running instances.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import httpx

from llama_orchestrator.config import get_instance_config
from llama_orchestrator.engine.state import HealthStatus

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig


class HealthCheckStatus(Enum):
    """Result status of a health check."""
    OK = "ok"
    LOADING = "loading"
    ERROR = "error"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    
    status: HealthCheckStatus
    response_time_ms: float | None = None
    error_message: str | None = None
    raw_response: dict | None = None
    slots_idle: int | None = None
    slots_processing: int | None = None
    
    @property
    def is_healthy(self) -> bool:
        """Check if the result indicates a healthy instance."""
        return self.status == HealthCheckStatus.OK
    
    @property
    def is_loading(self) -> bool:
        """Check if the instance is still loading."""
        return self.status == HealthCheckStatus.LOADING
    
    @property
    def to_health_status(self) -> HealthStatus:
        """Convert to HealthStatus enum for state storage."""
        return {
            HealthCheckStatus.OK: HealthStatus.HEALTHY,
            HealthCheckStatus.LOADING: HealthStatus.LOADING,
            HealthCheckStatus.ERROR: HealthStatus.ERROR,
            HealthCheckStatus.UNREACHABLE: HealthStatus.UNHEALTHY,
            HealthCheckStatus.TIMEOUT: HealthStatus.UNHEALTHY,
        }.get(self.status, HealthStatus.UNKNOWN)


def check_health(
    host: str,
    port: int,
    path: str = "/health",
    timeout: float = 5.0,
) -> HealthCheckResult:
    """
    Perform a health check against a llama.cpp server.
    
    Args:
        host: Server hostname or IP
        port: Server port
        path: Health check endpoint path (default: /health)
        timeout: Request timeout in seconds
        
    Returns:
        HealthCheckResult with status and response info
    """
    url = f"http://{host}:{port}{path}"
    start_time = time.perf_counter()
    
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    # llama.cpp /health returns {"status": "ok"} when ready
                    # or {"status": "loading model"} during startup
                    status_str = data.get("status", "").lower()
                    
                    if status_str == "ok":
                        return HealthCheckResult(
                            status=HealthCheckStatus.OK,
                            response_time_ms=elapsed_ms,
                            raw_response=data,
                            slots_idle=data.get("slots_idle"),
                            slots_processing=data.get("slots_processing"),
                        )
                    elif "loading" in status_str:
                        return HealthCheckResult(
                            status=HealthCheckStatus.LOADING,
                            response_time_ms=elapsed_ms,
                            raw_response=data,
                        )
                    else:
                        return HealthCheckResult(
                            status=HealthCheckStatus.ERROR,
                            response_time_ms=elapsed_ms,
                            error_message=f"Unknown status: {status_str}",
                            raw_response=data,
                        )
                except Exception as e:
                    # Response wasn't valid JSON
                    return HealthCheckResult(
                        status=HealthCheckStatus.OK,
                        response_time_ms=elapsed_ms,
                        error_message=f"Invalid JSON response: {e}",
                    )
            elif response.status_code == 503:
                # Service unavailable - typically means still loading
                return HealthCheckResult(
                    status=HealthCheckStatus.LOADING,
                    response_time_ms=elapsed_ms,
                    error_message=f"Service unavailable (HTTP 503)",
                )
            else:
                return HealthCheckResult(
                    status=HealthCheckStatus.ERROR,
                    response_time_ms=elapsed_ms,
                    error_message=f"HTTP {response.status_code}",
                )
                
    except httpx.ConnectError as e:
        return HealthCheckResult(
            status=HealthCheckStatus.UNREACHABLE,
            error_message=f"Connection refused: {e}",
        )
    except httpx.TimeoutException as e:
        return HealthCheckResult(
            status=HealthCheckStatus.TIMEOUT,
            error_message=f"Request timed out: {e}",
        )
    except Exception as e:
        return HealthCheckResult(
            status=HealthCheckStatus.ERROR,
            error_message=f"Unexpected error: {e}",
        )


def check_health_with_fallback(
    host: str,
    port: int,
    timeout: float = 5.0,
) -> HealthCheckResult:
    """
    Check health with fallback to /v1/health endpoint.
    
    Some older versions of llama.cpp use /v1/health instead of /health.
    This function tries both endpoints.
    
    Args:
        host: Server hostname or IP
        port: Server port
        timeout: Request timeout in seconds
        
    Returns:
        HealthCheckResult from first successful check
    """
    # Try primary endpoint first
    result = check_health(host, port, "/health", timeout)
    
    if result.status == HealthCheckStatus.UNREACHABLE:
        # Try fallback endpoint
        fallback_result = check_health(host, port, "/v1/health", timeout)
        if fallback_result.status != HealthCheckStatus.UNREACHABLE:
            return fallback_result
    
    return result


def check_instance_health(name: str, timeout: float | None = None) -> HealthCheckResult:
    """
    Check health of a named instance.
    
    Loads the instance configuration and performs a health check
    against the configured host:port.
    
    Args:
        name: Instance name
        timeout: Optional timeout override (uses config default if not provided)
        
    Returns:
        HealthCheckResult
        
    Raises:
        FileNotFoundError: If instance config doesn't exist
    """
    config = get_instance_config(name)
    
    check_timeout = timeout if timeout is not None else config.healthcheck.timeout
    
    return check_health_with_fallback(
        host=config.server.host,
        port=config.server.port,
        timeout=float(check_timeout),
    )
