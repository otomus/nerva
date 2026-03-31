package runtime

import (
	"sync"
	"time"
)

// CircuitState represents the state of a circuit breaker.
type CircuitState string

const (
	// CircuitClosed means normal operation, calls flow through.
	CircuitClosed CircuitState = "closed"
	// CircuitOpen means the handler is failing, calls are rejected.
	CircuitOpen CircuitState = "open"
	// CircuitHalfOpen means recovery probe, limited calls allowed.
	CircuitHalfOpen CircuitState = "half_open"
)

const (
	// DefaultFailureThreshold is consecutive failures before the circuit opens.
	DefaultFailureThreshold = 3
	// DefaultRecoverySeconds is seconds after opening before transitioning to half-open.
	DefaultRecoverySeconds = 60.0
	// DefaultHalfOpenMaxCalls is probe calls allowed in the half-open state.
	DefaultHalfOpenMaxCalls = 1
)

// CircuitBreakerConfig holds tunable thresholds for a circuit breaker.
type CircuitBreakerConfig struct {
	FailureThreshold int
	RecoverySeconds  float64
	HalfOpenMaxCalls int
}

// DefaultCircuitBreakerConfig returns a config with default values.
func DefaultCircuitBreakerConfig() CircuitBreakerConfig {
	return CircuitBreakerConfig{
		FailureThreshold: DefaultFailureThreshold,
		RecoverySeconds:  DefaultRecoverySeconds,
		HalfOpenMaxCalls: DefaultHalfOpenMaxCalls,
	}
}

// CircuitBreaker tracks handler health and prevents cascading failures.
type CircuitBreaker struct {
	mu                  sync.Mutex
	config              CircuitBreakerConfig
	state               CircuitState
	consecutiveFailures int
	openedAt            time.Time
	halfOpenCalls       int
}

// NewCircuitBreaker creates a new circuit breaker in the CLOSED state.
func NewCircuitBreaker(config *CircuitBreakerConfig) *CircuitBreaker {
	cfg := DefaultCircuitBreakerConfig()
	if config != nil {
		cfg = *config
	}
	return &CircuitBreaker{
		config: cfg,
		state:  CircuitClosed,
	}
}

// State returns the current circuit state, applying time-based transitions.
func (cb *CircuitBreaker) State() CircuitState {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.maybeTransitionToHalfOpen()
	return cb.state
}

// IsAllowed checks if a call is allowed under the current circuit state.
func (cb *CircuitBreaker) IsAllowed() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.maybeTransitionToHalfOpen()
	return cb.isAllowedLocked()
}

// RecordSuccess records a successful call. Resets failure counter and
// closes the circuit if it was half-open.
func (cb *CircuitBreaker) RecordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.consecutiveFailures = 0
	cb.halfOpenCalls = 0
	if cb.state == CircuitHalfOpen {
		cb.state = CircuitClosed
	}
}

// RecordFailure records a failed call. Increments consecutive failures
// and opens the circuit when the threshold is reached.
func (cb *CircuitBreaker) RecordFailure() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.consecutiveFailures++

	if cb.state == CircuitHalfOpen {
		cb.openLocked()
		return
	}

	if cb.consecutiveFailures >= cb.config.FailureThreshold {
		cb.openLocked()
	}
}

// Reset force-resets the circuit to the CLOSED state.
func (cb *CircuitBreaker) Reset() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.state = CircuitClosed
	cb.consecutiveFailures = 0
	cb.openedAt = time.Time{}
	cb.halfOpenCalls = 0
}

func (cb *CircuitBreaker) openLocked() {
	cb.state = CircuitOpen
	cb.openedAt = time.Now()
	cb.halfOpenCalls = 0
}

func (cb *CircuitBreaker) maybeTransitionToHalfOpen() {
	if cb.state != CircuitOpen {
		return
	}
	elapsed := time.Since(cb.openedAt).Seconds()
	if elapsed >= cb.config.RecoverySeconds {
		cb.state = CircuitHalfOpen
		cb.halfOpenCalls = 0
	}
}

func (cb *CircuitBreaker) isAllowedLocked() bool {
	switch cb.state {
	case CircuitClosed:
		return true
	case CircuitOpen:
		return false
	case CircuitHalfOpen:
		if cb.halfOpenCalls < cb.config.HalfOpenMaxCalls {
			cb.halfOpenCalls++
			return true
		}
		return false
	}
	return false
}
