"""Tests for CircuitBreaker state machine (N-172 partial)."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from nerva.runtime.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


# ===================================================================
# Basic state transitions
# ===================================================================


class TestCircuitBreakerBasic:
    """Fundamental state machine behaviour."""

    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_allows_calls_when_closed(self):
        cb = CircuitBreaker()
        assert cb.is_allowed() is True

    def test_opens_after_failure_threshold(self):
        cfg = CircuitBreakerConfig(failure_threshold=3, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_rejects_calls_when_open(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        assert cb.is_allowed() is False

    def test_success_resets_failure_count(self):
        cfg = CircuitBreakerConfig(failure_threshold=3, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Two more failures should not open (count was reset)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_transitions_to_half_open_after_recovery(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=0.0)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        # With recovery_seconds=0.0, reading state immediately transitions to HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_on_success_in_half_open(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=0.0)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Manually force half-open via time mock
        with patch("nerva.runtime.circuit_breaker.time") as mock_time:
            # Make monotonic() return a time far in the future so recovery triggers
            mock_time.monotonic.return_value = cb._opened_at + 10000
            assert cb.state == CircuitState.HALF_OPEN
            assert cb.is_allowed() is True

        # Now record failure while in half-open
        cb.record_failure()
        assert cb._state == CircuitState.OPEN


# ===================================================================
# Reset
# ===================================================================


class TestCircuitBreakerReset:
    """reset() forces CLOSED state."""

    def test_reset_from_open(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_allowed() is True

    def test_reset_clears_failure_count(self):
        cfg = CircuitBreakerConfig(failure_threshold=3, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED


# ===================================================================
# Half-open probe limit
# ===================================================================


class TestHalfOpenProbes:
    """Half-open state allows limited probe calls."""

    def test_allows_max_calls_then_rejects(self):
        cfg = CircuitBreakerConfig(
            failure_threshold=1, recovery_seconds=0.0, half_open_max_calls=2,
        )
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        # recovery_seconds=0.0 -> immediate half-open
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.is_allowed() is True
        assert cb.is_allowed() is True
        assert cb.is_allowed() is False


# ===================================================================
# Thread safety
# ===================================================================


class TestCircuitBreakerThreadSafety:
    """Concurrent access must not corrupt state."""

    def test_concurrent_record_calls(self):
        cfg = CircuitBreakerConfig(failure_threshold=100, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        errors: list[Exception] = []

        def record_many(success: bool, count: int) -> None:
            try:
                for _ in range(count):
                    if success:
                        cb.record_success()
                    else:
                        cb.record_failure()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=record_many, args=(True, 500)),
            threading.Thread(target=record_many, args=(False, 500)),
            threading.Thread(target=record_many, args=(True, 500)),
            threading.Thread(target=record_many, args=(False, 500)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # State must be one of the valid states
        assert cb.state in {CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN}


# ===================================================================
# Edge cases
# ===================================================================


class TestCircuitBreakerEdgeCases:
    """Degenerate configurations and boundary values."""

    def test_threshold_of_one(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=9999)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_recovery_seconds_zero_goes_straight_to_half_open(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_seconds=0.0)
        cb = CircuitBreaker(cfg)
        cb.record_failure()
        # Immediate transition to half-open on state read
        assert cb.state == CircuitState.HALF_OPEN

    def test_default_config_values(self):
        cfg = CircuitBreakerConfig()
        assert cfg.failure_threshold == 3
        assert cfg.recovery_seconds == 60.0
        assert cfg.half_open_max_calls == 1

    def test_is_allowed_does_not_change_closed_state(self):
        cb = CircuitBreaker()
        for _ in range(100):
            assert cb.is_allowed() is True
        assert cb.state == CircuitState.CLOSED

    def test_record_success_in_closed_is_noop_on_state(self):
        cb = CircuitBreaker()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
