// Package runtime provides agent execution with lifecycle management.
//
// Defines the AgentRuntime interface, value types (AgentInput, AgentResult),
// and the AgentStatus enum used across the Nerva execution layer.
package runtime

import (
	nctx "github.com/otomus/nerva/go/context"
)

// AgentStatus is the outcome status of an agent invocation.
type AgentStatus string

const (
	// StatusSuccess indicates the handler completed normally.
	StatusSuccess AgentStatus = "success"
	// StatusError indicates the handler raised an unrecoverable error.
	StatusError AgentStatus = "error"
	// StatusTimeout indicates the handler exceeded its deadline.
	StatusTimeout AgentStatus = "timeout"
	// StatusWrongHandler indicates the router selected the wrong handler.
	StatusWrongHandler AgentStatus = "wrong_handler"
	// StatusNeedsData indicates the handler requires additional data.
	StatusNeedsData AgentStatus = "needs_data"
	// StatusNeedsCredentials indicates the handler requires credentials.
	StatusNeedsCredentials AgentStatus = "needs_credentials"
)

// AgentInput is the immutable input passed to an agent handler.
type AgentInput struct {
	Message string
	Args    map[string]string
	Tools   []map[string]string
	History []map[string]string
}

// AgentResult is the result from an agent handler invocation.
type AgentResult struct {
	Status  AgentStatus
	Output  string
	Data    map[string]string
	Error   string
	Handler string
}

// AgentRuntime executes agent handlers with lifecycle management.
type AgentRuntime interface {
	// Invoke runs a single handler.
	Invoke(ctx *nctx.ExecContext, handler string, input AgentInput) (AgentResult, error)

	// InvokeChain runs handlers in sequence, piping each output as the next input's message.
	// Stops early if any handler returns a non-SUCCESS status.
	InvokeChain(ctx *nctx.ExecContext, handlers []string, input AgentInput) (AgentResult, error)

	// Delegate invokes a handler from within another handler (agent-to-agent delegation).
	// Creates a child ExecContext with inherited permissions and trace lineage.
	Delegate(ctx *nctx.ExecContext, handler string, input AgentInput) (AgentResult, error)
}
