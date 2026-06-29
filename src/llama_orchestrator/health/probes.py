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


class ProbeExecutionMode(Enum):
    """Execution mode for custom probes."""

    DISABLED = "disabled"
    RESTRICTED = "restricted"
    SANDBOXED = "sandboxed"


class ProbeSecurityError(Exception):
    """Raised when a custom probe security violation is detected."""

    def __init__(self, message: str, script: str = ""):
        self.message = message
        self.script = script
        super().__init__(self.message)


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

    SECURITY: Custom probes are disabled by default. They must be
    explicitly enabled via an execution policy before use.
    """

    # Maximum output size to prevent memory exhaustion (16 KB)
    MAX_OUTPUT_BYTES = 16384

    def __init__(
        self,
        script: str,
        shell: bool = False,
        execution_mode: ProbeExecutionMode = ProbeExecutionMode.DISABLED,
        allowlist_directory: str | None = None,
        **kwargs,
    ):
        """
        Initialize custom probe.

        Args:
            script: Script or command to execute
            shell: Whether to run in shell (always False in restricted mode)
            execution_mode: Security policy for probe execution
            allowlist_directory: Directory path where scripts are allowed to reside
            **kwargs: Additional arguments for HealthProbe

        Raises:
            ProbeSecurityError: If execution mode is DISABLED or security checks fail
        """
        super().__init__(**kwargs)
        self.script = script
        self.shell = shell
        self.execution_mode = execution_mode
        self.allowlist_directory = allowlist_directory

        # Security checks at construction time
        if execution_mode == ProbeExecutionMode.DISABLED:
            raise ProbeSecurityError(
                "Custom probes are disabled. Set execution_mode to 'restricted' or 'sandboxed' to enable.",
                script=script,
            )

        # Validate script path if allowlist is set
        if allowlist_directory and execution_mode in (
            ProbeExecutionMode.RESTRICTED,
            ProbeExecutionMode.SANDBOXED,
        ):
            self._validate_allowlist(script, allowlist_directory)

    def _validate_allowlist(self, script: str, allowlist_dir: str) -> None:
        """Validate that the script resides within the allowlisted directory."""
        import os

        # Extract script path (first token, handling quotes)
        raw = script.strip()
        if raw.startswith(("'", '"')):
            end = raw.index(raw[0], 1)
            path = raw[1:end] if end > 1 else raw
        else:
            path = raw.split()[0] if raw.split() else raw

        # Resolve to absolute path
        resolved = os.path.realpath(path)
        allowed = os.path.realpath(allowlist_dir)

        if not resolved.startswith(allowed + os.sep) and resolved != allowed:
            raise ProbeSecurityError(
                f"Script '{path}' is outside allowlisted directory '{allowlist_dir}'",
                script=script,
            )

    @property
    def probe_type(self) -> ProbeType:
        return ProbeType.CUSTOM

    def check(self, host: str, port: int) -> ProbeResult:
        """Execute custom health check script with security restrictions."""
        start = time.perf_counter()

        # Substitute placeholders in script
        script = self.script.replace("{host}", host).replace("{port}", str(port))

        try:
            # Restricted mode: always use list args, never shell=True
            if self.execution_mode in (
                ProbeExecutionMode.RESTRICTED,
                ProbeExecutionMode.SANDBOXED,
            ):
                import shlex

                args = shlex.split(script)
                result = subprocess.run(
                    args,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=self.allowlist_directory or None,
                    env=self._sanitized_env(),
                )
            else:
                # Legacy mode (execution_mode not restricted/sandboxed)
                result = subprocess.run(
                    script,
                    shell=self.shell,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Truncate output to prevent memory exhaustion
            stdout = (result.stdout or "")[: self.MAX_OUTPUT_BYTES]
            stderr = (result.stderr or "")[: self.MAX_OUTPUT_BYTES]

            if result.returncode == 0:
                return ProbeResult(
                    success=True,
                    response_time_ms=elapsed_ms,
                    status_code=result.returncode,
                    message=stdout.strip() or "OK",
                    details={
                        "script": script,
                        "execution_mode": self.execution_mode.value,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                )
            else:
                return ProbeResult(
                    success=False,
                    response_time_ms=elapsed_ms,
                    status_code=result.returncode,
                    message=stderr.strip() or f"Exit code: {result.returncode}",
                    details={
                        "script": script,
                        "execution_mode": self.execution_mode.value,
                        "duration_ms": round(elapsed_ms, 2),
                    },
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

    @staticmethod
    def _sanitized_env() -> dict[str, str]:
        """Return a sanitized environment for restricted probe execution."""
        import os

        # Only allow safe environment variables
        safe_keys = {
            "PATH",
            "HOME",
            "TEMP",
            "TMP",
            "SYSTEMROOT",
            "WINDIR",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "PROGRAMDATA",
        }
        return {k: v for k, v in os.environ.items() if k in safe_keys}


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
    # Custom probe security settings
    execution_mode: str = "disabled"
    allowlist_directory: str | None = None


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
            # Parse execution mode string to enum
            try:
                exec_mode = ProbeExecutionMode(config.execution_mode)
            except ValueError:
                exec_mode = ProbeExecutionMode.DISABLED
            return CustomProbe(
                script=config.custom_script,
                execution_mode=exec_mode,
                allowlist_directory=config.allowlist_directory,
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
            execution_mode=data.get("execution_mode", "disabled"),
            allowlist_directory=data.get("allowlist_directory"),
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
        healthcheck = getattr(instance_config, "healthcheck", None)

        if healthcheck is None:
            return get_default_probe()

        if hasattr(healthcheck, "to_probe_dict"):
            return ProbeFactory.from_dict(healthcheck.to_probe_dict())

        if hasattr(healthcheck, "model_dump"):
            return ProbeFactory.from_dict(healthcheck.model_dump())

        if isinstance(healthcheck, dict):
            return ProbeFactory.from_dict(healthcheck)

        return get_default_probe()


# Default probe for backward compatibility
def get_default_probe() -> HTTPProbe:
    """Get the default HTTP health probe."""
    return HTTPProbe(
        path="/health",
        expected_status=[200],
        timeout=5.0,
    )
