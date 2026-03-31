/**
 * Circuit breaker — prevent cascading failures by tracking handler health.
 *
 * State machine:
 *   CLOSED  (normal)  -- allow calls, count consecutive failures
 *       |  failureThreshold exceeded
 *       v
 *   OPEN    (failing) -- reject all calls
 *       |  recoveryMs elapsed
 *       v
 *   HALF_OPEN (testing) -- allow limited test calls
 *       |  success -> CLOSED  |  failure -> OPEN
 *
 * @module runtime/circuit-breaker
 */

// ---------------------------------------------------------------------------
// CircuitState
// ---------------------------------------------------------------------------

/**
 * States in the circuit breaker state machine.
 */
export const CircuitState = {
  /** Normal operation, calls flow through. */
  CLOSED: "closed",
  /** Handler is failing, calls are rejected. */
  OPEN: "open",
  /** Recovery probe, limited calls allowed. */
  HALF_OPEN: "half_open",
} as const;

export type CircuitState = (typeof CircuitState)[keyof typeof CircuitState];

// ---------------------------------------------------------------------------
// Named constants
// ---------------------------------------------------------------------------

/** Consecutive failures before the circuit opens. */
const DEFAULT_FAILURE_THRESHOLD = 3;

/** Milliseconds after opening before transitioning to half-open. */
const DEFAULT_RECOVERY_MS = 60_000;

/** Number of probe calls allowed in the half-open state. */
const DEFAULT_HALF_OPEN_MAX_CALLS = 1;

// ---------------------------------------------------------------------------
// CircuitBreakerConfig
// ---------------------------------------------------------------------------

/**
 * Tunable thresholds for a circuit breaker.
 */
export interface CircuitBreakerConfig {
  /** Consecutive failures before the circuit opens. */
  readonly failureThreshold: number;
  /** Milliseconds to wait before allowing a recovery probe. */
  readonly recoveryMs: number;
  /** Maximum probe calls permitted in half-open state. */
  readonly halfOpenMaxCalls: number;
}

/**
 * Create a CircuitBreakerConfig with defaults for any omitted fields.
 *
 * @param overrides - Partial config to merge with defaults.
 * @returns A complete CircuitBreakerConfig.
 */
export function createCircuitBreakerConfig(
  overrides?: Partial<CircuitBreakerConfig>,
): CircuitBreakerConfig {
  return {
    failureThreshold: overrides?.failureThreshold ?? DEFAULT_FAILURE_THRESHOLD,
    recoveryMs: overrides?.recoveryMs ?? DEFAULT_RECOVERY_MS,
    halfOpenMaxCalls: overrides?.halfOpenMaxCalls ?? DEFAULT_HALF_OPEN_MAX_CALLS,
  };
}

// ---------------------------------------------------------------------------
// Now provider (injectable for testing)
// ---------------------------------------------------------------------------

/**
 * Returns the current monotonic-ish timestamp in milliseconds.
 * Defaults to `performance.now()` but can be overridden for testing.
 */
type NowProvider = () => number;

// ---------------------------------------------------------------------------
// CircuitBreaker
// ---------------------------------------------------------------------------

/**
 * Per-handler circuit breaker with a state machine.
 *
 * Tracks success/failure for a specific handler. Opens the circuit after
 * `failureThreshold` consecutive failures, then transitions to half-open
 * after `recoveryMs` to test whether the handler has recovered.
 *
 * @example
 * ```ts
 * const breaker = new CircuitBreaker();
 * if (!breaker.isAllowed()) {
 *   throw new Error("circuit open");
 * }
 * try {
 *   const result = await callHandler();
 *   breaker.recordSuccess();
 * } catch {
 *   breaker.recordFailure();
 *   throw;
 * }
 * ```
 */
export class CircuitBreaker {
  private readonly _config: CircuitBreakerConfig;
  private readonly _now: NowProvider;
  private _state: CircuitState;
  private _consecutiveFailures: number;
  private _openedAt: number;
  private _halfOpenCalls: number;

  /**
   * Create a new circuit breaker in the CLOSED state.
   *
   * @param config - Optional thresholds. Uses defaults when omitted.
   * @param nowProvider - Optional time source for testing. Defaults to `performance.now`.
   */
  constructor(config?: Partial<CircuitBreakerConfig>, nowProvider?: NowProvider) {
    this._config = createCircuitBreakerConfig(config);
    this._now = nowProvider ?? (() => performance.now());
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
    this._openedAt = 0;
    this._halfOpenCalls = 0;
  }

  /**
   * Return the current circuit state, applying time-based transitions.
   *
   * @returns The live circuit state after evaluating recovery timeout.
   */
  get state(): CircuitState {
    this._maybeTransitionToHalfOpen();
    return this._state;
  }

  /**
   * Check if a call is allowed under the current circuit state.
   *
   * @returns `true` if the call may proceed, `false` if the circuit is open.
   */
  isAllowed(): boolean {
    this._maybeTransitionToHalfOpen();
    return this._isAllowedInternal();
  }

  /**
   * Record a successful call.
   *
   * Resets the failure counter. If the circuit is half-open, closes it.
   */
  recordSuccess(): void {
    this._consecutiveFailures = 0;
    this._halfOpenCalls = 0;

    if (this._state === CircuitState.HALF_OPEN) {
      this._state = CircuitState.CLOSED;
    }
  }

  /**
   * Record a failed call.
   *
   * Increments the consecutive failure counter. Opens the circuit when
   * the failure threshold is reached. Re-opens immediately if called
   * in the half-open state.
   */
  recordFailure(): void {
    this._consecutiveFailures += 1;

    if (this._state === CircuitState.HALF_OPEN) {
      this._open();
      return;
    }

    if (this._consecutiveFailures >= this._config.failureThreshold) {
      this._open();
    }
  }

  /**
   * Force-reset the circuit to the CLOSED state.
   *
   * Clears all failure counts and timers.
   */
  reset(): void {
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
    this._openedAt = 0;
    this._halfOpenCalls = 0;
  }

  // -- Private helpers ------------------------------------------------------

  /** Transition to OPEN and record the opening timestamp. */
  private _open(): void {
    this._state = CircuitState.OPEN;
    this._openedAt = this._now();
    this._halfOpenCalls = 0;
  }

  /** Move from OPEN to HALF_OPEN if recoveryMs have elapsed. */
  private _maybeTransitionToHalfOpen(): void {
    if (this._state !== CircuitState.OPEN) return;

    const elapsed = this._now() - this._openedAt;
    if (elapsed >= this._config.recoveryMs) {
      this._state = CircuitState.HALF_OPEN;
      this._halfOpenCalls = 0;
    }
  }

  /** Evaluate whether a call is permitted under current state. */
  private _isAllowedInternal(): boolean {
    if (this._state === CircuitState.CLOSED) return true;
    if (this._state === CircuitState.OPEN) return false;

    // HALF_OPEN: allow up to halfOpenMaxCalls probe calls
    if (this._halfOpenCalls < this._config.halfOpenMaxCalls) {
      this._halfOpenCalls += 1;
      return true;
    }

    return false;
  }
}
