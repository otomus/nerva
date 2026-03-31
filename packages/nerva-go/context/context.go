// Package context provides the execution context that flows through every Nerva primitive.
//
// Every operation in Nerva receives an ExecContext. It carries identity, permissions,
// observability (spans/events), token accounting, cancellation, and depth tracking.
package context

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"
)

// Scope defines memory isolation boundaries for context data.
type Scope string

const (
	// ScopeUser persists across sessions for the same user.
	ScopeUser Scope = "user"
	// ScopeSession is scoped to a single conversation session.
	ScopeSession Scope = "session"
	// ScopeAgent is private to the agent handling the request.
	ScopeAgent Scope = "agent"
	// ScopeGlobal is visible to all users and agents.
	ScopeGlobal Scope = "global"
)

// DefaultMemoryScope is the default scope when none is specified.
const DefaultMemoryScope = ScopeSession

// Permissions is an immutable capability set governing what a context is allowed to do.
// Nil allowlists mean "no restriction" (all allowed). An empty set means "none allowed".
type Permissions struct {
	Roles        map[string]bool
	AllowedTools *map[string]bool // nil means unrestricted
	AllowedAgents *map[string]bool // nil means unrestricted
}

// CanUseTool checks whether the given tool is permitted.
func (p *Permissions) CanUseTool(toolName string) bool {
	if p.AllowedTools == nil {
		return true
	}
	return (*p.AllowedTools)[toolName]
}

// CanUseAgent checks whether delegation to the given agent is permitted.
func (p *Permissions) CanUseAgent(agentName string) bool {
	if p.AllowedAgents == nil {
		return true
	}
	return (*p.AllowedAgents)[agentName]
}

// HasRole checks whether the context carries a specific role.
func (p *Permissions) HasRole(role string) bool {
	return p.Roles[role]
}

// TokenUsage accumulates LLM token consumption and estimated cost.
type TokenUsage struct {
	mu               sync.Mutex
	PromptTokens     int
	CompletionTokens int
	TotalTokens      int
	CostUSD          float64
}

// Add returns a new TokenUsage that is the sum of this and other.
func (t *TokenUsage) Add(other *TokenUsage) *TokenUsage {
	t.mu.Lock()
	defer t.mu.Unlock()
	other.mu.Lock()
	defer other.mu.Unlock()
	return &TokenUsage{
		PromptTokens:     t.PromptTokens + other.PromptTokens,
		CompletionTokens: t.CompletionTokens + other.CompletionTokens,
		TotalTokens:      t.TotalTokens + other.TotalTokens,
		CostUSD:          t.CostUSD + other.CostUSD,
	}
}

// Accumulate adds another TokenUsage's values into this one (mutating).
func (t *TokenUsage) Accumulate(other *TokenUsage) {
	t.mu.Lock()
	defer t.mu.Unlock()
	other.mu.Lock()
	defer other.mu.Unlock()
	t.PromptTokens += other.PromptTokens
	t.CompletionTokens += other.CompletionTokens
	t.TotalTokens += other.TotalTokens
	t.CostUSD += other.CostUSD
}

// Span is a timed segment of work within a request's lifecycle.
type Span struct {
	SpanID     string
	Name       string
	ParentID   string
	StartedAt  time.Time
	EndedAt    *time.Time
	Attributes map[string]string
}

// Event is a point-in-time occurrence recorded within a context.
type Event struct {
	Timestamp  time.Time
	Name       string
	Attributes map[string]string
}

// ExecContext is the execution context that flows through every Nerva primitive.
type ExecContext struct {
	RequestID   string
	TraceID     string
	UserID      string
	SessionID   string
	Permissions Permissions
	MemoryScope Scope
	Depth       int
	Metadata    map[string]string
	CreatedAt   time.Time
	TimeoutAt   *time.Time
	TokenUsage  *TokenUsage

	ctx    context.Context
	cancel context.CancelFunc

	mu     sync.Mutex
	spans  []Span
	events []Event
}

// NewContext creates a new root execution context with the given options.
func NewContext(opts ...Option) *ExecContext {
	cfg := defaultConfig()
	for _, o := range opts {
		o(&cfg)
	}

	now := time.Now()
	ctx, cancel := context.WithCancel(context.Background())

	ec := &ExecContext{
		RequestID:   generateID(),
		TraceID:     generateID(),
		UserID:      cfg.userID,
		SessionID:   cfg.sessionID,
		Permissions: cfg.permissions,
		MemoryScope: cfg.memoryScope,
		Depth:       0,
		Metadata:    make(map[string]string),
		CreatedAt:   now,
		TokenUsage:  &TokenUsage{},
		ctx:         ctx,
		cancel:      cancel,
		spans:       make([]Span, 0),
		events:      make([]Event, 0),
	}

	if cfg.timeoutSeconds > 0 {
		deadline := now.Add(time.Duration(cfg.timeoutSeconds * float64(time.Second)))
		ec.TimeoutAt = &deadline
	}

	return ec
}

// Child creates a child context for delegation to a sub-handler.
// The child inherits the parent's trace, permissions, memory scope, and timeout
// but gets a fresh RequestID and incremented depth.
func (ec *ExecContext) Child(handlerName string) *ExecContext {
	childRequestID := generateID()
	now := time.Now()

	rootSpan := Span{
		SpanID:     generateID(),
		Name:       handlerName,
		ParentID:   ec.RequestID,
		StartedAt:  now,
		Attributes: make(map[string]string),
	}

	// Copy metadata
	meta := make(map[string]string, len(ec.Metadata))
	for k, v := range ec.Metadata {
		meta[k] = v
	}

	child := &ExecContext{
		RequestID:   childRequestID,
		TraceID:     ec.TraceID,
		UserID:      ec.UserID,
		SessionID:   ec.SessionID,
		Permissions: ec.Permissions,
		MemoryScope: ec.MemoryScope,
		Depth:       ec.Depth + 1,
		Metadata:    meta,
		CreatedAt:   now,
		TimeoutAt:   ec.TimeoutAt,
		TokenUsage:  &TokenUsage{},
		ctx:         ec.ctx,
		cancel:      ec.cancel,
		spans:       []Span{rootSpan},
		events:      make([]Event, 0),
	}

	return child
}

// IsTimedOut checks whether the context has exceeded its timeout.
func (ec *ExecContext) IsTimedOut() bool {
	if ec.TimeoutAt == nil {
		return false
	}
	return time.Now().After(*ec.TimeoutAt)
}

// IsCancelled checks whether cancellation has been signalled.
func (ec *ExecContext) IsCancelled() bool {
	return ec.ctx.Err() != nil
}

// Cancel signals cancellation for this context and all children.
func (ec *ExecContext) Cancel() {
	ec.cancel()
}

// Context returns the underlying context.Context for use with standard library functions.
func (ec *ExecContext) Context() context.Context {
	return ec.ctx
}

// ElapsedSeconds returns the wall-clock seconds since this context was created.
func (ec *ExecContext) ElapsedSeconds() float64 {
	return time.Since(ec.CreatedAt).Seconds()
}

// AddSpan starts a new span and appends it to this context's span list.
func (ec *ExecContext) AddSpan(name string) Span {
	span := Span{
		SpanID:     generateID(),
		Name:       name,
		ParentID:   ec.RequestID,
		StartedAt:  time.Now(),
		Attributes: make(map[string]string),
	}
	ec.mu.Lock()
	ec.spans = append(ec.spans, span)
	ec.mu.Unlock()
	return span
}

// AddEvent records a point-in-time event in this context.
func (ec *ExecContext) AddEvent(name string, attributes map[string]string) Event {
	if attributes == nil {
		attributes = make(map[string]string)
	}
	event := Event{
		Timestamp:  time.Now(),
		Name:       name,
		Attributes: attributes,
	}
	ec.mu.Lock()
	ec.events = append(ec.events, event)
	ec.mu.Unlock()
	return event
}

// Spans returns a copy of the span list.
func (ec *ExecContext) Spans() []Span {
	ec.mu.Lock()
	defer ec.mu.Unlock()
	out := make([]Span, len(ec.spans))
	copy(out, ec.spans)
	return out
}

// Events returns a copy of the event list.
func (ec *ExecContext) Events() []Event {
	ec.mu.Lock()
	defer ec.mu.Unlock()
	out := make([]Event, len(ec.events))
	copy(out, ec.events)
	return out
}

// RecordTokens accumulates token usage into this context's running total.
func (ec *ExecContext) RecordTokens(usage *TokenUsage) {
	ec.TokenUsage.Accumulate(usage)
}

// Option configures a new ExecContext.
type Option func(*config)

type config struct {
	userID         string
	sessionID      string
	permissions    Permissions
	memoryScope    Scope
	timeoutSeconds float64
}

func defaultConfig() config {
	return config{
		permissions: Permissions{Roles: make(map[string]bool)},
		memoryScope: DefaultMemoryScope,
	}
}

// WithUserID sets the user identifier.
func WithUserID(id string) Option {
	return func(c *config) { c.userID = id }
}

// WithSessionID sets the session identifier.
func WithSessionID(id string) Option {
	return func(c *config) { c.sessionID = id }
}

// WithPermissions sets the permission set.
func WithPermissions(p Permissions) Option {
	return func(c *config) { c.permissions = p }
}

// WithMemoryScope sets the memory scope.
func WithMemoryScope(s Scope) Option {
	return func(c *config) { c.memoryScope = s }
}

// WithTimeout sets the timeout in seconds.
func WithTimeout(seconds float64) Option {
	return func(c *config) { c.timeoutSeconds = seconds }
}

func generateID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
