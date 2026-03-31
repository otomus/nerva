// Package responder formats agent output for target channels.
//
// Defines the Responder interface, Channel, and Response types.
package responder

import (
	nctx "github.com/otomus/nerva/go/context"
	"github.com/otomus/nerva/go/runtime"
)

// Channel is the target channel for a response.
type Channel struct {
	Name             string
	SupportsMarkdown bool
	SupportsMedia    bool
	MaxLength        int // 0 = unlimited
}

// Response is a formatted response ready for delivery.
type Response struct {
	Text     string
	Channel  Channel
	Media    []string
	Metadata map[string]string
}

// APIChannel is the default channel for programmatic API consumers.
var APIChannel = Channel{Name: "api", SupportsMarkdown: false, SupportsMedia: true}

// WebSocketChannel is the default channel for WebSocket connections.
var WebSocketChannel = Channel{Name: "websocket", SupportsMarkdown: true, SupportsMedia: true}

// Responder formats agent output for a target channel.
type Responder interface {
	// Format adapts the raw AgentResult into a Response for the delivery channel.
	Format(ctx *nctx.ExecContext, output runtime.AgentResult, channel Channel) (Response, error)
}
