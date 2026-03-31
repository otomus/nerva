// Package policy provides layered rules governing execution.
//
// Defines the PolicyEngine interface, PolicyAction, PolicyDecision,
// and convenience constants.
package policy

import (
	nctx "github.com/otomus/nerva/go/context"
)

// PolicyAction is an action to be evaluated by the policy engine.
type PolicyAction struct {
	Kind     string
	Subject  string
	Target   string
	Metadata map[string]string
}

// PolicyDecision is the result of a policy evaluation.
type PolicyDecision struct {
	Allowed         bool
	Reason          string
	RequireApproval bool
	Approvers       []string
	BudgetRemaining *float64 // nil if not tracked
}

// Allow is a pre-built decision that permits the action unconditionally.
var Allow = PolicyDecision{Allowed: true}

// DenyNoReason is a pre-built denial with a generic reason string.
var DenyNoReason = PolicyDecision{Allowed: false, Reason: "denied by policy"}

// PolicyEngine evaluates and records policy decisions at every execution stage.
type PolicyEngine interface {
	// Evaluate determines whether an action is allowed under current policies.
	Evaluate(ctx *nctx.ExecContext, action PolicyAction) (PolicyDecision, error)

	// Record logs a policy decision for the audit trail.
	Record(ctx *nctx.ExecContext, action PolicyAction, decision PolicyDecision) error
}
