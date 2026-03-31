package responder

import (
	"strings"
	"testing"

	nctx "github.com/otomus/nerva/go/context"
	"github.com/otomus/nerva/go/runtime"
)

// --- Channel constants ---

func TestAPIChannelDefaults(t *testing.T) {
	if APIChannel.Name != "api" {
		t.Fatalf("expected 'api', got %q", APIChannel.Name)
	}
	if APIChannel.SupportsMarkdown {
		t.Fatal("API channel should not support markdown")
	}
	if !APIChannel.SupportsMedia {
		t.Fatal("API channel should support media")
	}
}

func TestWebSocketChannelDefaults(t *testing.T) {
	if WebSocketChannel.Name != "websocket" {
		t.Fatalf("expected 'websocket', got %q", WebSocketChannel.Name)
	}
	if !WebSocketChannel.SupportsMarkdown {
		t.Fatal("WebSocket channel should support markdown")
	}
}

// --- PassthroughResponder ---

func TestNewPassthroughResponder(t *testing.T) {
	r := NewPassthroughResponder()
	if r == nil {
		t.Fatal("expected non-nil responder")
	}
}

func TestFormatPassthrough(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{
		Status: runtime.StatusSuccess,
		Output: "hello world",
	}

	resp, err := r.Format(ctx, output, APIChannel)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Text != "hello world" {
		t.Fatalf("expected 'hello world', got %q", resp.Text)
	}
	if resp.Channel.Name != APIChannel.Name {
		t.Fatal("expected API channel")
	}
	if resp.Media != nil {
		t.Fatal("expected nil media")
	}
	if resp.Metadata == nil {
		t.Fatal("expected non-nil metadata map")
	}
}

func TestFormatTruncation(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{Output: "abcdefghij"}

	ch := Channel{Name: "short", MaxLength: 5}
	resp, _ := r.Format(ctx, output, ch)

	if resp.Text != "abcde" {
		t.Fatalf("expected 'abcde', got %q", resp.Text)
	}
}

func TestFormatNoTruncationWithZeroMaxLength(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	longText := strings.Repeat("a", 10000)
	output := runtime.AgentResult{Output: longText}

	ch := Channel{Name: "unlimited", MaxLength: 0}
	resp, _ := r.Format(ctx, output, ch)

	if len(resp.Text) != 10000 {
		t.Fatalf("expected no truncation, got length %d", len(resp.Text))
	}
}

func TestFormatEmptyOutput(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{Output: ""}

	resp, err := r.Format(ctx, output, APIChannel)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Text != "" {
		t.Fatalf("expected empty text, got %q", resp.Text)
	}
}

func TestFormatErrorStatus(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{
		Status: runtime.StatusError,
		Output: "error output",
		Error:  "something went wrong",
	}

	resp, err := r.Format(ctx, output, APIChannel)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Passthrough does not interpret status — just passes output
	if resp.Text != "error output" {
		t.Fatalf("expected 'error output', got %q", resp.Text)
	}
}

func TestFormatTruncationBoundary(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{Output: "exact"}

	ch := Channel{Name: "exact", MaxLength: 5}
	resp, _ := r.Format(ctx, output, ch)
	if resp.Text != "exact" {
		t.Fatalf("expected 'exact', got %q", resp.Text)
	}
}

func TestFormatTruncationShorterThanMax(t *testing.T) {
	r := NewPassthroughResponder()
	ctx := nctx.NewContext()
	output := runtime.AgentResult{Output: "hi"}

	ch := Channel{Name: "big", MaxLength: 100}
	resp, _ := r.Format(ctx, output, ch)
	if resp.Text != "hi" {
		t.Fatalf("expected 'hi', got %q", resp.Text)
	}
}
