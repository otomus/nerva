package policy

import (
	"testing"

	nctx "github.com/otomus/nerva/go/context"
)

// --- PolicyDecision constants ---

func TestAllowDecision(t *testing.T) {
	if !Allow.Allowed {
		t.Fatal("Allow should be allowed")
	}
}

func TestDenyNoReasonDecision(t *testing.T) {
	if DenyNoReason.Allowed {
		t.Fatal("DenyNoReason should not be allowed")
	}
	if DenyNoReason.Reason == "" {
		t.Fatal("DenyNoReason should have a reason")
	}
}

// --- NewYamlPolicyEngineFromDict ---

func TestNewYamlPolicyEngineFromDictEmpty(t *testing.T) {
	engine := NewYamlPolicyEngineFromDict(map[string]any{})
	if engine == nil {
		t.Fatal("expected non-nil engine")
	}

	cfg := engine.Config()
	if cfg.MaxDepth != DefaultMaxDepth {
		t.Fatalf("expected default max depth %d, got %d", DefaultMaxDepth, cfg.MaxDepth)
	}
	if cfg.MaxToolCalls != DefaultMaxToolCalls {
		t.Fatalf("expected default max tool calls %d, got %d", DefaultMaxToolCalls, cfg.MaxToolCalls)
	}
}

func TestNewYamlPolicyEngineFromDictFull(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"budget": map[string]any{
				"per_agent": map[string]any{
					"max_tokens_per_hour":  10000,
					"max_cost_per_day_usd": 5.0,
					"on_exceed":            "reject",
				},
			},
			"rate_limit": map[string]any{
				"per_user": map[string]any{
					"max_requests_per_minute": 30,
					"on_exceed":               "block",
				},
			},
			"execution": map[string]any{
				"max_depth":                     5,
				"max_tool_calls_per_invocation": 20,
				"timeout_seconds":               10.0,
			},
			"approval": map[string]any{
				"agents": []any{
					map[string]any{
						"name":              "dangerous-agent",
						"requires_approval": true,
						"approvers":         []any{"admin1", "admin2"},
					},
				},
			},
		},
	}

	engine := NewYamlPolicyEngineFromDict(raw)
	cfg := engine.Config()

	if cfg.BudgetMaxTokensPerHour != 10000 {
		t.Fatalf("expected 10000, got %d", cfg.BudgetMaxTokensPerHour)
	}
	if cfg.BudgetMaxCostPerDayUSD != 5.0 {
		t.Fatalf("expected 5.0, got %f", cfg.BudgetMaxCostPerDayUSD)
	}
	if cfg.BudgetOnExceed != "reject" {
		t.Fatalf("expected 'reject', got %q", cfg.BudgetOnExceed)
	}
	if cfg.RateLimitMaxPerMinute != 30 {
		t.Fatalf("expected 30, got %d", cfg.RateLimitMaxPerMinute)
	}
	if cfg.MaxDepth != 5 {
		t.Fatalf("expected 5, got %d", cfg.MaxDepth)
	}
	if cfg.MaxToolCalls != 20 {
		t.Fatalf("expected 20, got %d", cfg.MaxToolCalls)
	}
	if cfg.TimeoutSeconds != 10.0 {
		t.Fatalf("expected 10.0, got %f", cfg.TimeoutSeconds)
	}
	approvers, ok := cfg.ApprovalAgents["dangerous-agent"]
	if !ok {
		t.Fatal("expected approval agents for dangerous-agent")
	}
	if len(approvers) != 2 {
		t.Fatalf("expected 2 approvers, got %d", len(approvers))
	}
}

// --- Evaluate: allow all with no limits ---

func TestEvaluateNoLimits(t *testing.T) {
	engine := NewYamlPolicyEngineFromDict(map[string]any{})
	ctx := nctx.NewContext(nctx.WithUserID("user1"))

	decision, err := engine.Evaluate(ctx, PolicyAction{Kind: "route", Subject: "user1", Target: "agent"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !decision.Allowed {
		t.Fatal("expected allowed with no limits")
	}
}

// --- Rate limit ---

func TestEvaluateRateLimitExceeded(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"rate_limit": map[string]any{
				"per_user": map[string]any{
					"max_requests_per_minute": 2,
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)

	ctx := nctx.NewContext(nctx.WithUserID("user1"))
	action := PolicyAction{Kind: "route", Subject: "user1", Target: "agent"}

	// Record 2 requests
	engine.Record(ctx, action, Allow)
	engine.Record(ctx, action, Allow)

	decision, _ := engine.Evaluate(ctx, action)
	if decision.Allowed {
		t.Fatal("expected denied after exceeding rate limit")
	}
}

func TestEvaluateRateLimitDifferentUsers(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"rate_limit": map[string]any{
				"per_user": map[string]any{
					"max_requests_per_minute": 1,
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)

	ctx1 := nctx.NewContext(nctx.WithUserID("user1"))
	ctx2 := nctx.NewContext(nctx.WithUserID("user2"))
	action := PolicyAction{Kind: "route", Target: "agent"}

	engine.Record(ctx1, action, Allow)

	// User2 should still be allowed
	decision, _ := engine.Evaluate(ctx2, action)
	if !decision.Allowed {
		t.Fatal("different users should have separate rate limits")
	}
}

func TestEvaluateRateLimitAnonymousUser(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"rate_limit": map[string]any{
				"per_user": map[string]any{
					"max_requests_per_minute": 1,
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)

	ctx := nctx.NewContext() // no user ID
	action := PolicyAction{Kind: "route", Target: "agent"}

	engine.Record(ctx, action, Allow)

	decision, _ := engine.Evaluate(ctx, action)
	if decision.Allowed {
		t.Fatal("anonymous user should also be rate limited")
	}
}

// --- Budget ---

func TestEvaluateTokenBudgetExceeded(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"budget": map[string]any{
				"per_agent": map[string]any{
					"max_tokens_per_hour": 100,
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)

	ctx := nctx.NewContext(nctx.WithUserID("user1"))
	ctx.TokenUsage.Accumulate(&nctx.TokenUsage{TotalTokens: 150})

	action := PolicyAction{Kind: "invoke_agent", Target: "my-agent"}
	engine.Record(ctx, action, Allow)

	decision, _ := engine.Evaluate(ctx, action)
	if decision.Allowed {
		t.Fatal("expected denied after exceeding token budget")
	}
	if decision.BudgetRemaining == nil || *decision.BudgetRemaining != 0.0 {
		t.Fatal("expected BudgetRemaining = 0")
	}
}

func TestEvaluateCostBudgetExceeded(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"budget": map[string]any{
				"per_agent": map[string]any{
					"max_cost_per_day_usd": 1.0,
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)

	ctx := nctx.NewContext(nctx.WithUserID("user1"))
	ctx.TokenUsage.Accumulate(&nctx.TokenUsage{CostUSD: 1.5})

	action := PolicyAction{Kind: "invoke_agent", Target: "my-agent"}
	engine.Record(ctx, action, Allow)

	decision, _ := engine.Evaluate(ctx, action)
	if decision.Allowed {
		t.Fatal("expected denied after exceeding cost budget")
	}
}

// --- Approval ---

func TestEvaluateRequiresApproval(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"approval": map[string]any{
				"agents": []any{
					map[string]any{
						"name":              "dangerous",
						"requires_approval": true,
						"approvers":         []any{"admin"},
					},
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)
	ctx := nctx.NewContext()

	decision, _ := engine.Evaluate(ctx, PolicyAction{Kind: "invoke_agent", Target: "dangerous"})
	if !decision.Allowed {
		t.Fatal("expected allowed (approval is separate from denial)")
	}
	if !decision.RequireApproval {
		t.Fatal("expected RequireApproval=true")
	}
	if len(decision.Approvers) != 1 || decision.Approvers[0] != "admin" {
		t.Fatal("expected approver 'admin'")
	}
}

func TestEvaluateNoApprovalForOtherAgents(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"approval": map[string]any{
				"agents": []any{
					map[string]any{
						"name":              "dangerous",
						"requires_approval": true,
						"approvers":         []any{"admin"},
					},
				},
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)
	ctx := nctx.NewContext()

	decision, _ := engine.Evaluate(ctx, PolicyAction{Kind: "invoke_agent", Target: "safe-agent"})
	if decision.RequireApproval {
		t.Fatal("should not require approval for unlisted agent")
	}
}

// --- Execution limits ---

func TestEvaluateDepthExceeded(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"execution": map[string]any{
				"max_depth": 3,
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)
	ctx := nctx.NewContext()
	ctx.Metadata["depth"] = "5"

	decision, _ := engine.Evaluate(ctx, PolicyAction{Kind: "invoke_agent", Target: "agent"})
	if decision.Allowed {
		t.Fatal("expected denied for depth exceeding max")
	}
}

func TestEvaluateDepthWithinLimit(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"execution": map[string]any{
				"max_depth": 10,
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)
	ctx := nctx.NewContext()
	ctx.Metadata["depth"] = "3"

	decision, _ := engine.Evaluate(ctx, PolicyAction{Kind: "invoke_agent", Target: "agent"})
	if !decision.Allowed {
		t.Fatal("expected allowed within depth limit")
	}
}

func TestEvaluateToolCallCountExceeded(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"execution": map[string]any{
				"max_tool_calls_per_invocation": 5,
			},
		},
	}
	engine := NewYamlPolicyEngineFromDict(raw)
	ctx := nctx.NewContext()
	ctx.Metadata["tool_call_count"] = "10"

	decision, _ := engine.Evaluate(ctx, PolicyAction{Kind: "invoke_agent", Target: "agent"})
	if decision.Allowed {
		t.Fatal("expected denied for tool call count exceeding max")
	}
}

// --- Record ---

func TestRecordDeniedDecisionNoOp(t *testing.T) {
	engine := NewYamlPolicyEngineFromDict(map[string]any{})
	ctx := nctx.NewContext(nctx.WithUserID("user1"))

	err := engine.Record(ctx, PolicyAction{}, PolicyDecision{Allowed: false})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

// --- parsePolicyConfig edge cases ---

func TestParsePolicyConfigNilPolicies(t *testing.T) {
	cfg := parsePolicyConfig(map[string]any{"other": "stuff"})
	if cfg.MaxDepth != DefaultMaxDepth {
		t.Fatal("expected defaults for missing policies key")
	}
}

func TestParsePolicyConfigStringValues(t *testing.T) {
	raw := map[string]any{
		"policies": map[string]any{
			"execution": map[string]any{
				"max_depth":      "7",
				"timeout_seconds": "15.5",
			},
		},
	}
	cfg := parsePolicyConfig(raw)
	if cfg.MaxDepth != 7 {
		t.Fatalf("expected 7, got %d", cfg.MaxDepth)
	}
	if cfg.TimeoutSeconds != 15.5 {
		t.Fatalf("expected 15.5, got %f", cfg.TimeoutSeconds)
	}
}

// --- toInt / toFloat ---

func TestToIntVariousTypes(t *testing.T) {
	if toInt(42) != 42 {
		t.Fatal("int input")
	}
	if toInt(42.7) != 42 {
		t.Fatal("float64 input")
	}
	if toInt("99") != 99 {
		t.Fatal("string input")
	}
	if toInt(nil) != 0 {
		t.Fatal("nil input")
	}
	if toInt(true) != 0 {
		t.Fatal("bool input should return 0")
	}
}

func TestToFloatVariousTypes(t *testing.T) {
	if toFloat(3.14) != 3.14 {
		t.Fatal("float64 input")
	}
	if toFloat(7) != 7.0 {
		t.Fatal("int input")
	}
	if toFloat("2.5") != 2.5 {
		t.Fatal("string input")
	}
	if toFloat(nil) != 0.0 {
		t.Fatal("nil input")
	}
}

// --- NewYamlPolicyEngine from file ---

func TestNewYamlPolicyEngineFileNotFound(t *testing.T) {
	_, err := NewYamlPolicyEngine("/nonexistent/path.yaml")
	if err == nil {
		t.Fatal("expected error for missing file")
	}
}
