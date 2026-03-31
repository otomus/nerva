package policy

import (
	"fmt"
	"os"
	"strconv"
	"sync"
	"time"

	nctx "github.com/otomus/nerva/go/context"
	"gopkg.in/yaml.v3"
)

const (
	// SecondsPerMinute is used for rate limit sliding window.
	SecondsPerMinute = 60
	// SecondsPerHour is used for token budget sliding window.
	SecondsPerHour = 3600
	// SecondsPerDay is used for cost budget sliding window.
	SecondsPerDay = 86400

	// DefaultMaxDepth is the maximum delegation depth.
	DefaultMaxDepth = 10
	// DefaultMaxToolCalls is the maximum tool invocations per invocation.
	DefaultMaxToolCalls = 50
	// DefaultPolicyTimeoutSeconds is the per-action timeout.
	DefaultPolicyTimeoutSeconds = 30.0

	// Unlimited means no enforcement.
	Unlimited = 0

	onExceedBlock = "block"
	onExceedReject = "reject"
)

// PolicyConfig is the parsed, validated policy configuration.
type PolicyConfig struct {
	BudgetMaxTokensPerHour  int
	BudgetMaxCostPerDayUSD  float64
	BudgetOnExceed          string
	RateLimitMaxPerMinute   int
	RateLimitOnExceed       string
	ApprovalAgents          map[string][]string
	MaxDepth                int
	MaxToolCalls            int
	TimeoutSeconds          float64
}

func defaultPolicyConfig() PolicyConfig {
	return PolicyConfig{
		BudgetOnExceed:    onExceedBlock,
		RateLimitOnExceed: onExceedReject,
		ApprovalAgents:    make(map[string][]string),
		MaxDepth:          DefaultMaxDepth,
		MaxToolCalls:      DefaultMaxToolCalls,
		TimeoutSeconds:    DefaultPolicyTimeoutSeconds,
	}
}

// YamlPolicyEngine loads rules from YAML configuration and evaluates them at runtime.
type YamlPolicyEngine struct {
	config PolicyConfig
	mu     sync.Mutex

	// Sliding window: userID -> list of request timestamps
	requestTimestamps map[string][]float64

	// Token tracking: agentName -> list of (timestamp, tokenCount)
	tokenLedger map[string][]ledgerEntry

	// Cost tracking: agentName -> list of (timestamp, costUSD)
	costLedger map[string][]ledgerEntry
}

type ledgerEntry struct {
	timestamp float64
	value     float64
}

// NewYamlPolicyEngine creates a policy engine from a YAML file path.
func NewYamlPolicyEngine(configPath string) (*YamlPolicyEngine, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("policy config not found: %s", configPath)
	}
	var raw map[string]any
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("failed to parse policy config: %w", err)
	}
	return newEngineFromDict(raw), nil
}

// NewYamlPolicyEngineFromDict creates a policy engine from a pre-parsed dict.
func NewYamlPolicyEngineFromDict(raw map[string]any) *YamlPolicyEngine {
	return newEngineFromDict(raw)
}

func newEngineFromDict(raw map[string]any) *YamlPolicyEngine {
	config := parsePolicyConfig(raw)
	return &YamlPolicyEngine{
		config:            config,
		requestTimestamps: make(map[string][]float64),
		tokenLedger:       make(map[string][]ledgerEntry),
		costLedger:        make(map[string][]ledgerEntry),
	}
}

// Config returns the parsed policy configuration.
func (e *YamlPolicyEngine) Config() PolicyConfig {
	return e.config
}

// Evaluate checks action against all applicable policies.
func (e *YamlPolicyEngine) Evaluate(ctx *nctx.ExecContext, action PolicyAction) (PolicyDecision, error) {
	checks := []func(PolicyAction, *nctx.ExecContext) PolicyDecision{
		e.checkRateLimit,
		e.checkBudget,
		e.checkApproval,
		e.checkExecution,
	}

	for _, check := range checks {
		decision := check(action, ctx)
		if !decision.Allowed || decision.RequireApproval {
			return decision, nil
		}
	}

	return Allow, nil
}

// Record updates internal counters after a decision.
func (e *YamlPolicyEngine) Record(ctx *nctx.ExecContext, action PolicyAction, decision PolicyDecision) error {
	if !decision.Allowed {
		return nil
	}

	now := float64(time.Now().UnixMilli()) / 1000.0
	userID := ctx.UserID
	if userID == "" {
		userID = "anonymous"
	}

	e.mu.Lock()
	defer e.mu.Unlock()

	e.requestTimestamps[userID] = append(e.requestTimestamps[userID], now)

	if ctx.TokenUsage.TotalTokens > 0 {
		e.tokenLedger[action.Target] = append(e.tokenLedger[action.Target],
			ledgerEntry{timestamp: now, value: float64(ctx.TokenUsage.TotalTokens)})
	}
	if ctx.TokenUsage.CostUSD > 0 {
		e.costLedger[action.Target] = append(e.costLedger[action.Target],
			ledgerEntry{timestamp: now, value: ctx.TokenUsage.CostUSD})
	}

	return nil
}

func (e *YamlPolicyEngine) checkRateLimit(action PolicyAction, ctx *nctx.ExecContext) PolicyDecision {
	limit := e.config.RateLimitMaxPerMinute
	if limit == Unlimited {
		return Allow
	}

	userID := ctx.UserID
	if userID == "" {
		userID = "anonymous"
	}

	now := float64(time.Now().UnixMilli()) / 1000.0
	cutoff := now - SecondsPerMinute

	e.mu.Lock()
	timestamps := e.requestTimestamps[userID]
	var recent []float64
	for _, ts := range timestamps {
		if ts > cutoff {
			recent = append(recent, ts)
		}
	}
	e.requestTimestamps[userID] = recent
	e.mu.Unlock()

	if len(recent) >= limit {
		return PolicyDecision{
			Allowed: false,
			Reason: fmt.Sprintf("rate limit exceeded: %d/%d requests per minute (on_exceed=%s)",
				len(recent), limit, e.config.RateLimitOnExceed),
		}
	}
	return Allow
}

func (e *YamlPolicyEngine) checkBudget(action PolicyAction, ctx *nctx.ExecContext) PolicyDecision {
	tokenDecision := e.checkTokenBudget(action.Target)
	if !tokenDecision.Allowed {
		return tokenDecision
	}
	return e.checkCostBudget(action.Target)
}

func (e *YamlPolicyEngine) checkTokenBudget(agentName string) PolicyDecision {
	limit := e.config.BudgetMaxTokensPerHour
	if limit == Unlimited {
		return Allow
	}

	now := float64(time.Now().UnixMilli()) / 1000.0
	cutoff := now - SecondsPerHour

	e.mu.Lock()
	entries := e.tokenLedger[agentName]
	var recent []ledgerEntry
	for _, entry := range entries {
		if entry.timestamp > cutoff {
			recent = append(recent, entry)
		}
	}
	e.tokenLedger[agentName] = recent
	e.mu.Unlock()

	totalTokens := 0.0
	for _, entry := range recent {
		totalTokens += entry.value
	}

	if totalTokens >= float64(limit) {
		zero := 0.0
		return PolicyDecision{
			Allowed:         false,
			Reason:          fmt.Sprintf("token budget exceeded: %.0f/%d tokens per hour (on_exceed=%s)", totalTokens, limit, e.config.BudgetOnExceed),
			BudgetRemaining: &zero,
		}
	}
	return Allow
}

func (e *YamlPolicyEngine) checkCostBudget(agentName string) PolicyDecision {
	limit := e.config.BudgetMaxCostPerDayUSD
	if limit <= 0 {
		return Allow
	}

	now := float64(time.Now().UnixMilli()) / 1000.0
	cutoff := now - SecondsPerDay

	e.mu.Lock()
	entries := e.costLedger[agentName]
	var recent []ledgerEntry
	for _, entry := range entries {
		if entry.timestamp > cutoff {
			recent = append(recent, entry)
		}
	}
	e.costLedger[agentName] = recent
	e.mu.Unlock()

	totalCost := 0.0
	for _, entry := range recent {
		totalCost += entry.value
	}
	remaining := limit - totalCost

	if remaining <= 0 {
		zero := 0.0
		return PolicyDecision{
			Allowed:         false,
			Reason:          fmt.Sprintf("cost budget exceeded: $%.2f/$%.2f per day (on_exceed=%s)", totalCost, limit, e.config.BudgetOnExceed),
			BudgetRemaining: &zero,
		}
	}

	return PolicyDecision{Allowed: true, BudgetRemaining: &remaining}
}

func (e *YamlPolicyEngine) checkApproval(action PolicyAction, _ *nctx.ExecContext) PolicyDecision {
	approvers, ok := e.config.ApprovalAgents[action.Target]
	if !ok {
		return Allow
	}
	return PolicyDecision{
		Allowed:         true,
		RequireApproval: true,
		Approvers:       approvers,
		Reason:          fmt.Sprintf("agent '%s' requires approval", action.Target),
	}
}

func (e *YamlPolicyEngine) checkExecution(action PolicyAction, ctx *nctx.ExecContext) PolicyDecision {
	depthDecision := e.checkDepth(ctx)
	if !depthDecision.Allowed {
		return depthDecision
	}
	return e.checkToolCallCount(ctx)
}

func (e *YamlPolicyEngine) checkDepth(ctx *nctx.ExecContext) PolicyDecision {
	depthStr := ctx.Metadata["depth"]
	depth, _ := strconv.Atoi(depthStr)

	if depth > e.config.MaxDepth {
		return PolicyDecision{
			Allowed: false,
			Reason:  fmt.Sprintf("execution depth %d exceeds maximum %d", depth, e.config.MaxDepth),
		}
	}
	return Allow
}

func (e *YamlPolicyEngine) checkToolCallCount(ctx *nctx.ExecContext) PolicyDecision {
	countStr := ctx.Metadata["tool_call_count"]
	count, _ := strconv.Atoi(countStr)

	if count > e.config.MaxToolCalls {
		return PolicyDecision{
			Allowed: false,
			Reason:  fmt.Sprintf("tool call count %d exceeds maximum %d", count, e.config.MaxToolCalls),
		}
	}
	return Allow
}

// parsePolicyConfig parses a raw YAML dict into a validated PolicyConfig.
func parsePolicyConfig(raw map[string]any) PolicyConfig {
	cfg := defaultPolicyConfig()

	policies, ok := raw["policies"].(map[string]any)
	if !ok {
		return cfg
	}

	// Budget
	if budget, ok := policies["budget"].(map[string]any); ok {
		if perAgent, ok := budget["per_agent"].(map[string]any); ok {
			if v, ok := perAgent["max_tokens_per_hour"]; ok {
				cfg.BudgetMaxTokensPerHour = toInt(v)
			}
			if v, ok := perAgent["max_cost_per_day_usd"]; ok {
				cfg.BudgetMaxCostPerDayUSD = toFloat(v)
			}
			if v, ok := perAgent["on_exceed"].(string); ok {
				cfg.BudgetOnExceed = v
			}
		}
	}

	// Rate limit
	if rateLimit, ok := policies["rate_limit"].(map[string]any); ok {
		if perUser, ok := rateLimit["per_user"].(map[string]any); ok {
			if v, ok := perUser["max_requests_per_minute"]; ok {
				cfg.RateLimitMaxPerMinute = toInt(v)
			}
			if v, ok := perUser["on_exceed"].(string); ok {
				cfg.RateLimitOnExceed = v
			}
		}
	}

	// Approval
	if approval, ok := policies["approval"].(map[string]any); ok {
		if agents, ok := approval["agents"].([]any); ok {
			for _, agent := range agents {
				agentMap, ok := agent.(map[string]any)
				if !ok {
					continue
				}
				name, ok := agentMap["name"].(string)
				if !ok {
					continue
				}
				requiresApproval, _ := agentMap["requires_approval"].(bool)
				if !requiresApproval {
					continue
				}
				if approvers, ok := agentMap["approvers"].([]any); ok {
					var approverStrs []string
					for _, a := range approvers {
						approverStrs = append(approverStrs, fmt.Sprintf("%v", a))
					}
					cfg.ApprovalAgents[name] = approverStrs
				}
			}
		}
	}

	// Execution
	if execution, ok := policies["execution"].(map[string]any); ok {
		if v, ok := execution["max_depth"]; ok {
			cfg.MaxDepth = toInt(v)
		}
		if v, ok := execution["max_tool_calls_per_invocation"]; ok {
			cfg.MaxToolCalls = toInt(v)
		}
		if v, ok := execution["timeout_seconds"]; ok {
			cfg.TimeoutSeconds = toFloat(v)
		}
	}

	return cfg
}

func toInt(v any) int {
	switch val := v.(type) {
	case int:
		return val
	case float64:
		return int(val)
	case string:
		n, _ := strconv.Atoi(val)
		return n
	default:
		return 0
	}
}

func toFloat(v any) float64 {
	switch val := v.(type) {
	case float64:
		return val
	case int:
		return float64(val)
	case string:
		f, _ := strconv.ParseFloat(val, 64)
		return f
	default:
		return 0.0
	}
}
