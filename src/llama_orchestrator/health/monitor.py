"""
Health monitoring service for llama-orchestrator.

Provides background health monitoring with auto-restart support.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from llama_orchestrator.config import discover_instances, get_instance_config
from llama_orchestrator.engine.process import restart_instance
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceStatus,
    load_state,
    save_state,
)
from llama_orchestrator.health.checker import (
    HealthCheckResult,
    HealthCheckStatus,
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
        state.health = new_health
        state.last_health_check = time.time()
        save_state(state)
        
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
        # Skip if restart policy is disabled
        if not config.restart.enabled:
            return False
        
        # Skip if still in start period
        if health_state.in_start_period:
            return False
        
        # Check consecutive failure threshold
        if health_state.consecutive_failures < config.healthcheck.retries:
            return False
        
        # Check max restart attempts
        if health_state.restart_attempts >= config.restart.max_retries:
            logger.warning(
                f"Instance {name} exceeded max restart attempts "
                f"({config.restart.max_retries})"
            )
            return False
        
        # Check backoff delay
        if health_state.last_restart_time:
            delay = self._calculate_backoff(
                health_state.restart_attempts,
                config.restart.initial_delay,
                config.restart.backoff_multiplier,
                config.restart.max_delay,
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
        """Trigger a restart for an unhealthy instance."""
        logger.info(
            f"Restarting instance {name} after {health_state.consecutive_failures} "
            f"consecutive failures (attempt {health_state.restart_attempts + 1})"
        )
        
        try:
            restart_instance(name)
            health_state.restart_attempts += 1
            health_state.last_restart_time = time.time()
            health_state.consecutive_failures = 0
            health_state.in_start_period = True
            
            if self.on_restart:
                try:
                    self.on_restart(name, health_state.restart_attempts)
                except Exception as e:
                    logger.error(f"Error in on_restart callback: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to restart instance {name}: {e}")
            health_state.restart_attempts += 1
    
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
) -> HealthMonitor:
    """
    Start the global health monitor.
    
    Args:
        interval: Seconds between health checks
        on_health_change: Callback for health state changes
        on_restart: Callback when an instance is restarted
        
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
