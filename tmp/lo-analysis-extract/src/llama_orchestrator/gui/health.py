"""Health check client pool and async health monitoring.

Extracted from app.py (Phase 5: Module extraction).
Manages health check HTTP clients and async health monitoring.

NOTE: This module does NOT import from app.py to avoid circular imports.
Health results are reported via callbacks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
from aiohttp import ClientSession, ClientTimeout


@dataclass
class HealthResult:
    """Result of a health check for a single instance."""
    instance_name: str
    healthy: bool
    message: str = ""
    elapsed_ms: float = 0.0


@dataclass
class HealthClientPool:
    """Pool of aiohttp.ClientSession instances for health checks."""
    sessions: dict[str, ClientSession] = field(default_factory=dict)
    timeout: ClientTimeout = field(
        default_factory=lambda: ClientTimeout(total=5.0)
    )
    max_concurrent: int = 10


# ─── Public API ───────────────────────────────────────────────────────


async def check_health(
    instance_name: str,
    host: str,
    port: int,
    timeout: ClientTimeout | None = None,
) -> HealthResult:
    """Check health of a single instance.

    Args:
        instance_name: Name of the instance.
        host: Host to check.
        port: Port to check.
        timeout: Optional timeout override.

    Returns:
        HealthResult with status.
    """
    url = f"http://{host}:{port}/health"
    timeout = timeout or ClientTimeout(total=5.0)
    start = asyncio.get_event_loop().time()

    try:
        # SIM117: Can't combine async with statements (no Python syntax for that)
        async with aiohttp.ClientSession(timeout=timeout) as session:  # noqa: SIM117
            async with session.get(url) as resp:
                elapsed = (asyncio.get_event_loop().time() - start) * 1000
                if resp.status == 200:
                    return HealthResult(
                        instance_name=instance_name,
                        healthy=True,
                        elapsed_ms=elapsed,
                    )
                return HealthResult(
                    instance_name=instance_name,
                    healthy=False,
                    message=f"HTTP {resp.status}",
                    elapsed_ms=elapsed,
                )
    except Exception as exc:
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        return HealthResult(
            instance_name=instance_name,
            healthy=False,
            message=str(exc),
            elapsed_ms=elapsed,
        )


async def check_health_batch(
    instances: list[tuple[str, str, int]],
    on_result: Callable[[HealthResult], None],
    max_concurrent: int = 10,
) -> list[HealthResult]:
    """Check health of multiple instances concurrently.

    Args:
        instances: List of (name, host, port) tuples.
        on_result: Callback for each result.
        max_concurrent: Max concurrent requests.

    Returns:
        List of all HealthResult objects.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[HealthResult] = []

    async with aiohttp.ClientSession() as session:
        async def _check(name: str, host: str, port: int) -> None:
            async with semaphore:
                result = await _check_single(name, host, port, session)
                results.append(result)
                on_result(result)

        tasks = [
            asyncio.create_task(_check(name, host, port))
            for name, host, port in instances
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    return results


async def _check_single(
    name: str,
    host: str,
    port: int,
    session: ClientSession,
) -> HealthResult:
    """Check health of a single instance using existing session."""
    url = f"http://{host}:{port}/health"
    start = asyncio.get_event_loop().time()

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
            elapsed = (asyncio.get_event_loop().time() - start) * 1000
            if resp.status == 200:
                return HealthResult(
                    instance_name=name,
                    healthy=True,
                    elapsed_ms=elapsed,
                )
            else:
                return HealthResult(
                    instance_name=name,
                    healthy=False,
                    message=f"HTTP {resp.status}",
                    elapsed_ms=elapsed,
                )
    except Exception as exc:
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        return HealthResult(
            instance_name=name,
            healthy=False,
            message=str(exc),
            elapsed_ms=elapsed,
        )


def format_health_summary(results: list[HealthResult]) -> str:
    """Format health check results as a summary string.

    Args:
        results: List of HealthResult objects.

    Returns:
        Formatted summary string.
    """
    healthy = sum(1 for r in results if r.healthy)
    total = len(results)
    unhealthy = total - healthy

    summary = f"Health: {healthy}/{total} healthy, {unhealthy} unhealthy"
    if unhealthy > 0:
        failures = [r for r in results if not r.healthy]
        for r in failures[:5]:  # Show up to 5 failures
            summary += f"\n  - {r.instance_name}: {r.message}"

    return summary
