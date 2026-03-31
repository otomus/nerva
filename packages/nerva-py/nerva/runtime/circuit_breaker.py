"""Circuit breaker — prevent cascading failures by tracking handler health.

State machine:
    CLOSED  (normal)  -- allow calls, count consecutive failures
        |  failure_threshold exceeded
        v
    OPEN    (failing) -- reject all calls
        |  recovery_seconds elapsed
        v
    HALF_OPEN (testing) -- allow limited test calls
        |  success -> CLOSED  |  failure -> OPEN
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["CircuitState", "CircuitBreakerConfig", "CircuitBreaker"]


class CircuitState(StrEnum):
    """States in the circuit breaker state machine.

    Members:
        CLOSED: Normal operation, calls flow through.
        OPEN: Handler is failing, calls are rejected.
        HALF_OPEN: Recovery probe, limited calls allowed.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

DEFAULT_FAILURE_THRESHOLD = 3
"""Consecutive failures before the circuit opens."""

DEFAULT_RECOVERY_SECONDS = 60.0
"""Seconds after opening before transitioning to half-open."""

DEFAULT_HALF_OPEN_MAX_CALLS = 1
"""Number of probe calls allowed in the half-open state."""


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Tunable thresholds for a circuit breaker.

    Attributes:
        failure_threshold: Consecutive failures before the circuit opens.
        recovery_seconds: Seconds to wait before allowing a recovery probe.
        half_open_max_calls: Maximum probe calls permitted in half-open state.
    """

    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    recovery_seconds: float = DEFAULT_RECOVERY_SECONDS
    half_open_max_calls: int = DEFAULT_HALF_OPEN_MAX_CALLS


class CircuitBreaker:
    """Per-handler circuit breaker with a thread-safe state machine.

    Tracks success/failure for a specific handler. Opens the circuit after
    ``failure_threshold`` consecutive failures, then transitions to half-open
    after ``recovery_seconds`` to test whether the handler has recovered.

    Example::

        breaker = CircuitBreaker()
        if not breaker.is_allowed():
            raise RuntimeError("circuit open")
        try:
            result = await call_handler()
            breaker.record_success()
        except Exception:
            breaker.record_failure()
            raise
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        """Create a new circuit breaker in the CLOSED state.

        Args:
            config: Optional thresholds. Uses defaults when None.
        """
        self._config = config or CircuitBreakerConfig()
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_calls = 0

    # -- public read-only properties ----------------------------------------

    @property
    def state(self) -> CircuitState:
        """Return the current circuit state, applying time-based transitions.

        Returns:
            The live circuit state after evaluating recovery timeout.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    # -- public mutation methods --------------------------------------------

    def is_allowed(self) -> bool:
        """Check if a call is allowed under the current circuit state.

        Returns:
            True if the call may proceed, False if the circuit is open.
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._is_allowed_locked()

    def record_success(self) -> None:
        """Record a successful call.

        Resets the failure counter. If the circuit is half-open, closes it.
        """
        with self._lock:
            self._consecutive_failures = 0
            self._half_open_calls = 0

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call.

        Increments the consecutive failure counter. Opens the circuit when
        the failure threshold is reached. Re-opens immediately if called
        in the half-open state.
        """
        with self._lock:
            self._consecutive_failures += 1

            if self._state == CircuitState.HALF_OPEN:
                self._open_locked()
                return

            if self._consecutive_failures >= self._config.failure_threshold:
                self._open_locked()

    def reset(self) -> None:
        """Force-reset the circuit to the CLOSED state.

        Clears all failure counts and timers.
        """
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = 0.0
            self._half_open_calls = 0

    # -- private helpers (must be called with _lock held) -------------------

    def _open_locked(self) -> None:
        """Transition to OPEN and record the opening timestamp."""
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_calls = 0

    def _maybe_transition_to_half_open(self) -> None:
        """Move from OPEN to HALF_OPEN if recovery_seconds have elapsed."""
        if self._state != CircuitState.OPEN:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self._config.recovery_seconds:
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0

    def _is_allowed_locked(self) -> bool:
        """Evaluate whether a call is permitted under current state."""
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            return False

        # HALF_OPEN: allow up to half_open_max_calls probe calls
        if self._half_open_calls < self._config.half_open_max_calls:
            self._half_open_calls += 1
            return True

        return False
