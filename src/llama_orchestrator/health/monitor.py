"""
Health monitoring service for llama-orchestrator.

Provides background health monitoring with auto-restart support.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from llama_orchestrator.config import discover_instances, get_instance_config
from llama_orchestrator.engine.process import PortCollisionError, restart_instance
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    RuntimeState,
    load_runtime,
    load_state,
    log_event,
    record_health_check,
    save_runtime,
    save_state,
)
from llama_orchestrator.health.checker import (
    HealthCheckResult,
    check_instance_health,
)

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig

logger = logging.getLogger(__name__)


@dataclass
class InstanceHealthState:
    """Tracks health state for an instance."""

    name: str
    consecutive_failures: int = 0
    last_check_time: float | None = None
    last_result: HealthCheckResult | None = None
    restart_attempts: int = 0
    last_restart_time: float | None = None
    in_start_period: bool = True


@dataclass
class HealthMonitor:
    """
    Background health monitor for llama.cpp instances.

    Periodically checks health of running instances and triggers
    auto-restart when configured.
    """

    check_interval: float = 10.0  # Seconds between checks
    on_health_change: Callable[[str, HealthStatus, HealthStatus], None] | None = None
    on_restart: Callable[[str, int], None] | None = None
    # R11: automatic re-porting on collision is OPT-IN (default off). The
    # default policy marks the instance ERROR, emits an event, and lets the
    # configured restart policy (retries/backoff) drive further attempts.
    reassign_ports_on_collision: bool = False

    _running: bool = field(default=False, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _instance_states: dict[str, InstanceHealthState] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def start(self) -> None:
        """Start the health monitoring thread."""
        if self._running:
            logger.warning("Health monitor already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Health monitor started")

    def stop(self) -> None:
        """Stop the health monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Health monitor stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_all_instances()
            except Exception as e:
                logger.error(f"Error in health monitor loop: {e}")

            # Sleep with interruptible check
            sleep_end = time.time() + self.check_interval
            while self._running and time.time() < sleep_end:
                time.sleep(0.5)

    def _check_all_instances(self) -> None:
        """Check health of all running instances."""
        instances = discover_instances()

        for name, _ in instances:
            if not self._running:
                break

            try:
                self._check_instance(name)
            except Exception as e:
                logger.error(f"Error checking instance {name}: {e}")

    def _check_instance(self, name: str) -> None:
        """Check health of a single instance."""
        # Load current state
        state = load_state(name)
        if state is None or state.status != InstanceStatus.RUNNING:
            # Skip non-running instances
            return

        # Get or create health tracking state
        with self._lock:
            if name not in self._instance_states:
                self._instance_states[name] = InstanceHealthState(name=name)
            health_state = self._instance_states[name]

        # Load config for health check settings
        try:
            config = get_instance_config(name)
        except FileNotFoundError:
            logger.warning(f"Config not found for instance {name}")
            return

        # Check if still in start period
        if state.start_time:
            elapsed = time.time() - state.start_time
            health_state.in_start_period = elapsed < config.healthcheck.start_period

        # Perform health check
        result = check_instance_health(name, timeout=config.healthcheck.timeout)
        health_state.last_check_time = time.time()
        health_state.last_result = result

        # Update state based on result
        old_health = state.health
        new_health = result.to_health_status

        if result.is_healthy:
            health_state.consecutive_failures = 0
            health_state.restart_attempts = 0
        elif result.is_loading:
            # Loading is expected during startup
            if not health_state.in_start_period:
                health_state.consecutive_failures += 1
        else:
            health_state.consecutive_failures += 1

        # Update state in database
        checked_at = time.time()
        state.health = new_health
        state.last_health_check = checked_at
        save_state(state)

        runtime = load_runtime(name) or RuntimeState(name=name)
        runtime.pid = state.pid
        runtime.port = config.server.port
        runtime.status = state.status
        runtime.health = new_health
        runtime.started_at = runtime.started_at or state.start_time
        runtime.last_seen_at = checked_at
        runtime.restart_attempts = state.restart_count
        if result.is_healthy:
            runtime.last_health_ok_at = checked_at
            runtime.last_error = ""
        elif result.error_message:
            runtime.last_error = result.error_message
        save_runtime(runtime)
        record_health_check(
            name,
            new_health,
            response_time_ms=result.response_time_ms,
            error_message=result.error_message or "",
        )

        # Notify on health change
        if old_health != new_health and self.on_health_change:
            try:
                self.on_health_change(name, old_health, new_health)
            except Exception as e:
                logger.error(f"Error in on_health_change callback: {e}")

        # Check if restart is needed
        if self._should_restart(name, config, health_state):
            self._trigger_restart(name, config, health_state)

    def _should_restart(
        self,
        name: str,
        config: InstanceConfig,
        health_state: InstanceHealthState,
    ) -> bool:
        """Check if an instance should be restarted."""
        restart_policy = config.restart_policy

        # Skip if restart policy is disabled
        if not restart_policy.enabled:
            return False

        # Skip if still in start period
        if health_state.in_start_period:
            return False

        # Check consecutive failure threshold
        if health_state.consecutive_failures < config.healthcheck.retries:
            return False

        # Check max restart attempts
        if health_state.restart_attempts >= restart_policy.max_retries:
            logger.warning(
                f"Instance {name} exceeded max restart attempts "
                f"({restart_policy.max_retries})"
            )
            return False

        # Check backoff delay
        if health_state.last_restart_time:
            delay = self._calculate_backoff(
                health_state.restart_attempts,
                restart_policy.initial_delay,
                restart_policy.backoff_multiplier,
                restart_policy.max_delay,
            )
            elapsed = time.time() - health_state.last_restart_time
            if elapsed < delay:
                return False

        return True

    def _calculate_backoff(
        self,
        attempt: int,
        initial_delay: float,
        multiplier: float,
        max_delay: float,
    ) -> float:
        """Calculate exponential backoff delay."""
        delay = initial_delay * (multiplier ** attempt)
        return min(delay, max_delay)

    def _trigger_restart(
        self,
        name: str,
        config: InstanceConfig,
        health_state: InstanceHealthState,
    ) -> None:
        """Trigger a restart for an unhealthy instance with port collision handling.

        R11: collisions are detected via the structured ``PortCollisionError``
        (never message substrings) and routed to ``_handle_port_collision``,
        which applies the defined policy: ERROR state + event + configured
        restart policy, with automatic re-porting only when opted in.
        """
        logger.info(
            f"Restarting instance {name} after {health_state.consecutive_failures} "
            f"consecutive failures (attempt {health_state.restart_attempts + 1})"
        )

        try:
            restart_instance(name, wait_for_ready=False)
            health_state.restart_attempts += 1
            health_state.last_restart_time = time.time()
            health_state.consecutive_failures = 0
            health_state.in_start_period = True

            if self.on_restart:
                try:
                    self.on_restart(name, health_state.restart_attempts)
                except Exception as e:
                    logger.error(f"Error in on_restart callback: {e}")

        except PortCollisionError as e:
            logger.error(f"Port collision detected for instance {name}: {e}")
            self._handle_port_collision(name, config, health_state)

        except Exception as e:
            logger.error(f"Failed to restart instance {name}: {e}")
            health_state.restart_attempts += 1

    def _handle_port_collision(
        self,
        name: str,
        config: InstanceConfig,
        health_state: InstanceHealthState,
    ) -> None:
        """R11 port-collision policy.

        Default (``reassign_ports_on_collision=False``): mark the instance
        ERROR, emit an error-level event (surfaced by ``describe``/GUI), and
        count the attempt against the configured restart policy so that
        max_retries and backoff keep governing subsequent cycles.

        Opt-in (``reassign_ports_on_collision=True``): additionally attempt a
        one-shot restart on a free port via config override. The override is
        NOT persisted to the instance's config file; until the operator
        updates the config, health checks keep probing the configured port.
        """
        from llama_orchestrator.health.ports import suggest_port_for_instance

        current_port = config.server.port

        # 1. Mark the instance ERROR. The failed start already persisted this
        #    state; making it explicit keeps the policy self-contained.
        state = load_state(name)
        if state is not None:
            state.status = InstanceStatus.ERROR
            state.health = HealthStatus.ERROR
            state.error_message = f"Port {current_port} is in use"
            save_state(state)

        # 2. Emit the event (visible in describe / GUI activity log).
        log_event(
            event_type="port_collision",
            message=(
                f"Port collision: port {current_port} is in use; "
                f"instance marked ERROR"
            ),
            instance_name=name,
            level="error",
            meta={
                "port": current_port,
                "reassign_enabled": self.reassign_ports_on_collision,
            },
        )

        # 3. Apply the configured restart policy: count this as an attempt so
        #    backoff and max_retries apply to subsequent cycles. The instance
        #    is still failing, so consecutive_failures stays elevated.
        health_state.restart_attempts += 1
        health_state.last_restart_time = time.time()

        if not self.reassign_ports_on_collision:
            logger.warning(
                f"Port collision for instance {name}: port {current_port} is in use; "
                f"keeping ERROR state (automatic re-porting is disabled by default; "
                f"use HealthMonitor(reassign_ports_on_collision=True) to enable it)."
            )
            return

        # 4. Opt-in automatic re-porting: one-shot restart on a free port.
        new_port = suggest_port_for_instance(
            name,
            preferred_port=current_port,
            port_range=(8080, 9000),
        )
        if new_port is None or new_port == current_port:
            logger.error(
                f"No alternative free port found for instance {name}; "
                f"keeping ERROR state."
            )
            return

        override = config.model_copy(
            update={"server": config.server.model_copy(update={"port": new_port})}
        )
        try:
            restart_instance(name, wait_for_ready=False, config_override=override)
        except Exception as e:
            logger.error(f"Re-port restart of instance {name} to port {new_port} failed: {e}")
            return

        log_event(
            event_type="port_reassigned",
            message=(
                f"Instance restarted on port {new_port} after collision "
                f"(configured port: {current_port})"
            ),
            instance_name=name,
            level="warning",
            meta={"from": current_port, "to": new_port},
        )
        logger.warning(
            f"Port collision for instance {name}: restarted on port {new_port} "
            f"(configured port was {current_port}); update the config to persist it."
        )

    def get_instance_health(self, name: str) -> InstanceHealthState | None:
        """Get the health tracking state for an instance."""
        with self._lock:
            return self._instance_states.get(name)

    @property
    def is_running(self) -> bool:
        """Check if the monitor is running."""
        return self._running


# Module-level monitor instance
_monitor: HealthMonitor | None = None
_monitor_lock = threading.Lock()


def start_monitoring(
    interval: float = 10.0,
    on_health_change: Callable[[str, HealthStatus, HealthStatus], None] | None = None,
    on_restart: Callable[[str, int], None] | None = None,
    reassign_ports_on_collision: bool = False,
) -> HealthMonitor:
    """
    Start the global health monitor.

    Args:
        interval: Seconds between health checks
        on_health_change: Callback for health state changes
        on_restart: Callback when an instance is restarted
        reassign_ports_on_collision: Opt-in automatic re-porting on port
            collision (R11; default False keeps the ERROR+event policy)

    Returns:
        The HealthMonitor instance
    """
    global _monitor

    with _monitor_lock:
        if _monitor is not None and _monitor.is_running:
            return _monitor

        _monitor = HealthMonitor(
            check_interval=interval,
            on_health_change=on_health_change,
            on_restart=on_restart,
            reassign_ports_on_collision=reassign_ports_on_collision,
        )
        _monitor.start()
        return _monitor


def stop_monitoring() -> None:
    """Stop the global health monitor."""
    global _monitor

    with _monitor_lock:
        if _monitor is not None:
            _monitor.stop()
            _monitor = None


def get_monitor() -> HealthMonitor | None:
    """Get the current health monitor instance."""
    return _monitor
