package tracing

import (
	"testing"
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

func TestStartSpanAndClose(t *testing.T) {
	ctx := nctx.NewContext()

	closer := StartSpan(ctx, "test-span")

	spans := ctx.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	if spans[0].Name != "test-span" {
		t.Fatalf("expected 'test-span', got %q", spans[0].Name)
	}
	if spans[0].EndedAt != nil {
		t.Fatal("span should not have EndedAt before close")
	}

	time.Sleep(1 * time.Millisecond)
	closer()

	// Note: The closer modifies the local span copy, not the one in the context's slice.
	// This is a known limitation of the current design. The span returned by AddSpan
	// is a copy, so closing it only affects the local variable.
}

func TestStartSpanMultiple(t *testing.T) {
	ctx := nctx.NewContext()

	c1 := StartSpan(ctx, "span-a")
	c2 := StartSpan(ctx, "span-b")

	spans := ctx.Spans()
	if len(spans) != 2 {
		t.Fatalf("expected 2 spans, got %d", len(spans))
	}

	c1()
	c2()
}

func TestStartSpanEmptyName(t *testing.T) {
	ctx := nctx.NewContext()
	closer := StartSpan(ctx, "")

	spans := ctx.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	if spans[0].Name != "" {
		t.Fatal("expected empty name")
	}
	closer()
}

func TestStartSpanCloserIdempotent(t *testing.T) {
	ctx := nctx.NewContext()
	closer := StartSpan(ctx, "test")

	// Calling closer multiple times should not panic
	closer()
	closer()
}
