"""
Backoff utilities with jitter for health monitoring.

Implements exponential backoff with jitter to prevent thundering herd
problems when multiple instances need to reconnect simultaneously.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff."""
    
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.1  # 0.0 = no jitter, 1.0 = full jitter
    multiplier: float = 2.0
    
    def __post_init__(self):
        """Validate configuration."""
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")
        if self.base_delay <= 0:
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")


class BackoffCalculator:
    """
    Calculates exponential backoff delays with jitter.
    
    The jitter helps prevent thundering herd problems when many
    instances fail and try to reconnect at the same time.
    
    Formula:
        delay = min(base * (multiplier ^ attempt), max_delay)
        jittered_delay = delay * (1 - jitter + random * jitter * 2)
    """
    
    def __init__(self, config: Optional[BackoffConfig] = None):
        """
        Initialize backoff calculator.
        
        Args:
            config: Backoff configuration, or use defaults
        """
        self.config = config or BackoffConfig()
        self._attempt = 0
    
    @property
    def attempt(self) -> int:
        """Current attempt number (0-based)."""
        return self._attempt
    
    def reset(self) -> None:
        """Reset attempt counter to zero."""
        self._attempt = 0
    
    def calculate_delay(self, attempt: Optional[int] = None) -> float:
        """
        Calculate delay for a given attempt.
        
        Args:
            attempt: Attempt number (0-based). If None, uses internal counter.
            
        Returns:
            Delay in seconds with jitter applied
        """
        if attempt is None:
            attempt = self._attempt
        
        # Calculate base exponential delay
        delay = self.config.base_delay * (self.config.multiplier ** attempt)
        
        # Cap at max_delay
        delay = min(delay, self.config.max_delay)
        
        # Apply jitter
        if self.config.jitter > 0:
            # Full jitter: delay * random(0, jitter)
            # This gives range [delay * (1-jitter), delay * (1+jitter)]
            jitter_range = delay * self.config.jitter
            delay = delay + random.uniform(-jitter_range, jitter_range)
            # Ensure non-negative
            delay = max(0.1, delay)
        
        return delay
    
    def next_delay(self) -> float:
        """
        Get delay for next attempt and increment counter.
        
        Returns:
            Delay in seconds for the next attempt
        """
        delay = self.calculate_delay()
        self._attempt += 1
        return delay
    
    def get_delay_sequence(self, count: int) -> list[float]:
        """
        Get a sequence of delays for debugging/display.
        
        Args:
            count: Number of delays to calculate
            
        Returns:
            List of delay values (without jitter for reproducibility)
        """
        delays = []
        for i in range(count):
            delay = self.config.base_delay * (self.config.multiplier ** i)
            delays.append(min(delay, self.config.max_delay))
        return delays


class RetryHandler:
    """
    Handles retry logic with backoff for health checks.
    
    Provides a clean interface for retrying operations with
    exponential backoff and customizable error handling.
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        backoff: Optional[BackoffCalculator] = None,
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    ):
        """
        Initialize retry handler.
        
        Args:
            max_retries: Maximum number of retry attempts
            backoff: Backoff calculator instance
            on_retry: Callback called on each retry (attempt, exception, delay)
        """
        self.max_retries = max_retries
        self.backoff = backoff or BackoffCalculator()
        self.on_retry = on_retry
        self._consecutive_failures = 0
    
    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failures."""
        return self._consecutive_failures
    
    def record_success(self) -> None:
        """Record a successful attempt, resetting counters."""
        self._consecutive_failures = 0
        self.backoff.reset()
    
    def record_failure(self, error: Optional[Exception] = None) -> float:
        """
        Record a failure and get next backoff delay.
        
        Args:
            error: Optional exception that caused the failure
            
        Returns:
            Delay before next retry attempt in seconds
        """
        self._consecutive_failures += 1
        delay = self.backoff.next_delay()
        
        if self.on_retry and error:
            self.on_retry(self._consecutive_failures, error, delay)
        
        return delay
    
    def should_retry(self) -> bool:
        """Check if more retries are available."""
        return self._consecutive_failures < self.max_retries
    
    def is_exhausted(self) -> bool:
        """Check if all retries have been exhausted."""
        return self._consecutive_failures >= self.max_retries


@dataclass
class HealthCheckBackoff:
    """
    Specialized backoff for health check monitoring.
    
    Tracks check intervals with increasing delays on failures.
    """
    
    normal_interval: float = 10.0
    failure_base: float = 1.0
    failure_max: float = 60.0
    jitter: float = 0.1
    
    _failures: int = 0
    _calculator: Optional[BackoffCalculator] = None
    
    def __post_init__(self):
        """Initialize calculator."""
        self._calculator = BackoffCalculator(
            BackoffConfig(
                base_delay=self.failure_base,
                max_delay=self.failure_max,
                jitter=self.jitter,
            )
        )
    
    def get_next_interval(self, last_success: bool) -> float:
        """
        Get the next check interval based on last result.
        
        Args:
            last_success: Whether the last check was successful
            
        Returns:
            Seconds until next check
        """
        if last_success:
            self._failures = 0
            self._calculator.reset()
            return self.normal_interval
        
        self._failures += 1
        return self._calculator.next_delay()
    
    def reset(self) -> None:
        """Reset to normal interval."""
        self._failures = 0
        self._calculator.reset()
    
    @property
    def is_in_backoff(self) -> bool:
        """Check if currently in backoff mode due to failures."""
        return self._failures > 0
    
    @property
    def current_failures(self) -> int:
        """Number of consecutive failures."""
        return self._failures


def calculate_jittered_delay(
    base: float,
    attempt: int = 0,
    max_delay: float = 60.0,
    multiplier: float = 2.0,
    jitter: float = 0.1,
) -> float:
    """
    Convenience function to calculate a single jittered delay.
    
    Args:
        base: Base delay in seconds
        attempt: Attempt number (0-based)
        max_delay: Maximum delay cap
        multiplier: Exponential multiplier
        jitter: Jitter factor (0-1)
        
    Returns:
        Calculated delay with jitter
    """
    config = BackoffConfig(
        base_delay=base,
        max_delay=max_delay,
        multiplier=multiplier,
        jitter=jitter,
    )
    calc = BackoffCalculator(config)
    return calc.calculate_delay(attempt)


def with_jitter(delay: float, jitter_factor: float = 0.1) -> float:
    """
    Add jitter to a delay value.
    
    Args:
        delay: Original delay in seconds
        jitter_factor: Jitter factor (0-1)
        
    Returns:
        Delay with random jitter applied
    """
    if jitter_factor <= 0:
        return delay
    
    jitter_range = delay * jitter_factor
    return delay + random.uniform(-jitter_range, jitter_range)
