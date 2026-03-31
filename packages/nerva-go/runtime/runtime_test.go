package runtime

import (
	"sync"
	"testing"
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

// --- AgentStatus constants ---

func TestAgentStatusValues(t *testing.T) {
	statuses := []AgentStatus{
		StatusSuccess, StatusError, StatusTimeout,
		StatusWrongHandler, StatusNeedsData, StatusNeedsCredentials,
	}
	seen := make(map[AgentStatus]bool)
	for _, s := range statuses {
		if s == "" {
			t.Fatal("status constant must not be empty")
		}
		if seen[s] {
			t.Fatalf("duplicate status: %s", s)
		}
		seen[s] = true
	}
}

// --- CircuitBreaker ---

func TestCircuitBreakerDefaultConfig(t *testing.T) {
	cfg := DefaultCircuitBreakerConfig()
	if cfg.FailureThreshold != DefaultFailureThreshold {
		t.Fatalf("expected %d, got %d", DefaultFailureThreshold, cfg.FailureThreshold)
	}
	if cfg.RecoverySeconds != DefaultRecoverySeconds {
		t.Fatalf("expected %f, got %f", DefaultRecoverySeconds, cfg.RecoverySeconds)
	}
	if cfg.HalfOpenMaxCalls != DefaultHalfOpenMaxCalls {
		t.Fatalf("expected %d, got %d", DefaultHalfOpenMaxCalls, cfg.HalfOpenMaxCalls)
	}
}

func TestCircuitBreakerStartsClosed(t *testing.T) {
	cb := NewCircuitBreaker(nil)
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed, got %s", cb.State())
	}
	if !cb.IsAllowed() {
		t.Fatal("closed circuit should allow calls")
	}
}

func TestCircuitBreakerOpensAfterThreshold(t *testing.T) {
	cfg := &CircuitBreakerConfig{
		FailureThreshold: 2,
		RecoverySeconds:  60,
		HalfOpenMaxCalls: 1,
	}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	if cb.State() != CircuitClosed {
		t.Fatal("should still be closed after 1 failure")
	}

	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open after 2 failures, got %s", cb.State())
	}
	if cb.IsAllowed() {
		t.Fatal("open circuit should reject calls")
	}
}

func TestCircuitBreakerSuccessResets(t *testing.T) {
	cfg := &CircuitBreakerConfig{FailureThreshold: 2, RecoverySeconds: 60, HalfOpenMaxCalls: 1}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	cb.RecordSuccess()

	// After success, counter resets — need 2 more failures to open
	cb.RecordFailure()
	if cb.State() != CircuitClosed {
		t.Fatal("should still be closed — success reset the counter")
	}
}

func TestCircuitBreakerTransitionsToHalfOpen(t *testing.T) {
	cfg := &CircuitBreakerConfig{
		FailureThreshold: 1,
		RecoverySeconds:  0.01, // 10ms
		HalfOpenMaxCalls: 1,
	}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatal("expected open")
	}

	time.Sleep(20 * time.Millisecond)

	if cb.State() != CircuitHalfOpen {
		t.Fatalf("expected half_open after recovery, got %s", cb.State())
	}
}

func TestCircuitBreakerHalfOpenAllowsLimitedCalls(t *testing.T) {
	cfg := &CircuitBreakerConfig{
		FailureThreshold: 1,
		RecoverySeconds:  0.001,
		HalfOpenMaxCalls: 1,
	}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	time.Sleep(5 * time.Millisecond)

	// First call should be allowed (probe)
	if !cb.IsAllowed() {
		t.Fatal("half-open should allow first probe call")
	}
	// Second call should be blocked
	if cb.IsAllowed() {
		t.Fatal("half-open should block after max probe calls")
	}
}

func TestCircuitBreakerHalfOpenSuccessCloses(t *testing.T) {
	cfg := &CircuitBreakerConfig{
		FailureThreshold: 1,
		RecoverySeconds:  0.001,
		HalfOpenMaxCalls: 1,
	}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	time.Sleep(5 * time.Millisecond)
	// Force state check to transition to half-open
	_ = cb.State()

	cb.RecordSuccess()
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed after half-open success, got %s", cb.State())
	}
}

func TestCircuitBreakerHalfOpenFailureReopens(t *testing.T) {
	cfg := &CircuitBreakerConfig{
		FailureThreshold: 1,
		RecoverySeconds:  0.001,
		HalfOpenMaxCalls: 1,
	}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	time.Sleep(5 * time.Millisecond)
	_ = cb.State() // transition to half-open

	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatalf("expected open after half-open failure, got %s", cb.State())
	}
}

func TestCircuitBreakerReset(t *testing.T) {
	cfg := &CircuitBreakerConfig{FailureThreshold: 1, RecoverySeconds: 60, HalfOpenMaxCalls: 1}
	cb := NewCircuitBreaker(cfg)

	cb.RecordFailure()
	if cb.State() != CircuitOpen {
		t.Fatal("expected open")
	}

	cb.Reset()
	if cb.State() != CircuitClosed {
		t.Fatalf("expected closed after reset, got %s", cb.State())
	}
	if !cb.IsAllowed() {
		t.Fatal("should allow calls after reset")
	}
}

func TestCircuitBreakerConcurrentAccess(t *testing.T) {
	cb := NewCircuitBreaker(nil)
	var wg sync.WaitGroup

	for i := 0; i < 100; i++ {
		wg.Add(3)
		go func() {
			defer wg.Done()
			cb.RecordFailure()
		}()
		go func() {
			defer wg.Done()
			cb.RecordSuccess()
		}()
		go func() {
			defer wg.Done()
			_ = cb.State()
			_ = cb.IsAllowed()
		}()
	}
	wg.Wait()
	// No panic = pass
}

func TestCircuitBreakerNilConfig(t *testing.T) {
	cb := NewCircuitBreaker(nil)
	if cb == nil {
		t.Fatal("expected non-nil breaker with nil config")
	}
}

// --- SubprocessRuntime ---

func TestNewSubprocessRuntimeDefaults(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	if sr == nil {
		t.Fatal("expected non-nil SubprocessRuntime")
	}
	if sr.config.TimeoutSeconds != DefaultTimeoutSeconds {
		t.Fatalf("expected default timeout, got %f", sr.config.TimeoutSeconds)
	}
}

func TestNewSubprocessRuntimeCustomConfig(t *testing.T) {
	cfg := &SubprocessConfig{
		TimeoutSeconds: 10.0,
		HandlerDir:     "/tmp/handlers",
	}
	sr := NewSubprocessRuntime(cfg)
	if sr.config.TimeoutSeconds != 10.0 {
		t.Fatalf("expected 10.0, got %f", sr.config.TimeoutSeconds)
	}
}

func TestInvokeChainEmptyHandlers(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	ctx := newTestContext()

	_, err := sr.InvokeChain(ctx, nil, AgentInput{Message: "test"})
	if err == nil {
		t.Fatal("expected error for empty handlers list")
	}
}

func TestExtractJSONEmptyString(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.extractJSON("")
	if len(result) != 0 {
		t.Fatalf("expected empty map, got %v", result)
	}
}

func TestExtractJSONValidJSON(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.extractJSON(`{"output": "hello", "key": "val"}`)
	if result["output"] != "hello" {
		t.Fatalf("expected 'hello', got %q", result["output"])
	}
	if result["key"] != "val" {
		t.Fatalf("expected 'val', got %q", result["key"])
	}
}

func TestExtractJSONEmbeddedInText(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.extractJSON(`Some text before {"error": "oops"} some text after`)
	if result["error"] != "oops" {
		t.Fatalf("expected 'oops', got %q", result["error"])
	}
}

func TestExtractJSONInvalidJSON(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.extractJSON("not json at all")
	if len(result) != 0 {
		t.Fatalf("expected empty map for invalid JSON, got %v", result)
	}
}

func TestBuildResultSuccess(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.buildResult("h", `{"output": "hello"}`, 0, nil)
	if result.Status != StatusSuccess {
		t.Fatalf("expected success, got %s", result.Status)
	}
	if result.Output != "hello" {
		t.Fatalf("expected 'hello', got %q", result.Output)
	}
	if result.Handler != "h" {
		t.Fatalf("expected handler 'h', got %q", result.Handler)
	}
}

func TestBuildResultSuccessWithResponse(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.buildResult("h", `{"response": "hi there"}`, 0, nil)
	if result.Output != "hi there" {
		t.Fatalf("expected 'hi there', got %q", result.Output)
	}
}

func TestBuildResultSuccessPlainText(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	result := sr.buildResult("h", "plain text output", 0, nil)
	if result.Output != "plain text output" {
		t.Fatalf("expected plain text, got %q", result.Output)
	}
}

func TestBuildResultTimeout(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := "retryable"
	result := sr.buildResult("h", "", -1, &kind)
	if result.Status != StatusTimeout {
		t.Fatalf("expected timeout, got %s", result.Status)
	}
}

func TestBuildResultWrongHandler(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := "wrong_handler"
	result := sr.buildResult("h", `{"error": "not my job"}`, 2, &kind)
	if result.Status != StatusWrongHandler {
		t.Fatalf("expected wrong_handler, got %s", result.Status)
	}
	if result.Error != "not my job" {
		t.Fatalf("expected 'not my job', got %q", result.Error)
	}
}

func TestBuildResultWrongHandlerNoError(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := "wrong_handler"
	result := sr.buildResult("h", "", 2, &kind)
	if result.Error != "handler declined the input" {
		t.Fatalf("expected default reason, got %q", result.Error)
	}
}

func TestBuildResultFatalError(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := "fatal"
	result := sr.buildResult("h", `{"error": "boom"}`, 1, &kind)
	if result.Status != StatusError {
		t.Fatalf("expected error, got %s", result.Status)
	}
	if result.Error != "boom" {
		t.Fatalf("expected 'boom', got %q", result.Error)
	}
}

func TestClassifyErrorExitZero(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := sr.classifyError(0)
	if kind != nil {
		t.Fatal("expected nil for exit code 0")
	}
}

func TestClassifyErrorExitNegOne(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := sr.classifyError(-1)
	if kind == nil || *kind != "retryable" {
		t.Fatal("expected 'retryable' for exit code -1")
	}
}

func TestClassifyErrorExitTwo(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := sr.classifyError(WrongHandlerExitCode)
	if kind == nil || *kind != "wrong_handler" {
		t.Fatal("expected 'wrong_handler' for exit code 2")
	}
}

func TestClassifyErrorOtherCode(t *testing.T) {
	sr := NewSubprocessRuntime(nil)
	kind := sr.classifyError(42)
	if kind == nil || *kind != "fatal" {
		t.Fatal("expected 'fatal' for other exit codes")
	}
}

// --- tryParseJSON ---

func TestTryParseJSONValid(t *testing.T) {
	result := tryParseJSON(`{"key": "value", "num": 42}`)
	if result == nil {
		t.Fatal("expected non-nil result")
	}
	if result["key"] != "value" {
		t.Fatalf("expected 'value', got %q", result["key"])
	}
	if result["num"] != "42" {
		t.Fatalf("expected '42', got %q", result["num"])
	}
}

func TestTryParseJSONInvalid(t *testing.T) {
	if tryParseJSON("not json") != nil {
		t.Fatal("expected nil for invalid JSON")
	}
}

func TestTryParseJSONEmpty(t *testing.T) {
	result := tryParseJSON("{}")
	if result == nil {
		t.Fatal("expected non-nil for empty object")
	}
	if len(result) != 0 {
		t.Fatalf("expected empty map, got %v", result)
	}
}

func TestTryParseJSONArray(t *testing.T) {
	// Arrays should fail since we expect map[string]any
	if tryParseJSON("[1,2,3]") != nil {
		t.Fatal("expected nil for JSON array")
	}
}

// helper
func newTestContext() *nctx.ExecContext {
	return nctx.NewContext(nctx.WithUserID("test-user"))
}
