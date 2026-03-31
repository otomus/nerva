// Package orchestrator wires all Nerva primitives into a single request handler.
//
// The orchestrator owns the full request lifecycle:
// message -> context -> policy -> memory -> router -> runtime -> responder -> response
package orchestrator

import (
	"fmt"

	nctx "github.com/otomus/nerva/go/context"
	"github.com/otomus/nerva/go/memory"
	"github.com/otomus/nerva/go/policy"
	"github.com/otomus/nerva/go/registry"
	"github.com/otomus/nerva/go/responder"
	"github.com/otomus/nerva/go/router"
	"github.com/otomus/nerva/go/runtime"
	"github.com/otomus/nerva/go/tools"
)

const (
	// PolicyActionRoute is the policy action kind for routing a user message.
	PolicyActionRoute = "route"
	// PolicyActionInvoke is the policy action kind for invoking a handler.
	PolicyActionInvoke = "invoke_agent"
	// FallbackHandler is the sentinel handler name when the router returns no candidates.
	FallbackHandler = "__fallback__"
	// DefaultMaxDelegationDepth is the maximum delegation depth.
	DefaultMaxDelegationDepth = 5
)

// PolicyDeniedError is raised when policy blocks a request.
type PolicyDeniedError struct {
	Decision policy.PolicyDecision
}

func (e *PolicyDeniedError) Error() string {
	if e.Decision.Reason != "" {
		return e.Decision.Reason
	}
	return "denied by policy"
}

// Config holds the optional dependencies for the orchestrator.
type Config struct {
	Router             router.IntentRouter
	Runtime            runtime.AgentRuntime
	Responder          responder.Responder
	Tools              tools.ToolManager
	Memory             memory.Memory
	Registry           registry.Registry
	Policy             policy.PolicyEngine
	MaxDelegationDepth int
}

// Orchestrator wires all primitives into a single request handler.
type Orchestrator struct {
	router             router.IntentRouter
	runtime            runtime.AgentRuntime
	responder          responder.Responder
	tools              tools.ToolManager
	memory             memory.Memory
	registry           registry.Registry
	policy             policy.PolicyEngine
	maxDelegationDepth int
}

// New creates a new Orchestrator from the given config.
func New(cfg Config) *Orchestrator {
	maxDepth := cfg.MaxDelegationDepth
	if maxDepth <= 0 {
		maxDepth = DefaultMaxDelegationDepth
	}
	return &Orchestrator{
		router:             cfg.Router,
		runtime:            cfg.Runtime,
		responder:          cfg.Responder,
		tools:              cfg.Tools,
		memory:             cfg.Memory,
		registry:           cfg.Registry,
		policy:             cfg.Policy,
		maxDelegationDepth: maxDepth,
	}
}

// Handle processes a message through the full pipeline.
func (o *Orchestrator) Handle(ctx *nctx.ExecContext, message string, channel *responder.Channel) (responder.Response, error) {
	if ctx == nil {
		ctx = nctx.NewContext()
	}
	targetChannel := responder.APIChannel
	if channel != nil {
		targetChannel = *channel
	}

	// Policy check on route
	if err := o.checkPolicy(ctx, PolicyActionRoute, message); err != nil {
		return responder.Response{}, err
	}

	// Memory recall
	history := o.recallMemory(ctx, message)

	// Route
	intent, err := o.router.Classify(ctx, message)
	if err != nil {
		return responder.Response{}, fmt.Errorf("routing failed: %w", err)
	}
	handlerName := pickHandler(intent)

	// Build input
	agentInput := runtime.AgentInput{
		Message: message,
		History: history,
	}

	// Policy check on invoke
	if err := o.checkPolicy(ctx, PolicyActionInvoke, handlerName); err != nil {
		return responder.Response{}, err
	}

	// Invoke
	result, err := o.runtime.Invoke(ctx, handlerName, agentInput)
	if err != nil {
		return responder.Response{}, fmt.Errorf("invocation failed: %w", err)
	}

	// Store memory
	o.storeMemory(ctx, result)

	// Format response
	return o.responder.Format(ctx, result, targetChannel)
}

// Delegate delegates execution to another handler with a child context.
func (o *Orchestrator) Delegate(ctx *nctx.ExecContext, handlerName, message string) (runtime.AgentResult, error) {
	if handlerName == "" {
		return buildDelegationError("handler_name must not be empty"), nil
	}

	if !ctx.Permissions.CanUseAgent(handlerName) {
		ctx.AddEvent("delegation.denied", map[string]string{
			"handler": handlerName,
			"reason":  "permission_denied",
		})
		return buildDelegationError(
			fmt.Sprintf("permission denied: cannot delegate to '%s'", handlerName),
		), nil
	}

	childCtx := ctx.Child(handlerName)

	if childCtx.Depth > o.maxDelegationDepth {
		ctx.AddEvent("delegation.depth_exceeded", map[string]string{
			"handler":   handlerName,
			"depth":     fmt.Sprintf("%d", childCtx.Depth),
			"max_depth": fmt.Sprintf("%d", o.maxDelegationDepth),
		})
		return buildDelegationError(
			fmt.Sprintf("Delegation depth limit exceeded (max: %d)", o.maxDelegationDepth),
		), nil
	}

	agentInput := runtime.AgentInput{Message: message}
	result, err := o.runtime.Invoke(childCtx, handlerName, agentInput)
	if err != nil {
		return runtime.AgentResult{}, err
	}

	ctx.RecordTokens(childCtx.TokenUsage)
	return result, nil
}

func (o *Orchestrator) checkPolicy(ctx *nctx.ExecContext, actionKind, target string) error {
	if o.policy == nil {
		return nil
	}

	subject := ctx.UserID
	if subject == "" {
		subject = "anonymous"
	}

	action := policy.PolicyAction{
		Kind:    actionKind,
		Subject: subject,
		Target:  target,
	}

	decision, err := o.policy.Evaluate(ctx, action)
	if err != nil {
		return fmt.Errorf("policy evaluation failed: %w", err)
	}

	_ = o.policy.Record(ctx, action, decision)

	if !decision.Allowed {
		ctx.AddEvent("policy.denied", map[string]string{
			"action_kind": actionKind,
			"target":      target,
		})
		return &PolicyDeniedError{Decision: decision}
	}

	return nil
}

func (o *Orchestrator) recallMemory(ctx *nctx.ExecContext, message string) []map[string]string {
	if o.memory == nil {
		return nil
	}
	memCtx, err := o.memory.Recall(ctx, message)
	if err != nil {
		return nil
	}
	return memCtx.Conversation
}

func (o *Orchestrator) storeMemory(ctx *nctx.ExecContext, result runtime.AgentResult) {
	if o.memory == nil {
		return
	}
	if result.Status != runtime.StatusSuccess {
		return
	}
	event := memory.MemoryEvent{
		Content: result.Output,
		Tier:    memory.TierHot,
		Source:  result.Handler,
	}
	_ = o.memory.Store(ctx, event)
}

func pickHandler(intent router.IntentResult) string {
	best := intent.BestHandler()
	if best == nil {
		return FallbackHandler
	}
	return best.Name
}

func buildDelegationError(msg string) runtime.AgentResult {
	return runtime.AgentResult{
		Status: runtime.StatusError,
		Error:  msg,
	}
}
