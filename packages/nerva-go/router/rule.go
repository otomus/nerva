package router

import (
	"fmt"
	"regexp"
	"strings"

	nctx "github.com/otomus/nerva/go/context"
)

const (
	// MatchConfidence is the confidence for a regex-matched rule.
	MatchConfidence = 1.0
	// DefaultConfidence is the confidence for the default fallback handler.
	DefaultConfidence = 0.5
	// NoMatchConfidence is the confidence when nothing matched.
	NoMatchConfidence = 0.0
	// DefaultIntent is the intent label for the default fallback handler.
	DefaultIntent = "default"
	// NoMatchIntent is the intent label when nothing matched.
	NoMatchIntent = "unknown"
)

// Rule maps a regex pattern to a handler.
type Rule struct {
	Pattern string
	Handler string
	Intent  string
}

// RuleRouter is a deterministic router using regex pattern matching.
// Tests each rule's regex in order; first match wins. Falls back to the
// default handler (with reduced confidence) or returns an empty result.
type RuleRouter struct {
	rules          []Rule
	compiled       []*regexp.Regexp
	defaultHandler string
}

// NewRuleRouter creates a new RuleRouter from the given rules.
// Returns an error if any pattern fails to compile.
func NewRuleRouter(rules []Rule, defaultHandler string) (*RuleRouter, error) {
	compiled := make([]*regexp.Regexp, len(rules))
	for i, rule := range rules {
		re, err := regexp.Compile("(?i)" + rule.Pattern)
		if err != nil {
			return nil, fmt.Errorf("invalid pattern %q: %w", rule.Pattern, err)
		}
		compiled[i] = re
	}
	return &RuleRouter{
		rules:          rules,
		compiled:       compiled,
		defaultHandler: defaultHandler,
	}, nil
}

// Classify classifies a message by testing rules in order.
func (r *RuleRouter) Classify(_ *nctx.ExecContext, message string) (IntentResult, error) {
	if strings.TrimSpace(message) == "" {
		return emptyResult(), nil
	}

	for i, re := range r.compiled {
		if re.FindString(message) != "" {
			return resultFromRule(r.rules[i]), nil
		}
	}

	if r.defaultHandler != "" {
		return defaultResult(r.defaultHandler), nil
	}

	return emptyResult(), nil
}

func resultFromRule(rule Rule) IntentResult {
	candidate, _ := NewHandlerCandidate(rule.Handler, MatchConfidence, "Matched pattern: "+rule.Pattern)
	result, _ := NewIntentResult(rule.Intent, MatchConfidence, []HandlerCandidate{candidate})
	return result
}

func defaultResult(handler string) IntentResult {
	candidate, _ := NewHandlerCandidate(handler, DefaultConfidence, "No rules matched; using default handler")
	result, _ := NewIntentResult(DefaultIntent, DefaultConfidence, []HandlerCandidate{candidate})
	return result
}

func emptyResult() IntentResult {
	result, _ := NewIntentResult(NoMatchIntent, NoMatchConfidence, nil)
	return result
}
