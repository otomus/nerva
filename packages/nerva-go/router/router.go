// Package router provides intent routing — classify messages and select handlers.
//
// Defines the IntentRouter interface and supporting value types.
// Strategy implementations (rule-based, embedding, LLM) satisfy this interface.
package router

import (
	"fmt"

	nctx "github.com/otomus/nerva/go/context"
)

const (
	// MinConfidence is the lower bound for classification confidence.
	MinConfidence = 0.0
	// MaxConfidence is the upper bound for classification confidence.
	MaxConfidence = 1.0
	// MinScore is the lower bound for handler match scores.
	MinScore = 0.0
	// MaxScore is the upper bound for handler match scores.
	MaxScore = 1.0
)

// HandlerCandidate is a candidate handler returned by the router.
type HandlerCandidate struct {
	Name   string
	Score  float64
	Reason string
}

// NewHandlerCandidate creates a validated HandlerCandidate.
// Returns an error if score is outside [0.0, 1.0].
func NewHandlerCandidate(name string, score float64, reason string) (HandlerCandidate, error) {
	if score < MinScore || score > MaxScore {
		return HandlerCandidate{}, fmt.Errorf("score must be between %.1f and %.1f, got %f", MinScore, MaxScore, score)
	}
	return HandlerCandidate{Name: name, Score: score, Reason: reason}, nil
}

// IntentResult is the result of intent classification.
type IntentResult struct {
	Intent     string
	Confidence float64
	Handlers   []HandlerCandidate
	RawScores  map[string]float64
}

// NewIntentResult creates a validated IntentResult.
// Returns an error if confidence is outside [0.0, 1.0].
func NewIntentResult(intent string, confidence float64, handlers []HandlerCandidate) (IntentResult, error) {
	if confidence < MinConfidence || confidence > MaxConfidence {
		return IntentResult{}, fmt.Errorf("confidence must be between %.1f and %.1f, got %f", MinConfidence, MaxConfidence, confidence)
	}
	return IntentResult{
		Intent:     intent,
		Confidence: confidence,
		Handlers:   handlers,
		RawScores:  make(map[string]float64),
	}, nil
}

// BestHandler returns the top-ranked handler, or nil if no candidates exist.
func (r *IntentResult) BestHandler() *HandlerCandidate {
	if len(r.Handlers) == 0 {
		return nil
	}
	return &r.Handlers[0]
}

// IntentRouter classifies a user message and selects the best handler.
type IntentRouter interface {
	// Classify determines the intent and returns ranked handler candidates.
	Classify(ctx *nctx.ExecContext, message string) (IntentResult, error)
}
