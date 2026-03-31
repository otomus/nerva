package memory

import (
	"fmt"
	"sync"

	nctx "github.com/otomus/nerva/go/context"
)

const (
	// DefaultTokenBudget is the maximum estimated tokens for recalled context.
	DefaultTokenBudget = 4000
	// CharsPerToken is the rough character-to-token ratio for budget estimation.
	CharsPerToken = 4
	// DefaultMaxMessages is the maximum conversation messages per session before pruning.
	DefaultMaxMessages = 100
)

// InMemoryHotMemory is an in-memory hot tier for session state.
type InMemoryHotMemory struct {
	mu            sync.Mutex
	maxMessages   int
	conversations map[string][]map[string]string
}

// NewInMemoryHotMemory creates a new in-memory hot memory store.
func NewInMemoryHotMemory(maxMessages int) *InMemoryHotMemory {
	if maxMessages <= 0 {
		maxMessages = DefaultMaxMessages
	}
	return &InMemoryHotMemory{
		maxMessages:   maxMessages,
		conversations: make(map[string][]map[string]string),
	}
}

// AddMessage appends a message to a session's conversation history.
func (h *InMemoryHotMemory) AddMessage(role, content, sessionID string) error {
	if role == "" {
		return fmt.Errorf("role must be a non-empty string")
	}
	if content == "" {
		return fmt.Errorf("content must be a non-empty string")
	}

	h.mu.Lock()
	defer h.mu.Unlock()

	msgs := h.conversations[sessionID]
	msgs = append(msgs, map[string]string{"role": role, "content": content})

	if len(msgs) > h.maxMessages {
		msgs = msgs[len(msgs)-h.maxMessages:]
	}

	h.conversations[sessionID] = msgs
	return nil
}

// GetConversation returns a copy of the conversation history for a session.
func (h *InMemoryHotMemory) GetConversation(sessionID string) []map[string]string {
	h.mu.Lock()
	defer h.mu.Unlock()

	msgs, ok := h.conversations[sessionID]
	if !ok {
		return nil
	}
	out := make([]map[string]string, len(msgs))
	copy(out, msgs)
	return out
}

// Clear removes all messages for a session.
func (h *InMemoryHotMemory) Clear(sessionID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.conversations, sessionID)
}

// TieredMemory orchestrates hot, warm, and cold tiers with scope isolation.
type TieredMemory struct {
	hot         *InMemoryHotMemory
	tokenBudget int
}

// NewTieredMemory creates a new tiered memory with the given hot tier.
func NewTieredMemory(hot *InMemoryHotMemory, tokenBudget int) *TieredMemory {
	if tokenBudget <= 0 {
		tokenBudget = DefaultTokenBudget
	}
	return &TieredMemory{
		hot:         hot,
		tokenBudget: tokenBudget,
	}
}

// Recall retrieves relevant context from all available tiers.
func (t *TieredMemory) Recall(ctx *nctx.ExecContext, query string) (MemoryContext, error) {
	sessionID := ctx.SessionID
	if sessionID == "" {
		sessionID = ctx.RequestID
	}

	var conversation []map[string]string
	if t.hot != nil {
		conversation = t.hot.GetConversation(sessionID)
	}

	return assembleWithinBudget(conversation, nil, nil, nil, t.tokenBudget), nil
}

// Store routes an event to the appropriate tier.
func (t *TieredMemory) Store(ctx *nctx.ExecContext, event MemoryEvent) error {
	if event.Tier == TierHot && t.hot != nil {
		sessionID := ctx.SessionID
		if sessionID == "" {
			sessionID = ctx.RequestID
		}
		source := event.Source
		if source == "" {
			source = "system"
		}
		return t.hot.AddMessage(source, event.Content, sessionID)
	}
	// Warm and cold tiers are no-op placeholders in this implementation
	return nil
}

// Consolidate is a no-op placeholder for tier promotion/expiry.
func (t *TieredMemory) Consolidate(_ *nctx.ExecContext) error {
	return nil
}

func assembleWithinBudget(
	conversation []map[string]string,
	episodes, facts, knowledge []string,
	budget int,
) MemoryContext {
	budgetRemaining := budget

	keptConversation := fitMessages(conversation, budgetRemaining)
	budgetRemaining -= estimateMessagesTokens(keptConversation)

	keptFacts := fitStrings(facts, budgetRemaining)
	budgetRemaining -= estimateStringsTokens(keptFacts)

	keptEpisodes := fitStrings(episodes, budgetRemaining)
	budgetRemaining -= estimateStringsTokens(keptEpisodes)

	keptKnowledge := fitStrings(knowledge, budgetRemaining)

	totalTokens := estimateMessagesTokens(keptConversation) +
		estimateStringsTokens(keptFacts) +
		estimateStringsTokens(keptEpisodes) +
		estimateStringsTokens(keptKnowledge)

	return MemoryContext{
		Conversation: keptConversation,
		Episodes:     keptEpisodes,
		Facts:        keptFacts,
		Knowledge:    keptKnowledge,
		TokenCount:   totalTokens,
	}
}

func fitMessages(messages []map[string]string, budget int) []map[string]string {
	if budget <= 0 || len(messages) == 0 {
		return nil
	}

	var kept []map[string]string
	used := 0
	// Walk backwards from newest
	for i := len(messages) - 1; i >= 0; i-- {
		cost := estimateStringTokens(messages[i]["content"])
		if used+cost > budget {
			break
		}
		kept = append([]map[string]string{messages[i]}, kept...)
		used += cost
	}
	return kept
}

func fitStrings(items []string, budget int) []string {
	if budget <= 0 || len(items) == 0 {
		return nil
	}

	var kept []string
	used := 0
	for _, item := range items {
		cost := estimateStringTokens(item)
		if used+cost > budget {
			break
		}
		kept = append(kept, item)
		used += cost
	}
	return kept
}

func estimateStringTokens(text string) int {
	if text == "" {
		return 0
	}
	tokens := len(text) / CharsPerToken
	if tokens < 1 {
		return 1
	}
	return tokens
}

func estimateStringsTokens(items []string) int {
	total := 0
	for _, item := range items {
		total += estimateStringTokens(item)
	}
	return total
}

func estimateMessagesTokens(messages []map[string]string) int {
	total := 0
	for _, msg := range messages {
		total += estimateStringTokens(msg["content"])
	}
	return total
}
