// Package tracing provides observability helpers for Nerva primitives.
//
// Spans and events are recorded on ExecContext directly. This package
// provides convenience functions for common tracing patterns.
package tracing

import (
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

// SpanCloser is returned by StartSpan and should be called when the work completes.
type SpanCloser func()

// StartSpan begins a named span on the context and returns a closer function.
// Call the closer when the work represented by the span is done.
func StartSpan(ctx *nctx.ExecContext, name string) SpanCloser {
	span := ctx.AddSpan(name)
	return func() {
		now := time.Now()
		span.EndedAt = &now
	}
}
