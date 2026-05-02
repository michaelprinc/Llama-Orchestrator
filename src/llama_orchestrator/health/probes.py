"""
Pluggable health probe system for Llama Orchestrator V2.

Provides extensible health checking with HTTP, TCP, and custom probes.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig

logger = logging.getLogger(__name__)


class ProbeType(Enum):
    """Type of health probe."""
    
    HTTP = "http"
    TCP = "tcp"
    CUSTOM = "custom"


@dataclass
class ProbeResult:
    """Result of a health probe check."""
    
    success: bool
    response_time_ms: float
    status_code: int | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_healthy(self) -> bool:
        """Alias for success."""
        return self.success


class HealthProbe(ABC):
    """
    Abstract base class for health probes.
    
    Subclasses implement specific health check mechanisms.
    """
    
    def __init__(
        self,
        timeout: float = 5.0,
        retries: int = 0,
        retry_delay: float = 1.0,
    ):
        """
        Initialize health probe.
        
        Args:
            timeout: Timeout for each check in seconds
            retries: Number of retries on failure
            retry_delay: Delay between retries in seconds
        """
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
    
    @property
    @abstractmethod
    def probe_type(self) -> ProbeType:
        """Get the probe type."""
        pass
    
    @abstractmethod
    def check(self, host: str, port: int) -> ProbeResult:
        """
        Perform a health check.
        
        Args:
            host: Target host
            port: Target port
            
        Returns:
            ProbeResult with check outcome
        """
        pass
    
    def check_with_retry(self, host: str, port: int) -> ProbeResult:
        """
        Perform health check with retries.
        
        Args:
            host: Target host
            port: Target port
            
        Returns:
            ProbeResult from successful check or last failed attempt
        """
        last_result = None
        
        for attempt in range(self.retries + 1):
            result = self.check(host, port)
            
            if result.success:
                return result
            
            last_result = result
            
            if attempt < self.retries:
                time.sleep(self.retry_delay)
        
        return last_result or ProbeResult(
            success=False,
            response_time_ms=0,
            message="No check performed",
        )


class HTTPProbe(HealthProbe):
    """
    HTTP health probe.
    
    Checks health by making HTTP GET request to a specified path.
    """
    
    def __init__(
        self,
        path: str = "/health",
        expected_status: int | list[int] = 200,
        expected_body: str | None = None,
        **kwargs,
    ):
        """
        Initialize HTTP probe.
        
        Args:
            path: Health check endpoint path
            expected_status: Expected HTTP status code(s)
            expected_body: Expected substring in response body
            **kwargs: Additional arguments for HealthProbe
        """
        super().__init__(**kwargs)
        self.path = path
        self.expected_status = (
            [expected_status] if isinstance(expected_status, int)
            else list(expected_status)
        )
        self.expected_body = expected_body
    
    @property
    def probe_type(self) -> ProbeType:
        return ProbeType.HTTP
    
    def check(self, host: str, port: int) -> ProbeResult:
        """Perform HTTP health check."""
        url = f"http://{host}:{port}{self.path}"
        start = time.perf_counter()
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url)
            
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            # Check status code
            if response.status_code not in self.expected_status:
                return ProbeResult(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    message=f"Unexpected status: {response.status_code}",
                )
            
            # Check body if specified
            if self.expected_body and self.expected_body not in response.text:
                return ProbeResult(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=response.status_code,
                    message=f"Expected body not found: {self.expected_body}",
                )
            
            return ProbeResult(
                success=True,
                response_time_ms=elapsed_ms,
                status_code=response.status_code,
                message="OK",
                details={"url": url},
            )
            
        except httpx.TimeoutException:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Timeout after {self.timeout}s",
            )
        
        except httpx.ConnectError as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Connection failed: {e}",
            )
        
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Error: {e}",
            )


class TCPProbe(HealthProbe):
    """
    TCP health probe.
    
    Checks health by attempting TCP connection to the port.
    """
    
    @property
    def probe_type(self) -> ProbeType:
        return ProbeType.TCP
    
    def check(self, host: str, port: int) -> ProbeResult:
        """Perform TCP health check."""
        start = time.perf_counter()
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                result = sock.connect_ex((host, port))
            
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            if result == 0:
                return ProbeResult(
                    success=True,
                    response_time_ms=elapsed_ms,
                    message="TCP connection successful",
                    details={"host": host, "port": port},
                )
            else:
                return ProbeResult(
                    success=False,
                    response_time_ms=elapsed_ms,
                    message=f"TCP connection failed (error: {result})",
                )
                
        except socket.timeout:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP timeout after {self.timeout}s",
            )
        
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP error: {e}",
            )


class CustomProbe(HealthProbe):
    """
    Custom script health probe.
    
    Executes a custom script/command to check health.
    Exit code 0 = healthy, non-zero = unhealthy.
    """
    
    def __init__(
        self,
        script: str,
        shell: bool = True,
        **kwargs,
    ):
        """
        Initialize custom probe.
        
        Args:
            script: Script or command to execute
            shell: Whether to run in shell
            **kwargs: Additional arguments for HealthProbe
        """
        super().__init__(**kwargs)
        self.script = script
        self.shell = shell
    
    @property
    def probe_type(self) -> ProbeType:
        return ProbeType.CUSTOM
    
    def check(self, host: str, port: int) -> ProbeResult:
        """Execute custom health check script."""
        start = time.perf_counter()
        
        # Substitute placeholders in script
        script = self.script.replace("{host}", host).replace("{port}", str(port))
        
        try:
            result = subprocess.run(
                script,
                shell=self.shell,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            if result.returncode == 0:
                return ProbeResult(
                    success=True,
                    response_time_ms=elapsed_ms,
                    status_code=result.returncode,
                    message=result.stdout.strip() or "OK",
                    details={"script": script},
                )
            else:
                return ProbeResult(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=result.returncode,
                    message=result.stderr.strip() or f"Exit code: {result.returncode}",
                )
                
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Script timeout after {self.timeout}s",
            )
        
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Script error: {e}",
            )


@dataclass
class ProbeConfig:
    """Configuration for health probe."""
    
    type: ProbeType = ProbeType.HTTP
    path: str = "/health"
    expected_status: list[int] = field(default_factory=lambda: [200])
    expected_body: str | None = None
    custom_script: str | None = None
    timeout: float = 5.0
    retries: int = 0
    retry_delay: float = 1.0


class ProbeFactory:
    """
    Factory for creating health probes from configuration.
    """
    
    @staticmethod
    def create(config: ProbeConfig) -> HealthProbe:
        """
        Create a health probe from configuration.
        
        Args:
            config: Probe configuration
            
        Returns:
            Configured HealthProbe instance
        """
        common_kwargs = {
            "timeout": config.timeout,
            "retries": config.retries,
            "retry_delay": config.retry_delay,
        }
        
        if config.type == ProbeType.HTTP:
            return HTTPProbe(
                path=config.path,
                expected_status=config.expected_status,
                expected_body=config.expected_body,
                **common_kwargs,
            )
        
        elif config.type == ProbeType.TCP:
            return TCPProbe(**common_kwargs)
        
        elif config.type == ProbeType.CUSTOM:
            if not config.custom_script:
                raise ValueError("custom_script is required for CUSTOM probe type")
            return CustomProbe(
                script=config.custom_script,
                **common_kwargs,
            )
        
        else:
            raise ValueError(f"Unknown probe type: {config.type}")
    
    @staticmethod
    def from_dict(data: dict) -> HealthProbe:
        """
        Create a health probe from dictionary configuration.
        
        Args:
            data: Dictionary with probe settings
            
        Returns:
            Configured HealthProbe instance
        """
        probe_type = ProbeType(data.get("type", "http"))
        
        config = ProbeConfig(
            type=probe_type,
            path=data.get("path", "/health"),
            expected_status=data.get("expected_status", [200]),
            expected_body=data.get("expected_body"),
            custom_script=data.get("custom_script"),
            timeout=data.get("timeout", 5.0),
            retries=data.get("retries", 0),
            retry_delay=data.get("retry_delay", 1.0),
        )
        
        return ProbeFactory.create(config)
    
    @staticmethod
    def from_instance_config(instance_config: "InstanceConfig") -> HealthProbe:
        """
        Create a health probe from instance configuration.
        
        Args:
            instance_config: Instance configuration object
            
        Returns:
            Configured HealthProbe instance
        """
        # Get healthcheck config, defaulting to HTTP probe
        healthcheck = getattr(instance_config, "healthcheck", None) or {}
        
        if isinstance(healthcheck, dict):
            return ProbeFactory.from_dict(healthcheck)
        
        # Default HTTP probe
        return HTTPProbe(
            path="/health",
            expected_status=[200],
            timeout=5.0,
        )


# Default probe for backward compatibility
def get_default_probe() -> HTTPProbe:
    """Get the default HTTP health probe."""
    return HTTPProbe(
        path="/health",
        expected_status=[200],
        timeout=5.0,
    )
