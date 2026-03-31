package router

import (
	"testing"

	nctx "github.com/otomus/nerva/go/context"
)

// --- NewHandlerCandidate ---

func TestNewHandlerCandidateValid(t *testing.T) {
	hc, err := NewHandlerCandidate("handler-a", 0.8, "matched")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if hc.Name != "handler-a" {
		t.Fatalf("expected name handler-a, got %q", hc.Name)
	}
	if hc.Score != 0.8 {
		t.Fatalf("expected score 0.8, got %f", hc.Score)
	}
}

func TestNewHandlerCandidateBoundaryScores(t *testing.T) {
	if _, err := NewHandlerCandidate("h", 0.0, ""); err != nil {
		t.Fatalf("score 0.0 should be valid: %v", err)
	}
	if _, err := NewHandlerCandidate("h", 1.0, ""); err != nil {
		t.Fatalf("score 1.0 should be valid: %v", err)
	}
}

func TestNewHandlerCandidateInvalidScore(t *testing.T) {
	if _, err := NewHandlerCandidate("h", -0.1, ""); err == nil {
		t.Fatal("expected error for score < 0")
	}
	if _, err := NewHandlerCandidate("h", 1.1, ""); err == nil {
		t.Fatal("expected error for score > 1")
	}
}

// --- NewIntentResult ---

func TestNewIntentResultValid(t *testing.T) {
	ir, err := NewIntentResult("greet", 0.9, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ir.Intent != "greet" {
		t.Fatalf("expected intent greet, got %q", ir.Intent)
	}
	if ir.RawScores == nil {
		t.Fatal("expected non-nil RawScores map")
	}
}

func TestNewIntentResultInvalidConfidence(t *testing.T) {
	if _, err := NewIntentResult("x", -0.5, nil); err == nil {
		t.Fatal("expected error for negative confidence")
	}
	if _, err := NewIntentResult("x", 1.5, nil); err == nil {
		t.Fatal("expected error for confidence > 1")
	}
}

// --- BestHandler ---

func TestBestHandlerReturnsFirst(t *testing.T) {
	hc1, _ := NewHandlerCandidate("first", 0.9, "")
	hc2, _ := NewHandlerCandidate("second", 0.5, "")
	ir, _ := NewIntentResult("test", 0.9, []HandlerCandidate{hc1, hc2})

	best := ir.BestHandler()
	if best == nil {
		t.Fatal("expected non-nil handler")
	}
	if best.Name != "first" {
		t.Fatalf("expected 'first', got %q", best.Name)
	}
}

func TestBestHandlerEmptyHandlers(t *testing.T) {
	ir, _ := NewIntentResult("test", 0.5, nil)
	if ir.BestHandler() != nil {
		t.Fatal("expected nil for empty handlers")
	}
}

// --- RuleRouter ---

func TestNewRuleRouterValidPatterns(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, err := NewRuleRouter(rules, "fallback")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if rr == nil {
		t.Fatal("expected non-nil RuleRouter")
	}
}

func TestNewRuleRouterInvalidPattern(t *testing.T) {
	rules := []Rule{
		{Pattern: `[invalid`, Handler: "h", Intent: "i"},
	}
	_, err := NewRuleRouter(rules, "")
	if err == nil {
		t.Fatal("expected error for invalid regex")
	}
}

func TestNewRuleRouterEmptyRules(t *testing.T) {
	rr, err := NewRuleRouter(nil, "fallback")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if rr == nil {
		t.Fatal("expected non-nil RuleRouter")
	}
}

// --- Classify ---

func TestClassifyMatchesFirstRule(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
		{Pattern: `bye`, Handler: "farewell", Intent: "farewell"},
	}
	rr, _ := NewRuleRouter(rules, "default")
	ctx := nctx.NewContext()

	result, err := rr.Classify(ctx, "hello world")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Intent != "greet" {
		t.Fatalf("expected intent 'greet', got %q", result.Intent)
	}
	if result.Confidence != MatchConfidence {
		t.Fatalf("expected confidence %f, got %f", MatchConfidence, result.Confidence)
	}
	best := result.BestHandler()
	if best == nil || best.Name != "greeter" {
		t.Fatal("expected greeter handler")
	}
}

func TestClassifyCaseInsensitive(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, _ := NewRuleRouter(rules, "")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "HELLO")
	if result.Intent != "greet" {
		t.Fatal("expected case-insensitive match")
	}
}

func TestClassifyFallsBackToDefault(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, _ := NewRuleRouter(rules, "catch-all")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "something else")
	if result.Intent != DefaultIntent {
		t.Fatalf("expected intent %q, got %q", DefaultIntent, result.Intent)
	}
	if result.Confidence != DefaultConfidence {
		t.Fatalf("expected confidence %f, got %f", DefaultConfidence, result.Confidence)
	}
	best := result.BestHandler()
	if best == nil || best.Name != "catch-all" {
		t.Fatal("expected catch-all handler")
	}
}

func TestClassifyNoMatchNoDefault(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, _ := NewRuleRouter(rules, "")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "something else")
	if result.Intent != NoMatchIntent {
		t.Fatalf("expected intent %q, got %q", NoMatchIntent, result.Intent)
	}
	if result.Confidence != NoMatchConfidence {
		t.Fatalf("expected confidence %f, got %f", NoMatchConfidence, result.Confidence)
	}
	if result.BestHandler() != nil {
		t.Fatal("expected nil handler for no match")
	}
}

func TestClassifyEmptyMessage(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, _ := NewRuleRouter(rules, "default")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "")
	if result.Intent != NoMatchIntent {
		t.Fatalf("expected unknown for empty message, got %q", result.Intent)
	}
}

func TestClassifyWhitespaceOnlyMessage(t *testing.T) {
	rules := []Rule{
		{Pattern: `hello`, Handler: "greeter", Intent: "greet"},
	}
	rr, _ := NewRuleRouter(rules, "default")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "   \t\n  ")
	if result.Intent != NoMatchIntent {
		t.Fatalf("expected unknown for whitespace-only, got %q", result.Intent)
	}
}

func TestClassifyFirstMatchWins(t *testing.T) {
	rules := []Rule{
		{Pattern: `he`, Handler: "partial", Intent: "partial"},
		{Pattern: `hello`, Handler: "full", Intent: "full"},
	}
	rr, _ := NewRuleRouter(rules, "")
	ctx := nctx.NewContext()

	result, _ := rr.Classify(ctx, "hello")
	if result.BestHandler().Name != "partial" {
		t.Fatal("expected first rule to win")
	}
}

func TestClassifyNilContext(t *testing.T) {
	rules := []Rule{
		{Pattern: `test`, Handler: "h", Intent: "i"},
	}
	rr, _ := NewRuleRouter(rules, "")

	// Context parameter is unused in RuleRouter, so nil should not panic
	result, err := rr.Classify(nil, "test")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Intent != "i" {
		t.Fatal("expected match even with nil context")
	}
}
