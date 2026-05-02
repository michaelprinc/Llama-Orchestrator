"""
Tests for backoff utilities with jitter.
"""

import random
from unittest.mock import MagicMock, patch

import pytest

from llama_orchestrator.health.backoff import (
    BackoffCalculator,
    BackoffConfig,
    HealthCheckBackoff,
    RetryHandler,
    calculate_jittered_delay,
    with_jitter,
)


# =============================================================================
# BackoffConfig Tests
# =============================================================================

class TestBackoffConfig:
    """Tests for BackoffConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = BackoffConfig()
        
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.jitter == 0.1
        assert config.multiplier == 2.0
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = BackoffConfig(
            base_delay=2.0,
            max_delay=120.0,
            jitter=0.2,
            multiplier=3.0,
        )
        
        assert config.base_delay == 2.0
        assert config.max_delay == 120.0
        assert config.jitter == 0.2
        assert config.multiplier == 3.0
    
    def test_invalid_jitter_negative(self):
        """Test that negative jitter raises error."""
        with pytest.raises(ValueError, match="jitter must be between 0 and 1"):
            BackoffConfig(jitter=-0.1)
    
    def test_invalid_jitter_too_high(self):
        """Test that jitter > 1 raises error."""
        with pytest.raises(ValueError, match="jitter must be between 0 and 1"):
            BackoffConfig(jitter=1.5)
    
    def test_invalid_base_delay(self):
        """Test that non-positive base_delay raises error."""
        with pytest.raises(ValueError, match="base_delay must be positive"):
            BackoffConfig(base_delay=0)
    
    def test_invalid_max_delay(self):
        """Test that max_delay < base_delay raises error."""
        with pytest.raises(ValueError, match="max_delay must be >= base_delay"):
            BackoffConfig(base_delay=10.0, max_delay=5.0)
    
    def test_invalid_multiplier(self):
        """Test that multiplier < 1 raises error."""
        with pytest.raises(ValueError, match="multiplier must be >= 1"):
            BackoffConfig(multiplier=0.5)


# =============================================================================
# BackoffCalculator Tests
# =============================================================================

class TestBackoffCalculator:
    """Tests for BackoffCalculator."""
    
    def test_default_calculator(self):
        """Test calculator with default config."""
        calc = BackoffCalculator()
        assert calc.attempt == 0
    
    def test_exponential_growth(self):
        """Test that delays grow exponentially."""
        config = BackoffConfig(base_delay=1.0, multiplier=2.0, jitter=0)
        calc = BackoffCalculator(config)
        
        # Get delays without jitter
        delays = calc.get_delay_sequence(5)
        
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]
    
    def test_max_delay_cap(self):
        """Test that delays are capped at max_delay."""
        config = BackoffConfig(base_delay=1.0, max_delay=5.0, multiplier=2.0, jitter=0)
        calc = BackoffCalculator(config)
        
        delays = calc.get_delay_sequence(5)
        
        assert delays == [1.0, 2.0, 4.0, 5.0, 5.0]
    
    def test_jitter_applied(self):
        """Test that jitter adds randomness."""
        config = BackoffConfig(base_delay=10.0, jitter=0.5)
        calc = BackoffCalculator(config)
        
        # With 50% jitter, delay should be between 5 and 15
        delays = [calc.calculate_delay(0) for _ in range(100)]
        
        assert all(5.0 <= d <= 15.0 for d in delays)
        # Verify there's actual variance
        assert max(delays) - min(delays) > 1.0
    
    def test_no_jitter_when_zero(self):
        """Test that no jitter is applied when jitter=0."""
        config = BackoffConfig(base_delay=10.0, jitter=0)
        calc = BackoffCalculator(config)
        
        delays = [calc.calculate_delay(0) for _ in range(10)]
        
        # All delays should be exactly the same
        assert all(d == 10.0 for d in delays)
    
    def test_next_delay_increments_attempt(self):
        """Test that next_delay increments internal counter."""
        config = BackoffConfig(base_delay=1.0, multiplier=2.0, jitter=0)
        calc = BackoffCalculator(config)
        
        assert calc.attempt == 0
        
        d1 = calc.next_delay()
        assert calc.attempt == 1
        assert d1 == 1.0
        
        d2 = calc.next_delay()
        assert calc.attempt == 2
        assert d2 == 2.0
    
    def test_reset(self):
        """Test that reset clears attempt counter."""
        calc = BackoffCalculator()
        
        calc.next_delay()
        calc.next_delay()
        assert calc.attempt == 2
        
        calc.reset()
        assert calc.attempt == 0
    
    def test_calculate_delay_with_explicit_attempt(self):
        """Test calculating delay for specific attempt."""
        config = BackoffConfig(base_delay=1.0, multiplier=2.0, jitter=0)
        calc = BackoffCalculator(config)
        
        # Explicit attempt doesn't change internal counter
        delay = calc.calculate_delay(5)
        
        assert delay == 32.0  # 1 * 2^5
        assert calc.attempt == 0  # Not changed


# =============================================================================
# RetryHandler Tests
# =============================================================================

class TestRetryHandler:
    """Tests for RetryHandler."""
    
    def test_initial_state(self):
        """Test initial handler state."""
        handler = RetryHandler(max_retries=3)
        
        assert handler.consecutive_failures == 0
        assert handler.should_retry() is True
        assert handler.is_exhausted() is False
    
    def test_record_failure(self):
        """Test recording failures."""
        handler = RetryHandler(max_retries=3)
        
        delay = handler.record_failure()
        
        assert handler.consecutive_failures == 1
        assert delay > 0
        assert handler.should_retry() is True
    
    def test_record_success(self):
        """Test that success resets counters."""
        handler = RetryHandler(max_retries=3)
        
        handler.record_failure()
        handler.record_failure()
        assert handler.consecutive_failures == 2
        
        handler.record_success()
        assert handler.consecutive_failures == 0
        assert handler.backoff.attempt == 0
    
    def test_exhausted_after_max_retries(self):
        """Test that handler is exhausted after max retries."""
        handler = RetryHandler(max_retries=3)
        
        handler.record_failure()
        handler.record_failure()
        handler.record_failure()
        
        assert handler.is_exhausted() is True
        assert handler.should_retry() is False
    
    def test_on_retry_callback(self):
        """Test that on_retry callback is called."""
        callback = MagicMock()
        handler = RetryHandler(max_retries=3, on_retry=callback)
        
        error = Exception("test error")
        handler.record_failure(error)
        
        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == 1  # attempt
        assert args[1] is error
        assert args[2] > 0  # delay


# =============================================================================
# HealthCheckBackoff Tests
# =============================================================================

class TestHealthCheckBackoff:
    """Tests for HealthCheckBackoff."""
    
    def test_initial_state(self):
        """Test initial backoff state."""
        backoff = HealthCheckBackoff()
        
        assert backoff.is_in_backoff is False
        assert backoff.current_failures == 0
    
    def test_normal_interval_on_success(self):
        """Test that success returns normal interval."""
        backoff = HealthCheckBackoff(normal_interval=10.0)
        
        interval = backoff.get_next_interval(last_success=True)
        
        assert interval == 10.0
        assert backoff.is_in_backoff is False
    
    def test_backoff_on_failure(self):
        """Test that failure triggers backoff."""
        backoff = HealthCheckBackoff(
            normal_interval=10.0,
            failure_base=1.0,
            jitter=0,
        )
        
        interval = backoff.get_next_interval(last_success=False)
        
        assert backoff.is_in_backoff is True
        assert backoff.current_failures == 1
        assert interval >= 1.0  # First failure uses base delay
    
    def test_increasing_delays_on_consecutive_failures(self):
        """Test that delays increase on consecutive failures."""
        backoff = HealthCheckBackoff(
            failure_base=1.0,
            failure_max=100.0,
            jitter=0,
        )
        
        interval1 = backoff.get_next_interval(last_success=False)
        interval2 = backoff.get_next_interval(last_success=False)
        interval3 = backoff.get_next_interval(last_success=False)
        
        assert interval1 < interval2 < interval3
    
    def test_success_resets_backoff(self):
        """Test that success resets backoff state."""
        backoff = HealthCheckBackoff(normal_interval=10.0, jitter=0)
        
        # Generate some failures
        backoff.get_next_interval(last_success=False)
        backoff.get_next_interval(last_success=False)
        assert backoff.is_in_backoff is True
        
        # Success resets
        interval = backoff.get_next_interval(last_success=True)
        
        assert interval == 10.0
        assert backoff.is_in_backoff is False
        assert backoff.current_failures == 0
    
    def test_reset_method(self):
        """Test explicit reset."""
        backoff = HealthCheckBackoff()
        
        backoff.get_next_interval(last_success=False)
        backoff.get_next_interval(last_success=False)
        
        backoff.reset()
        
        assert backoff.is_in_backoff is False
        assert backoff.current_failures == 0


# =============================================================================
# Utility Function Tests
# =============================================================================

class TestCalculateJitteredDelay:
    """Tests for calculate_jittered_delay function."""
    
    def test_basic_calculation(self):
        """Test basic delay calculation."""
        delay = calculate_jittered_delay(
            base=1.0,
            attempt=0,
            jitter=0,
        )
        assert delay == 1.0
    
    def test_exponential_growth(self):
        """Test exponential growth."""
        delay = calculate_jittered_delay(
            base=1.0,
            attempt=3,
            multiplier=2.0,
            jitter=0,
        )
        assert delay == 8.0  # 1 * 2^3
    
    def test_max_delay_cap(self):
        """Test max delay cap."""
        delay = calculate_jittered_delay(
            base=1.0,
            attempt=10,
            max_delay=10.0,
            jitter=0,
        )
        assert delay == 10.0


class TestWithJitter:
    """Tests for with_jitter function."""
    
    def test_no_jitter_when_zero(self):
        """Test no jitter when factor is 0."""
        result = with_jitter(10.0, jitter_factor=0)
        assert result == 10.0
    
    def test_jitter_applied(self):
        """Test jitter is applied."""
        results = [with_jitter(10.0, jitter_factor=0.5) for _ in range(100)]
        
        # Should have variance
        assert min(results) < 10.0
        assert max(results) > 10.0
        
        # Should be within expected range
        assert all(5.0 <= r <= 15.0 for r in results)
    
    def test_small_jitter(self):
        """Test small jitter factor."""
        results = [with_jitter(10.0, jitter_factor=0.1) for _ in range(100)]
        
        # Should be within 10% of original
        assert all(9.0 <= r <= 11.0 for r in results)


# =============================================================================
# Integration Tests
# =============================================================================

class TestBackoffIntegration:
    """Integration tests for backoff system."""
    
    def test_retry_handler_with_custom_backoff(self):
        """Test retry handler with custom backoff config."""
        config = BackoffConfig(
            base_delay=0.1,
            max_delay=1.0,
            multiplier=2.0,
            jitter=0,
        )
        calculator = BackoffCalculator(config)
        handler = RetryHandler(max_retries=5, backoff=calculator)
        
        delays = []
        while handler.should_retry():
            delay = handler.record_failure(Exception("test"))
            delays.append(delay)
        
        assert len(delays) == 5
        assert delays == [0.1, 0.2, 0.4, 0.8, 1.0]
    
    def test_health_check_backoff_respects_max(self):
        """Test that health check backoff respects max delay."""
        backoff = HealthCheckBackoff(
            failure_base=1.0,
            failure_max=5.0,
            jitter=0,
        )
        
        delays = []
        for _ in range(10):
            delay = backoff.get_next_interval(last_success=False)
            delays.append(delay)
        
        # All delays should respect max
        assert all(d <= 5.0 for d in delays)
        # Should eventually hit max
        assert delays[-1] == 5.0
