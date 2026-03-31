import { describe, it, expect } from "vitest";
import {
  CircuitBreaker,
  CircuitState,
  createCircuitBreakerConfig,
} from "../src/runtime/circuit-breaker.js";

// ---------------------------------------------------------------------------
// createCircuitBreakerConfig
// ---------------------------------------------------------------------------

describe("createCircuitBreakerConfig", () => {
  it("fills defaults when no overrides given", () => {
    const cfg = createCircuitBreakerConfig();
    expect(cfg.failureThreshold).toBe(3);
    expect(cfg.recoveryMs).toBe(60_000);
    expect(cfg.halfOpenMaxCalls).toBe(1);
  });

  it("respects partial overrides", () => {
    const cfg = createCircuitBreakerConfig({ failureThreshold: 5 });
    expect(cfg.failureThreshold).toBe(5);
    expect(cfg.recoveryMs).toBe(60_000);
  });
});

// ---------------------------------------------------------------------------
// CircuitBreaker basic states
// ---------------------------------------------------------------------------

describe("CircuitBreaker state machine", () => {
  it("starts in CLOSED state", () => {
    const cb = new CircuitBreaker();
    expect(cb.state).toBe(CircuitState.CLOSED);
  });

  it("allows calls in CLOSED state", () => {
    const cb = new CircuitBreaker();
    expect(cb.isAllowed()).toBe(true);
  });

  it("stays CLOSED when failures are below threshold", () => {
    const cb = new CircuitBreaker({ failureThreshold: 3 });
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.CLOSED);
    expect(cb.isAllowed()).toBe(true);
  });

  it("transitions to OPEN when failure threshold is reached", () => {
    const cb = new CircuitBreaker({ failureThreshold: 3 });
    cb.recordFailure();
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.OPEN);
  });

  it("rejects calls in OPEN state", () => {
    const cb = new CircuitBreaker({ failureThreshold: 1 });
    cb.recordFailure();
    expect(cb.isAllowed()).toBe(false);
  });

  it("transitions from OPEN to HALF_OPEN after recoveryMs", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 100 },
      () => now,
    );
    cb.recordFailure(); // opens at now=0
    expect(cb.state).toBe(CircuitState.OPEN);

    now = 50;
    expect(cb.state).toBe(CircuitState.OPEN);

    now = 100;
    expect(cb.state).toBe(CircuitState.HALF_OPEN);
  });

  it("allows limited probe calls in HALF_OPEN state", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 100, halfOpenMaxCalls: 1 },
      () => now,
    );
    cb.recordFailure();
    now = 100;
    expect(cb.isAllowed()).toBe(true); // probe call
    expect(cb.isAllowed()).toBe(false); // exceeded halfOpenMaxCalls
  });

  it("transitions from HALF_OPEN to CLOSED on success", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 100 },
      () => now,
    );
    cb.recordFailure();
    now = 100;
    expect(cb.state).toBe(CircuitState.HALF_OPEN);
    cb.recordSuccess();
    expect(cb.state).toBe(CircuitState.CLOSED);
  });

  it("transitions from HALF_OPEN back to OPEN on failure", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 100 },
      () => now,
    );
    cb.recordFailure();
    now = 100;
    expect(cb.state).toBe(CircuitState.HALF_OPEN);
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.OPEN);
  });

  it("recordSuccess resets failure counter in CLOSED state", () => {
    const cb = new CircuitBreaker({ failureThreshold: 3 });
    cb.recordFailure();
    cb.recordFailure();
    cb.recordSuccess();
    // after success, failures reset — need 3 more to open
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.CLOSED);
  });
});

// ---------------------------------------------------------------------------
// reset()
// ---------------------------------------------------------------------------

describe("CircuitBreaker.reset", () => {
  it("resets OPEN circuit to CLOSED", () => {
    const cb = new CircuitBreaker({ failureThreshold: 1 });
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.OPEN);
    cb.reset();
    expect(cb.state).toBe(CircuitState.CLOSED);
    expect(cb.isAllowed()).toBe(true);
  });

  it("resets HALF_OPEN circuit to CLOSED", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 100 },
      () => now,
    );
    cb.recordFailure();
    now = 100;
    expect(cb.state).toBe(CircuitState.HALF_OPEN);
    cb.reset();
    expect(cb.state).toBe(CircuitState.CLOSED);
  });

  it("is safe to call on an already-CLOSED circuit", () => {
    const cb = new CircuitBreaker();
    cb.reset();
    expect(cb.state).toBe(CircuitState.CLOSED);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("CircuitBreaker edge cases", () => {
  it("threshold=1 opens on first failure", () => {
    const cb = new CircuitBreaker({ failureThreshold: 1 });
    cb.recordFailure();
    expect(cb.state).toBe(CircuitState.OPEN);
  });

  it("recoveryMs=0 transitions immediately from OPEN to HALF_OPEN", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 0 },
      () => now,
    );
    cb.recordFailure();
    // Even at the same timestamp, elapsed >= recoveryMs (0 >= 0) is true
    expect(cb.state).toBe(CircuitState.HALF_OPEN);
  });

  it("multiple resets do not corrupt state", () => {
    const cb = new CircuitBreaker({ failureThreshold: 1 });
    cb.recordFailure();
    cb.reset();
    cb.reset();
    cb.reset();
    expect(cb.state).toBe(CircuitState.CLOSED);
    expect(cb.isAllowed()).toBe(true);
  });

  it("half-open allows exactly halfOpenMaxCalls probe calls", () => {
    let now = 0;
    const cb = new CircuitBreaker(
      { failureThreshold: 1, recoveryMs: 10, halfOpenMaxCalls: 3 },
      () => now,
    );
    cb.recordFailure();
    now = 10;
    expect(cb.isAllowed()).toBe(true);
    expect(cb.isAllowed()).toBe(true);
    expect(cb.isAllowed()).toBe(true);
    expect(cb.isAllowed()).toBe(false);
  });
});
