package context

import (
	"sync"
	"testing"
	"time"
)

// --- NewContext ---

func TestNewContextDefaultValues(t *testing.T) {
	ctx := NewContext()

	if ctx.RequestID == "" {
		t.Fatal("expected non-empty RequestID")
	}
	if ctx.TraceID == "" {
		t.Fatal("expected non-empty TraceID")
	}
	if ctx.Depth != 0 {
		t.Fatalf("expected Depth 0, got %d", ctx.Depth)
	}
	if ctx.MemoryScope != DefaultMemoryScope {
		t.Fatalf("expected MemoryScope %q, got %q", DefaultMemoryScope, ctx.MemoryScope)
	}
	if ctx.TimeoutAt != nil {
		t.Fatal("expected nil TimeoutAt without timeout option")
	}
	if ctx.TokenUsage == nil {
		t.Fatal("expected non-nil TokenUsage")
	}
	if ctx.Metadata == nil {
		t.Fatal("expected non-nil Metadata map")
	}
	if len(ctx.Spans()) != 0 {
		t.Fatalf("expected 0 spans, got %d", len(ctx.Spans()))
	}
	if len(ctx.Events()) != 0 {
		t.Fatalf("expected 0 events, got %d", len(ctx.Events()))
	}
}

func TestNewContextWithOptions(t *testing.T) {
	perms := Permissions{
		Roles: map[string]bool{"admin": true},
	}
	ctx := NewContext(
		WithUserID("u1"),
		WithSessionID("s1"),
		WithPermissions(perms),
		WithMemoryScope(ScopeUser),
		WithTimeout(5.0),
	)

	if ctx.UserID != "u1" {
		t.Fatalf("expected UserID u1, got %q", ctx.UserID)
	}
	if ctx.SessionID != "s1" {
		t.Fatalf("expected SessionID s1, got %q", ctx.SessionID)
	}
	if !ctx.Permissions.HasRole("admin") {
		t.Fatal("expected admin role")
	}
	if ctx.MemoryScope != ScopeUser {
		t.Fatalf("expected ScopeUser, got %q", ctx.MemoryScope)
	}
	if ctx.TimeoutAt == nil {
		t.Fatal("expected non-nil TimeoutAt with timeout option")
	}
}

func TestNewContextUniqueIDs(t *testing.T) {
	a := NewContext()
	b := NewContext()
	if a.RequestID == b.RequestID {
		t.Fatal("expected unique RequestIDs")
	}
	if a.TraceID == b.TraceID {
		t.Fatal("expected unique TraceIDs")
	}
}

// --- Child ---

func TestChildInheritsParentFields(t *testing.T) {
	parent := NewContext(
		WithUserID("u1"),
		WithSessionID("s1"),
		WithMemoryScope(ScopeAgent),
		WithTimeout(10.0),
	)
	parent.Metadata["key"] = "val"

	child := parent.Child("test-handler")

	if child.TraceID != parent.TraceID {
		t.Fatal("child should inherit TraceID")
	}
	if child.UserID != parent.UserID {
		t.Fatal("child should inherit UserID")
	}
	if child.SessionID != parent.SessionID {
		t.Fatal("child should inherit SessionID")
	}
	if child.MemoryScope != parent.MemoryScope {
		t.Fatal("child should inherit MemoryScope")
	}
	if child.TimeoutAt == nil || parent.TimeoutAt == nil {
		t.Fatal("both should have TimeoutAt")
	}
	if *child.TimeoutAt != *parent.TimeoutAt {
		t.Fatal("child should inherit TimeoutAt")
	}
	if child.Depth != parent.Depth+1 {
		t.Fatalf("expected child depth %d, got %d", parent.Depth+1, child.Depth)
	}
	if child.RequestID == parent.RequestID {
		t.Fatal("child should have a fresh RequestID")
	}
	if child.Metadata["key"] != "val" {
		t.Fatal("child should copy metadata")
	}
}

func TestChildMetadataIsolation(t *testing.T) {
	parent := NewContext()
	parent.Metadata["shared"] = "yes"

	child := parent.Child("h")
	child.Metadata["child_only"] = "true"

	if _, ok := parent.Metadata["child_only"]; ok {
		t.Fatal("child metadata mutation must not affect parent")
	}
}

func TestChildHasRootSpan(t *testing.T) {
	parent := NewContext()
	child := parent.Child("my-handler")

	spans := child.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 root span, got %d", len(spans))
	}
	if spans[0].Name != "my-handler" {
		t.Fatalf("expected span name 'my-handler', got %q", spans[0].Name)
	}
}

func TestChildTokenUsageIndependent(t *testing.T) {
	parent := NewContext()
	child := parent.Child("h")

	child.TokenUsage.Accumulate(&TokenUsage{PromptTokens: 10, TotalTokens: 10})

	if parent.TokenUsage.TotalTokens != 0 {
		t.Fatal("child token usage should not affect parent")
	}
}

// --- Permissions ---

func TestCanUseToolUnrestricted(t *testing.T) {
	p := Permissions{AllowedTools: nil}
	if !p.CanUseTool("anything") {
		t.Fatal("nil AllowedTools should allow all")
	}
}

func TestCanUseToolRestricted(t *testing.T) {
	allowed := map[string]bool{"read": true}
	p := Permissions{AllowedTools: &allowed}

	if !p.CanUseTool("read") {
		t.Fatal("expected read to be allowed")
	}
	if p.CanUseTool("write") {
		t.Fatal("expected write to be denied")
	}
}

func TestCanUseToolEmptySet(t *testing.T) {
	empty := map[string]bool{}
	p := Permissions{AllowedTools: &empty}
	if p.CanUseTool("anything") {
		t.Fatal("empty allowed set should deny all")
	}
}

func TestCanUseAgentUnrestricted(t *testing.T) {
	p := Permissions{AllowedAgents: nil}
	if !p.CanUseAgent("any") {
		t.Fatal("nil AllowedAgents should allow all")
	}
}

func TestCanUseAgentRestricted(t *testing.T) {
	allowed := map[string]bool{"agent-a": true}
	p := Permissions{AllowedAgents: &allowed}

	if !p.CanUseAgent("agent-a") {
		t.Fatal("expected agent-a to be allowed")
	}
	if p.CanUseAgent("agent-b") {
		t.Fatal("expected agent-b to be denied")
	}
}

func TestHasRoleNilRoles(t *testing.T) {
	p := Permissions{}
	if p.HasRole("admin") {
		t.Fatal("nil Roles map should return false")
	}
}

func TestHasRole(t *testing.T) {
	p := Permissions{Roles: map[string]bool{"admin": true}}
	if !p.HasRole("admin") {
		t.Fatal("expected true for admin")
	}
	if p.HasRole("user") {
		t.Fatal("expected false for user")
	}
}

// --- TokenUsage ---

func TestTokenUsageAdd(t *testing.T) {
	a := &TokenUsage{PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15, CostUSD: 0.01}
	b := &TokenUsage{PromptTokens: 20, CompletionTokens: 10, TotalTokens: 30, CostUSD: 0.02}

	result := a.Add(b)

	if result.PromptTokens != 30 {
		t.Fatalf("expected 30 prompt tokens, got %d", result.PromptTokens)
	}
	if result.TotalTokens != 45 {
		t.Fatalf("expected 45 total tokens, got %d", result.TotalTokens)
	}
	if result.CostUSD != 0.03 {
		t.Fatalf("expected 0.03 cost, got %f", result.CostUSD)
	}
}

func TestTokenUsageAccumulate(t *testing.T) {
	a := &TokenUsage{PromptTokens: 10, TotalTokens: 10}
	b := &TokenUsage{PromptTokens: 5, TotalTokens: 5}

	a.Accumulate(b)

	if a.PromptTokens != 15 {
		t.Fatalf("expected 15, got %d", a.PromptTokens)
	}
	if a.TotalTokens != 15 {
		t.Fatalf("expected 15, got %d", a.TotalTokens)
	}
}

func TestTokenUsageConcurrentAccumulate(t *testing.T) {
	target := &TokenUsage{}
	var wg sync.WaitGroup
	iterations := 100

	for i := 0; i < iterations; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			target.Accumulate(&TokenUsage{TotalTokens: 1})
		}()
	}
	wg.Wait()

	if target.TotalTokens != iterations {
		t.Fatalf("expected %d, got %d (race condition?)", iterations, target.TotalTokens)
	}
}

// --- Timeout / Cancel ---

func TestIsTimedOutNoTimeout(t *testing.T) {
	ctx := NewContext()
	if ctx.IsTimedOut() {
		t.Fatal("should not be timed out without timeout")
	}
}

func TestIsTimedOutExpired(t *testing.T) {
	ctx := NewContext(WithTimeout(0.001)) // 1ms
	time.Sleep(5 * time.Millisecond)
	if !ctx.IsTimedOut() {
		t.Fatal("should be timed out")
	}
}

func TestCancelAndIsCancelled(t *testing.T) {
	ctx := NewContext()
	if ctx.IsCancelled() {
		t.Fatal("should not be cancelled yet")
	}
	ctx.Cancel()
	if !ctx.IsCancelled() {
		t.Fatal("should be cancelled after Cancel()")
	}
}

func TestCancelPropagatesToChild(t *testing.T) {
	parent := NewContext()
	child := parent.Child("h")

	parent.Cancel()

	if !child.IsCancelled() {
		t.Fatal("child should be cancelled when parent is cancelled")
	}
}

// --- Spans / Events ---

func TestAddSpan(t *testing.T) {
	ctx := NewContext()
	span := ctx.AddSpan("my-span")

	if span.Name != "my-span" {
		t.Fatalf("expected name 'my-span', got %q", span.Name)
	}
	if span.SpanID == "" {
		t.Fatal("expected non-empty SpanID")
	}
	if span.ParentID != ctx.RequestID {
		t.Fatal("expected ParentID == RequestID")
	}

	spans := ctx.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
}

func TestAddEvent(t *testing.T) {
	ctx := NewContext()
	event := ctx.AddEvent("test-event", map[string]string{"k": "v"})

	if event.Name != "test-event" {
		t.Fatalf("expected name 'test-event', got %q", event.Name)
	}
	if event.Attributes["k"] != "v" {
		t.Fatal("expected attribute k=v")
	}

	events := ctx.Events()
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
}

func TestAddEventNilAttributes(t *testing.T) {
	ctx := NewContext()
	event := ctx.AddEvent("e", nil)

	if event.Attributes == nil {
		t.Fatal("nil attributes should be replaced with empty map")
	}
}

func TestSpansReturnsCopy(t *testing.T) {
	ctx := NewContext()
	ctx.AddSpan("s1")

	spans := ctx.Spans()
	spans = append(spans, Span{Name: "injected"})

	if len(ctx.Spans()) != 1 {
		t.Fatal("Spans() must return a copy, not a reference")
	}
}

func TestEventsReturnsCopy(t *testing.T) {
	ctx := NewContext()
	ctx.AddEvent("e1", nil)

	events := ctx.Events()
	events = append(events, Event{Name: "injected"})

	if len(ctx.Events()) != 1 {
		t.Fatal("Events() must return a copy, not a reference")
	}
}

func TestConcurrentSpanAndEventAdds(t *testing.T) {
	ctx := NewContext()
	var wg sync.WaitGroup
	count := 50

	for i := 0; i < count; i++ {
		wg.Add(2)
		go func() {
			defer wg.Done()
			ctx.AddSpan("s")
		}()
		go func() {
			defer wg.Done()
			ctx.AddEvent("e", nil)
		}()
	}
	wg.Wait()

	if len(ctx.Spans()) != count {
		t.Fatalf("expected %d spans, got %d", count, len(ctx.Spans()))
	}
	if len(ctx.Events()) != count {
		t.Fatalf("expected %d events, got %d", count, len(ctx.Events()))
	}
}

// --- RecordTokens ---

func TestRecordTokens(t *testing.T) {
	ctx := NewContext()
	ctx.RecordTokens(&TokenUsage{PromptTokens: 10, TotalTokens: 10})
	ctx.RecordTokens(&TokenUsage{PromptTokens: 5, TotalTokens: 5})

	if ctx.TokenUsage.TotalTokens != 15 {
		t.Fatalf("expected 15, got %d", ctx.TokenUsage.TotalTokens)
	}
}

// --- ElapsedSeconds ---

func TestElapsedSecondsPositive(t *testing.T) {
	ctx := NewContext()
	time.Sleep(2 * time.Millisecond)
	elapsed := ctx.ElapsedSeconds()
	if elapsed <= 0 {
		t.Fatalf("expected positive elapsed, got %f", elapsed)
	}
}

// --- Context() ---

func TestContextReturnsStdContext(t *testing.T) {
	ctx := NewContext()
	stdCtx := ctx.Context()
	if stdCtx == nil {
		t.Fatal("expected non-nil context.Context")
	}
}

// --- Edge cases ---

func TestNewContextEmptyUserID(t *testing.T) {
	ctx := NewContext(WithUserID(""))
	if ctx.UserID != "" {
		t.Fatal("expected empty UserID")
	}
}

func TestNewContextZeroTimeout(t *testing.T) {
	ctx := NewContext(WithTimeout(0))
	if ctx.TimeoutAt != nil {
		t.Fatal("zero timeout should not set TimeoutAt")
	}
}

func TestNewContextNegativeTimeout(t *testing.T) {
	ctx := NewContext(WithTimeout(-1))
	if ctx.TimeoutAt != nil {
		t.Fatal("negative timeout should not set TimeoutAt")
	}
}
