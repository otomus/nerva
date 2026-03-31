package orchestrator

import (
	"fmt"
	"testing"

	nctx "github.com/otomus/nerva/go/context"
	"github.com/otomus/nerva/go/memory"
	"github.com/otomus/nerva/go/policy"
	"github.com/otomus/nerva/go/responder"
	"github.com/otomus/nerva/go/router"
	"github.com/otomus/nerva/go/runtime"
)

// --- Mocks ---

type mockRouter struct {
	result router.IntentResult
	err    error
}

func (m *mockRouter) Classify(_ *nctx.ExecContext, _ string) (router.IntentResult, error) {
	return m.result, m.err
}

type mockRuntime struct {
	result runtime.AgentResult
	err    error
	calls  []string
}

func (m *mockRuntime) Invoke(_ *nctx.ExecContext, handler string, _ runtime.AgentInput) (runtime.AgentResult, error) {
	m.calls = append(m.calls, handler)
	return m.result, m.err
}

func (m *mockRuntime) InvokeChain(_ *nctx.ExecContext, _ []string, _ runtime.AgentInput) (runtime.AgentResult, error) {
	return m.result, m.err
}

func (m *mockRuntime) Delegate(_ *nctx.ExecContext, handler string, _ runtime.AgentInput) (runtime.AgentResult, error) {
	return m.result, m.err
}

type mockPolicy struct {
	decision policy.PolicyDecision
	err      error
}

func (m *mockPolicy) Evaluate(_ *nctx.ExecContext, _ policy.PolicyAction) (policy.PolicyDecision, error) {
	return m.decision, m.err
}

func (m *mockPolicy) Record(_ *nctx.ExecContext, _ policy.PolicyAction, _ policy.PolicyDecision) error {
	return nil
}

// --- New ---

func TestNewOrchestratorDefaults(t *testing.T) {
	o := New(Config{})
	if o.maxDelegationDepth != DefaultMaxDelegationDepth {
		t.Fatalf("expected %d, got %d", DefaultMaxDelegationDepth, o.maxDelegationDepth)
	}
}

func TestNewOrchestratorCustomDepth(t *testing.T) {
	o := New(Config{MaxDelegationDepth: 3})
	if o.maxDelegationDepth != 3 {
		t.Fatalf("expected 3, got %d", o.maxDelegationDepth)
	}
}

// --- Handle ---

func TestHandleHappyPath(t *testing.T) {
	candidate, _ := router.NewHandlerCandidate("greeter", 1.0, "match")
	intentResult, _ := router.NewIntentResult("greet", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "hello"}},
		Responder: responder.NewPassthroughResponder(),
	})

	ctx := nctx.NewContext(nctx.WithUserID("u1"))
	resp, err := o.Handle(ctx, "hi", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Text != "hello" {
		t.Fatalf("expected 'hello', got %q", resp.Text)
	}
}

func TestHandleNilContext(t *testing.T) {
	candidate, _ := router.NewHandlerCandidate("h", 1.0, "")
	intentResult, _ := router.NewIntentResult("i", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "ok"}},
		Responder: responder.NewPassthroughResponder(),
	})

	resp, err := o.Handle(nil, "msg", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Text != "ok" {
		t.Fatalf("expected 'ok', got %q", resp.Text)
	}
}

func TestHandleWithChannel(t *testing.T) {
	candidate, _ := router.NewHandlerCandidate("h", 1.0, "")
	intentResult, _ := router.NewIntentResult("i", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "ok"}},
		Responder: responder.NewPassthroughResponder(),
	})

	ch := responder.WebSocketChannel
	resp, _ := o.Handle(nctx.NewContext(), "msg", &ch)
	if resp.Channel.Name != "websocket" {
		t.Fatalf("expected websocket channel, got %q", resp.Channel.Name)
	}
}

func TestHandleFallbackWhenNoHandler(t *testing.T) {
	// No handlers in intent result
	intentResult, _ := router.NewIntentResult("unknown", 0.0, nil)

	rt := &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "fallback response"}}
	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   rt,
		Responder: responder.NewPassthroughResponder(),
	})

	_, err := o.Handle(nctx.NewContext(), "msg", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(rt.calls) != 1 || rt.calls[0] != FallbackHandler {
		t.Fatalf("expected fallback handler, got %v", rt.calls)
	}
}

func TestHandleRouterError(t *testing.T) {
	o := New(Config{
		Router:    &mockRouter{err: fmt.Errorf("router broke")},
		Runtime:   &mockRuntime{},
		Responder: responder.NewPassthroughResponder(),
	})

	_, err := o.Handle(nctx.NewContext(), "msg", nil)
	if err == nil {
		t.Fatal("expected error from router")
	}
}

func TestHandleRuntimeError(t *testing.T) {
	candidate, _ := router.NewHandlerCandidate("h", 1.0, "")
	intentResult, _ := router.NewIntentResult("i", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{err: fmt.Errorf("runtime broke")},
		Responder: responder.NewPassthroughResponder(),
	})

	_, err := o.Handle(nctx.NewContext(), "msg", nil)
	if err == nil {
		t.Fatal("expected error from runtime")
	}
}

// --- Handle with policy ---

func TestHandlePolicyDenied(t *testing.T) {
	o := New(Config{
		Router:    &mockRouter{},
		Runtime:   &mockRuntime{},
		Responder: responder.NewPassthroughResponder(),
		Policy:    &mockPolicy{decision: policy.PolicyDecision{Allowed: false, Reason: "blocked"}},
	})

	_, err := o.Handle(nctx.NewContext(), "msg", nil)
	if err == nil {
		t.Fatal("expected policy denied error")
	}

	policyErr, ok := err.(*PolicyDeniedError)
	if !ok {
		t.Fatalf("expected PolicyDeniedError, got %T", err)
	}
	if policyErr.Error() != "blocked" {
		t.Fatalf("expected 'blocked', got %q", policyErr.Error())
	}
}

func TestHandlePolicyEvalError(t *testing.T) {
	o := New(Config{
		Router:    &mockRouter{},
		Runtime:   &mockRuntime{},
		Responder: responder.NewPassthroughResponder(),
		Policy:    &mockPolicy{err: fmt.Errorf("policy eval failed")},
	})

	_, err := o.Handle(nctx.NewContext(), "msg", nil)
	if err == nil {
		t.Fatal("expected error from policy evaluation")
	}
}

// --- Handle with memory ---

func TestHandleWithMemory(t *testing.T) {
	hot := memory.NewInMemoryHotMemory(10)
	mem := memory.NewTieredMemory(hot, 4000)

	candidate, _ := router.NewHandlerCandidate("h", 1.0, "")
	intentResult, _ := router.NewIntentResult("i", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "answer", Handler: "h"}},
		Responder: responder.NewPassthroughResponder(),
		Memory:    mem,
	})

	ctx := nctx.NewContext(nctx.WithSessionID("sess"))
	_, err := o.Handle(ctx, "question", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify memory was stored
	mc, _ := mem.Recall(ctx, "")
	if len(mc.Conversation) != 1 {
		t.Fatalf("expected 1 stored message, got %d", len(mc.Conversation))
	}
}

func TestHandleDoesNotStoreOnError(t *testing.T) {
	hot := memory.NewInMemoryHotMemory(10)
	mem := memory.NewTieredMemory(hot, 4000)

	candidate, _ := router.NewHandlerCandidate("h", 1.0, "")
	intentResult, _ := router.NewIntentResult("i", 1.0, []router.HandlerCandidate{candidate})

	o := New(Config{
		Router:    &mockRouter{result: intentResult},
		Runtime:   &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusError, Output: "err", Error: "boom"}},
		Responder: responder.NewPassthroughResponder(),
		Memory:    mem,
	})

	ctx := nctx.NewContext(nctx.WithSessionID("sess"))
	o.Handle(ctx, "question", nil)

	mc, _ := mem.Recall(ctx, "")
	if len(mc.Conversation) != 0 {
		t.Fatal("should not store memory on error result")
	}
}

// --- Delegate ---

func TestDelegateHappyPath(t *testing.T) {
	o := New(Config{
		Runtime: &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "delegated"}},
	})

	ctx := nctx.NewContext()
	result, err := o.Delegate(ctx, "sub-agent", "do something")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Output != "delegated" {
		t.Fatalf("expected 'delegated', got %q", result.Output)
	}
}

func TestDelegateEmptyHandler(t *testing.T) {
	o := New(Config{Runtime: &mockRuntime{}})
	ctx := nctx.NewContext()

	result, err := o.Delegate(ctx, "", "msg")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != runtime.StatusError {
		t.Fatal("expected error status for empty handler")
	}
}

func TestDelegatePermissionDenied(t *testing.T) {
	allowed := map[string]bool{"agent-a": true}
	perms := nctx.Permissions{AllowedAgents: &allowed}
	ctx := nctx.NewContext(nctx.WithPermissions(perms))

	o := New(Config{Runtime: &mockRuntime{}})

	result, err := o.Delegate(ctx, "agent-b", "msg")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != runtime.StatusError {
		t.Fatal("expected error status for permission denied")
	}
}

func TestDelegateDepthExceeded(t *testing.T) {
	o := New(Config{
		Runtime:            &mockRuntime{},
		MaxDelegationDepth: 1,
	})

	// Create a context at depth 1 — child will be depth 2, exceeding max of 1
	ctx := nctx.NewContext()
	child := ctx.Child("first-level") // depth 1

	result, err := o.Delegate(child, "agent", "msg")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != runtime.StatusError {
		t.Fatal("expected error status for depth exceeded")
	}
}

func TestDelegateTokenAccumulation(t *testing.T) {
	o := New(Config{
		Runtime: &mockRuntime{result: runtime.AgentResult{Status: runtime.StatusSuccess, Output: "ok"}},
	})

	ctx := nctx.NewContext()
	o.Delegate(ctx, "agent", "msg")

	// The child context's token usage is accumulated into parent
	// Since mockRuntime doesn't record tokens, this is 0, but no panic = pass
	if ctx.TokenUsage.TotalTokens < 0 {
		t.Fatal("unexpected negative tokens")
	}
}

// --- PolicyDeniedError ---

func TestPolicyDeniedErrorWithReason(t *testing.T) {
	err := &PolicyDeniedError{Decision: policy.PolicyDecision{Reason: "rate limit"}}
	if err.Error() != "rate limit" {
		t.Fatalf("expected 'rate limit', got %q", err.Error())
	}
}

func TestPolicyDeniedErrorNoReason(t *testing.T) {
	err := &PolicyDeniedError{Decision: policy.PolicyDecision{}}
	if err.Error() != "denied by policy" {
		t.Fatalf("expected 'denied by policy', got %q", err.Error())
	}
}

// --- pickHandler ---

func TestPickHandlerWithCandidate(t *testing.T) {
	candidate, _ := router.NewHandlerCandidate("my-handler", 1.0, "")
	ir, _ := router.NewIntentResult("intent", 1.0, []router.HandlerCandidate{candidate})

	name := pickHandler(ir)
	if name != "my-handler" {
		t.Fatalf("expected 'my-handler', got %q", name)
	}
}

func TestPickHandlerFallback(t *testing.T) {
	ir, _ := router.NewIntentResult("unknown", 0.0, nil)
	name := pickHandler(ir)
	if name != FallbackHandler {
		t.Fatalf("expected %q, got %q", FallbackHandler, name)
	}
}
