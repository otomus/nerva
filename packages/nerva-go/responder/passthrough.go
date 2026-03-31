package responder

import (
	nctx "github.com/otomus/nerva/go/context"
	"github.com/otomus/nerva/go/runtime"
)

// PassthroughResponder returns agent output as-is, without any transformation.
// Use for API consumers and programmatic access where the caller handles its own formatting.
type PassthroughResponder struct{}

// NewPassthroughResponder creates a new PassthroughResponder.
func NewPassthroughResponder() *PassthroughResponder {
	return &PassthroughResponder{}
}

// Format passes output through without transformation.
// Truncates output to channel.MaxLength when set.
func (p *PassthroughResponder) Format(_ *nctx.ExecContext, output runtime.AgentResult, channel Channel) (Response, error) {
	text := output.Output
	if channel.MaxLength > 0 && len(text) > channel.MaxLength {
		text = text[:channel.MaxLength]
	}
	return Response{
		Text:     text,
		Channel:  channel,
		Media:    nil,
		Metadata: make(map[string]string),
	}, nil
}
