"""Async health-check client pool and concurrent check orchestrator.

This module replaces the legacy per-call ``httpx.Client`` pattern with a
shared async client pool, enabling true concurrent health checks across
N running instances.  All I/O is non-blocking and suitable for
integration with the Tkinter event loop via ``asyncio.create_task()``.

Usage
-----

Inside a background async task::

    pool = HealthClientPool()
    await pool.initialize()          # opens the shared client
    configs = load_running_configs()  # dict[str, InstanceConfig]
    results = await check_all_async(pool, configs)
    await pool.aclose()               # gracefully closes the client
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from llama_orchestrator.config import InstanceConfig
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
    RuntimeState,
    load_runtime,
    load_state,
    record_health_check,
    save_runtime,
    save_state,
)
from llama_orchestrator.health.checker import HealthCheckResult, HealthCheckStatus

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class AsyncProbeResult:
    """Result of an async health check against a single instance."""

    name: str
    status_code: int | None
    success: bool
    message: str
    response_time_ms: float
    raw: dict[str, Any] | None = None


@dataclass
class HealthClientPool:
    """Singleton-like pool sharing a single ``httpx.AsyncClient``.

    The pool is safe to call from any thread / event loop.  The client
    is created lazily on first use and closed on ``aclose()``.
    """

    timeout: float = 5.0
    _client: httpx.AsyncClient | None = field(init=False, default=None)

    async def initialize(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Open the shared client (no-op if already open)."""
        if self._client is None or self._client.is_closed:
            to = timeout if timeout is not None else self.timeout
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(to),
                http2=False,  # llama.cpp does not speak HTTP/2
            )
        assert self._client is not None  # guard for type checker
        return self._client

    async def get_client(self) -> httpx.AsyncClient:
        """Return the client, initialising if necessary."""
        if self._client is None:
            await self.initialize()
        assert self._client is not None  # guard for type checker
        return self._client

    async def aclose(self) -> None:
        """Gracefully close the shared client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient | None:
        """Synchronous accessor (returns None if not yet initialised)."""
        return self._client

    @property
    def is_open(self) -> bool:
        """True if the pool holds an active client."""
        return (
            self._client is not None and not self._client.is_closed
        )


# ---------------------------------------------------------------------------
# Async HTTP probe
# ---------------------------------------------------------------------------

async def _probe_http_async(
    client: httpx.AsyncClient,
    host: str,
    port: int,
    path: str,
    expected_status: list[int],
    expected_body: str | None,
    timeout: float,
) -> AsyncProbeResult:
    """Fire-and-forget async HTTP health probe."""
    url = f"http://{host}:{port}{path}"
    start = time.perf_counter()

    try:
        resp = await asyncio.wait_for(
            client.get(url, timeout=timeout),
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        if resp.status_code not in expected_status:
            return AsyncProbeResult(
                name="",
                status_code=resp.status_code,
                success=False,
                message=f"Unexpected status: {resp.status_code}",
                response_time_ms=elapsed_ms,
                raw=resp.json() if resp.is_server_error else None,
            )

        if expected_body and expected_body not in resp.text:
            return AsyncProbeResult(
                name="",
                status_code=resp.status_code,
                success=False,
                message=f"Expected body not found: {expected_body}",
                response_time_ms=elapsed_ms,
                raw={"text_snippet": resp.text[:200]},
            )

        return AsyncProbeResult(
            name="",
            status_code=resp.status_code,
            success=True,
            message="OK",
            response_time_ms=elapsed_ms,
            raw=resp.json() if resp.is_server_error else None,
        )

    except TimeoutError:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return AsyncProbeResult(
            name="",
            status_code=None,
            success=False,
            message=f"Timeout after {timeout}s",
            response_time_ms=elapsed_ms,
        )

    except httpx.ConnectError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return AsyncProbeResult(
            name="",
            status_code=None,
            success=False,
            message=f"Connection failed: {exc}",
            response_time_ms=elapsed_ms,
        )

    except httpx.HTTPStatusError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return AsyncProbeResult(
            name="",
            status_code=exc.response.status_code,
            success=False,
            message=f"HTTP error: {exc}",
            response_time_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return AsyncProbeResult(
            name="",
            status_code=None,
            success=False,
            message=f"Error: {exc}",
            response_time_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Async probe dispatcher
# ---------------------------------------------------------------------------

async def _probe_async(
    client: httpx.AsyncClient,
    config: InstanceConfig,
) -> AsyncProbeResult:
    """Dispatch to the correct async probe type and return a named result."""
    healthcheck = config.healthcheck
    name = config.name

    # TCP probe
    if healthcheck.type == "tcp":
        start = time.perf_counter()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(healthcheck.timeout)
            result = sock.connect_ex((config.server.host, config.server.port))
            sock.close()
            elapsed_ms = (time.perf_counter() - start) * 1000
            if result == 0:
                return AsyncProbeResult(
                    name=name,
                    status_code=None,
                    success=True,
                    message="TCP connection successful",
                    response_time_ms=elapsed_ms,
                )
            return AsyncProbeResult(
                name=name,
                status_code=None,
                success=False,
                message=f"TCP connection failed (error: {result})",
                response_time_ms=elapsed_ms,
            )
        except TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AsyncProbeResult(
                name=name,
                status_code=None,
                success=False,
                message=f"TCP timeout after {healthcheck.timeout}s",
                response_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return AsyncProbeResult(
                name=name,
                status_code=None,
                success=False,
                message=f"TCP error: {exc}",
                response_time_ms=elapsed_ms,
            )

    # HTTP probe (default)
    expected = healthcheck.expected_status or [200]
    return await _probe_http_async(
        client=client,
        host=config.server.host,
        port=config.server.port,
        path=healthcheck.path or "/health",
        expected_status=expected,
        expected_body=healthcheck.expected_body,
        timeout=float(healthcheck.timeout),
    )


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def check_one_async(
    client: httpx.AsyncClient,
    config: InstanceConfig,
) -> HealthCheckResult:
    """Perform a single async health check and return a legacy-compatible result.

    This function is a thin bridge that lets existing callers pass ``async``
    calls without refactoring their post-check logic (state persistence,
    callbacks, etc.).
    """
    probe_result = await _probe_async(client, config)

    if probe_result.success:
        return HealthCheckResult(
            status=HealthCheckStatus.OK,
            response_time_ms=probe_result.response_time_ms,
            raw_response=probe_result.raw,
        )

    msg_lower = probe_result.message.lower()
    if probe_result.status_code == 503:
        status = HealthCheckStatus.LOADING
    elif "timeout" in msg_lower:
        status = HealthCheckStatus.TIMEOUT
    elif any(t in msg_lower for t in ["connection failed", "refused", "unreachable"]):
        status = HealthCheckStatus.UNREACHABLE
    else:
        status = HealthCheckStatus.ERROR

    return HealthCheckResult(
        status=status,
        response_time_ms=probe_result.response_time_ms,
        error_message=probe_result.message,
        raw_response=probe_result.raw,
    )


async def check_all_async(
    pool: HealthClientPool,
    configs: dict[str, InstanceConfig],
    *,
    timeout: float | None = None,
) -> dict[str, HealthCheckResult]:
    """Fire concurrent health checks for all configs and await all results.

    Each instance is health-checked **in parallel** using the shared async
    client.  Total wall-clock time is ``max(timeout)`` regardless of N.

    Args:
        pool: HealthClientPool instance (must be initialised).
        configs: Name -> InstanceConfig mapping for running instances.
        timeout: Optional per-check timeout override.

    Returns:
        Dict mapping instance name to ``HealthCheckResult``.
    """
    client = await pool.get_client()
    if timeout is not None:
        client.timeout = httpx.Timeout(timeout)

    tasks = [check_one_async(client, cfg) for cfg in configs.values()]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    results: dict[str, HealthCheckResult] = {}
    names = list(configs.keys())
    for name, result in zip(names, results_raw, strict=True):
        if isinstance(result, Exception):
            logger.error(f"Async health check failed for {name}: {result}")
            results[name] = HealthCheckResult(
                status=HealthCheckStatus.ERROR,
                error_message=str(result),
            )
        else:
            # Type narrowed: result is HealthCheckResult (not BaseException)
            results[name] = result  # type: ignore[assignment]

    return results


# ---------------------------------------------------------------------------
# Convenience: full async health cycle (check -> persist -> callback)
# ---------------------------------------------------------------------------

@dataclass
class AsyncHealthConfig:
    """Per-instance async health check scheduling config."""

    interval: float = 10.0
    idle_interval: float = 30.0
    start_period: int = 60
    retries: int = 3
    timeout: float = 5.0
    backoff_enabled: bool = True
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    backoff_jitter: float = 0.1


@dataclass
class AsyncInstanceHealthState:
    """Tracks async health state for a single instance."""

    name: str
    consecutive_failures: int = 0
    last_check_time: float | None = None
    last_result: HealthCheckResult | None = None
    last_status: InstanceStatus = InstanceStatus.STOPPED
    in_start_period: bool = True
    restart_attempts: int = 0
    last_restart_time: float | None = None
    is_idle: bool = False


class AsyncHealthMonitor:
    """Async-driven health monitor that runs concurrently via ``asyncio``.

    Unlike the threaded ``HealthMonitor``, this monitor:
    - Checks all instances **in parallel** (``asyncio.gather``)
    - Uses the shared ``HealthClientPool`` (no per-call client creation)
    - Runs inside the caller's ``asyncio`` event loop (not a background thread)
    """

    def __init__(
        self,
        pool: HealthClientPool | None = None,
        on_health_change: Callable[[str, HealthStatus, HealthStatus], None] | None = None,
        on_restart: Callable[[str, int], None] | None = None,
        config: AsyncHealthConfig | None = None,
    ) -> None:
        self.pool = pool or HealthClientPool()
        self.on_health_change = on_health_change
        self.on_restart = on_restart
        self.config = config or AsyncHealthConfig()

        self._instance_states: dict[str, AsyncInstanceHealthState] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the async monitor loop."""
        if self._running:
            logger.warning("AsyncHealthMonitor already running")
            return
        await self.pool.initialize(timeout=self.config.timeout)
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop the monitor and close the client pool."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.pool.aclose()

    async def _monitor_loop(self) -> None:
        """Main async monitoring loop."""
        while self._running:
            try:
                await self._check_all_instances_async()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Error in async health monitor loop: {exc}")

            # Sleep with interruptible check
            loop = asyncio.get_event_loop()
            sleep_end = loop.time() + self.config.interval
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.sleep(sleep_end - loop.time()),
                    timeout=self.config.interval + 1,
                )

    async def _check_all_instances_async(self) -> None:
        """Discover running instances and health-check all in parallel."""
        from llama_orchestrator.config import discover_instances, get_instance_config
        from llama_orchestrator.engine.process import restart_instance

        running_names: list[str] = []
        running_configs: dict[str, InstanceConfig] = {}

        for name, _ in discover_instances():
            try:
                config = get_instance_config(name)
                state = load_state(name)
                if state is None or state.status != InstanceStatus.RUNNING:
                    continue
                running_names.append(name)
                running_configs[name] = config
            except Exception:
                continue

        if not running_names:
            return

        # Concurrent health checks
        results = await check_all_async(self.pool, running_configs)

        for name in running_names:
            result = results.get(name)
            if result is None:
                continue

            state = load_state(name)
            if state is None:
                continue

            old_health = state.health
            new_health = result.to_health_status

            # Update state
            await self._process_health_result_async(
                name=name,
                config=running_configs[name],
                state=state,
                result=result,
                new_health=new_health,
            )

            if old_health != new_health and self.on_health_change:
                try:
                    self.on_health_change(name, old_health, new_health)
                except Exception as e:
                    logger.error(f"on_health_change callback error for {name}: {e}")

            # Auto-restart if needed
            if self._should_restart(name, running_configs[name], result):
                try:
                    restart_instance(name, wait_for_ready=False)
                    state.restart_count += 1
                    save_state(state)
                    if self.on_restart:
                        self.on_restart(name, state.restart_count)
                except Exception as e:
                    logger.error(f"Failed to restart {name}: {e}")

    async def _process_health_result_async(
        self,
        name: str,
        config: InstanceConfig,
        state: InstanceState,
        result: HealthCheckResult,
        new_health: HealthStatus,
    ) -> None:
        """Update instance health state and persist."""
        async with self._lock:
            if name not in self._instance_states:
                self._instance_states[name] = AsyncInstanceHealthState(name=name)
            hs = self._instance_states[name]

        # Update instance health state
        hs.last_check_time = time.time()
        hs.last_result = result
        hs.last_status = state.status

        if result.is_healthy:
            hs.consecutive_failures = 0
        elif result.is_loading:
            if not hs.in_start_period:
                hs.consecutive_failures += 1
        else:
            hs.consecutive_failures += 1

        # Persist
        state.health = new_health
        state.last_health_check = time.time()
        save_state(state)

        runtime = load_runtime(name) or RuntimeState(name=name)
        runtime.port = config.server.port
        runtime.health = new_health
        runtime.last_seen_at = time.time()
        runtime.last_error = ""
        save_runtime(runtime)

        record_health_check(
            name,
            new_health,
            response_time_ms=result.response_time_ms,
            error_message=result.error_message or "",
        )

    def _should_restart(
        self,
        name: str,
        config: InstanceConfig,
        result: HealthCheckResult,
    ) -> bool:
        """Determine whether the instance should be auto-restarted."""
        if not config.restart_policy.enabled:
            return False

        hs = self._instance_states.get(name)
        if hs is None:
            return False
        if hs.in_start_period:
            return False
        if hs.consecutive_failures < config.healthcheck.retries:
            return False
        if hs.restart_attempts >= config.restart_policy.max_retries:
            return False
        if hs.last_restart_time:
            backoff = self._calculate_backoff(
                hs.restart_attempts,
                config.restart_policy.initial_delay,
                config.restart_policy.backoff_multiplier,
                config.restart_policy.max_delay,
            )
            if time.time() - hs.last_restart_time < backoff:
                return False
        return True

    def _calculate_backoff(self, attempt: int, base: float, multiplier: float, max_delay: float) -> float:
        """Calculate exponential backoff delay."""
        delay = base * (multiplier ** attempt)
        return min(delay, max_delay)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def instance_states(self) -> dict[str, AsyncInstanceHealthState]:
        return dict(self._instance_states)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_global_pool: HealthClientPool | None = None
_global_pool_lock = asyncio.Lock()


async def get_global_pool() -> HealthClientPool:
    """Return the singleton async client pool (initialised on first call)."""
    global _global_pool
    if _global_pool is None:
        async with _global_pool_lock:
            if _global_pool is None:
                _global_pool = HealthClientPool()
                await _global_pool.initialize()
    return _global_pool


async def close_global_pool() -> None:
    """Close the singleton async client pool."""
    global _global_pool
    if _global_pool is not None:
        await _global_pool.aclose()
        _global_pool = None
