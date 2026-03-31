package memory

import (
	"sync"
	"testing"

	nctx "github.com/otomus/nerva/go/context"
)

// --- InMemoryHotMemory ---

func TestNewInMemoryHotMemoryDefault(t *testing.T) {
	h := NewInMemoryHotMemory(0)
	if h == nil {
		t.Fatal("expected non-nil")
	}
	if h.maxMessages != DefaultMaxMessages {
		t.Fatalf("expected %d, got %d", DefaultMaxMessages, h.maxMessages)
	}
}

func TestNewInMemoryHotMemoryNegative(t *testing.T) {
	h := NewInMemoryHotMemory(-5)
	if h.maxMessages != DefaultMaxMessages {
		t.Fatalf("expected default for negative value, got %d", h.maxMessages)
	}
}

func TestAddMessageAndGet(t *testing.T) {
	h := NewInMemoryHotMemory(10)

	err := h.AddMessage("user", "hello", "sess1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	msgs := h.GetConversation("sess1")
	if len(msgs) != 1 {
		t.Fatalf("expected 1 message, got %d", len(msgs))
	}
	if msgs[0]["role"] != "user" {
		t.Fatalf("expected role 'user', got %q", msgs[0]["role"])
	}
	if msgs[0]["content"] != "hello" {
		t.Fatalf("expected content 'hello', got %q", msgs[0]["content"])
	}
}

func TestAddMessageEmptyRole(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	err := h.AddMessage("", "content", "sess")
	if err == nil {
		t.Fatal("expected error for empty role")
	}
}

func TestAddMessageEmptyContent(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	err := h.AddMessage("user", "", "sess")
	if err == nil {
		t.Fatal("expected error for empty content")
	}
}

func TestAddMessagePruning(t *testing.T) {
	h := NewInMemoryHotMemory(3)

	for i := 0; i < 5; i++ {
		h.AddMessage("user", "msg", "sess")
	}

	msgs := h.GetConversation("sess")
	if len(msgs) != 3 {
		t.Fatalf("expected 3 after pruning, got %d", len(msgs))
	}
}

func TestGetConversationUnknownSession(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	msgs := h.GetConversation("nonexistent")
	if msgs != nil {
		t.Fatalf("expected nil for unknown session, got %v", msgs)
	}
}

func TestGetConversationReturnsSliceCopy(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	h.AddMessage("user", "hello", "sess")

	msgs := h.GetConversation("sess")
	// Appending to the returned slice should not affect the original
	msgs = append(msgs, map[string]string{"role": "injected", "content": "extra"})

	original := h.GetConversation("sess")
	if len(original) != 1 {
		t.Fatal("GetConversation should return a slice copy")
	}
}

func TestClear(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	h.AddMessage("user", "hello", "sess")
	h.Clear("sess")

	msgs := h.GetConversation("sess")
	if msgs != nil {
		t.Fatal("expected nil after clear")
	}
}

func TestClearNonexistent(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	// Should not panic
	h.Clear("nonexistent")
}

func TestSessionIsolation(t *testing.T) {
	h := NewInMemoryHotMemory(10)
	h.AddMessage("user", "msg1", "sess1")
	h.AddMessage("user", "msg2", "sess2")

	if len(h.GetConversation("sess1")) != 1 {
		t.Fatal("expected isolation between sessions")
	}
	if len(h.GetConversation("sess2")) != 1 {
		t.Fatal("expected isolation between sessions")
	}
}

func TestConcurrentAddMessage(t *testing.T) {
	h := NewInMemoryHotMemory(1000)
	var wg sync.WaitGroup
	count := 100

	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			h.AddMessage("user", "msg", "sess")
		}()
	}
	wg.Wait()

	msgs := h.GetConversation("sess")
	if len(msgs) != count {
		t.Fatalf("expected %d, got %d", count, len(msgs))
	}
}

// --- TieredMemory ---

func TestNewTieredMemoryDefaults(t *testing.T) {
	tm := NewTieredMemory(nil, 0)
	if tm.tokenBudget != DefaultTokenBudget {
		t.Fatalf("expected %d, got %d", DefaultTokenBudget, tm.tokenBudget)
	}
}

func TestTieredMemoryRecallEmpty(t *testing.T) {
	hot := NewInMemoryHotMemory(10)
	tm := NewTieredMemory(hot, 4000)
	ctx := nctx.NewContext(nctx.WithSessionID("sess"))

	mc, err := tm.Recall(ctx, "query")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mc.Conversation != nil {
		t.Fatalf("expected nil conversation, got %v", mc.Conversation)
	}
}

func TestTieredMemoryStoreAndRecall(t *testing.T) {
	hot := NewInMemoryHotMemory(10)
	tm := NewTieredMemory(hot, 4000)
	ctx := nctx.NewContext(nctx.WithSessionID("sess"))

	err := tm.Store(ctx, MemoryEvent{
		Content: "stored message",
		Tier:    TierHot,
		Source:  "user",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	mc, _ := tm.Recall(ctx, "query")
	if len(mc.Conversation) != 1 {
		t.Fatalf("expected 1 message, got %d", len(mc.Conversation))
	}
	if mc.Conversation[0]["content"] != "stored message" {
		t.Fatalf("expected 'stored message', got %q", mc.Conversation[0]["content"])
	}
}

func TestTieredMemoryStoreDefaultSource(t *testing.T) {
	hot := NewInMemoryHotMemory(10)
	tm := NewTieredMemory(hot, 4000)
	ctx := nctx.NewContext(nctx.WithSessionID("sess"))

	err := tm.Store(ctx, MemoryEvent{
		Content: "msg",
		Tier:    TierHot,
		Source:  "", // should default to "system"
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	mc, _ := tm.Recall(ctx, "")
	if mc.Conversation[0]["role"] != "system" {
		t.Fatalf("expected role 'system', got %q", mc.Conversation[0]["role"])
	}
}

func TestTieredMemoryStoreWarmIsNoOp(t *testing.T) {
	hot := NewInMemoryHotMemory(10)
	tm := NewTieredMemory(hot, 4000)
	ctx := nctx.NewContext(nctx.WithSessionID("sess"))

	err := tm.Store(ctx, MemoryEvent{Content: "warm data", Tier: TierWarm})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	mc, _ := tm.Recall(ctx, "")
	if len(mc.Conversation) != 0 {
		t.Fatal("warm store should be no-op")
	}
}

func TestTieredMemoryRecallFallsBackToRequestID(t *testing.T) {
	hot := NewInMemoryHotMemory(10)
	tm := NewTieredMemory(hot, 4000)
	// No SessionID — should use RequestID
	ctx := nctx.NewContext()

	tm.Store(ctx, MemoryEvent{Content: "msg", Tier: TierHot, Source: "user"})
	mc, _ := tm.Recall(ctx, "")
	if len(mc.Conversation) != 1 {
		t.Fatal("expected recall to work using RequestID as fallback")
	}
}

func TestTieredMemoryConsolidateNoOp(t *testing.T) {
	tm := NewTieredMemory(nil, 4000)
	ctx := nctx.NewContext()
	err := tm.Consolidate(ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestTieredMemoryNilHot(t *testing.T) {
	tm := NewTieredMemory(nil, 4000)
	ctx := nctx.NewContext(nctx.WithSessionID("sess"))

	mc, err := tm.Recall(ctx, "query")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if mc.Conversation != nil {
		t.Fatal("expected nil conversation with nil hot memory")
	}
}

// --- Budget estimation ---

func TestEstimateStringTokens(t *testing.T) {
	if estimateStringTokens("") != 0 {
		t.Fatal("empty string should be 0 tokens")
	}
	// "ab" = 2 chars, 2/4 = 0, but min is 1
	if estimateStringTokens("ab") != 1 {
		t.Fatalf("expected 1 for short string, got %d", estimateStringTokens("ab"))
	}
	// "abcdefgh" = 8 chars, 8/4 = 2
	if estimateStringTokens("abcdefgh") != 2 {
		t.Fatalf("expected 2, got %d", estimateStringTokens("abcdefgh"))
	}
}

func TestAssembleWithinBudgetRespectsBudget(t *testing.T) {
	msgs := []map[string]string{
		{"content": "short"},
		{"content": "another short message here for testing"},
	}

	// Very small budget: should keep only newest that fits
	mc := assembleWithinBudget(msgs, nil, nil, nil, 2)
	if len(mc.Conversation) > 1 {
		t.Fatal("budget should limit messages")
	}
}

func TestAssembleWithinBudgetZeroBudget(t *testing.T) {
	msgs := []map[string]string{{"content": "hello"}}
	mc := assembleWithinBudget(msgs, nil, nil, nil, 0)
	if len(mc.Conversation) != 0 {
		t.Fatal("zero budget should return no messages")
	}
}

func TestFitMessagesWalkFromNewest(t *testing.T) {
	msgs := []map[string]string{
		{"content": "old message with lots of content"},
		{"content": "new"},
	}
	kept := fitMessages(msgs, 2)
	// Budget is 2 tokens; "new" = 1 token, "old..." = 7 tokens
	if len(kept) != 1 {
		t.Fatalf("expected 1, got %d", len(kept))
	}
	if kept[0]["content"] != "new" {
		t.Fatal("should keep newest message first")
	}
}

func TestFitStringsEmpty(t *testing.T) {
	result := fitStrings(nil, 100)
	if result != nil {
		t.Fatal("expected nil for empty input")
	}
}
