// Package memory provides tiered context storage with scope isolation.
//
// Defines the Memory interface, MemoryContext, MemoryEvent, and MemoryTier.
package memory

import (
	nctx "github.com/otomus/nerva/go/context"
)

// MemoryTier is the storage tier for memory events.
type MemoryTier string

const (
	// TierHot is the current session state — in-memory, fast but ephemeral.
	TierHot MemoryTier = "hot"
	// TierWarm is recent episodes and facts — persisted in a key-value store.
	TierWarm MemoryTier = "warm"
	// TierCold is long-term knowledge — stored for semantic search.
	TierCold MemoryTier = "cold"
)

// MemoryEvent is an event to be stored in memory.
type MemoryEvent struct {
	Content string
	Tier    MemoryTier
	Scope   *nctx.Scope // nil means inherit from ctx
	Tags    map[string]bool
	Source  string
}

// MemoryContext is retrieved memory context for an agent.
type MemoryContext struct {
	Conversation []map[string]string
	Episodes     []string
	Facts        []string
	Knowledge    []string
	TokenCount   int
}

// Memory is tiered context storage that agents read from and write to.
type Memory interface {
	// Recall retrieves relevant context scoped by ctx.MemoryScope.
	Recall(ctx *nctx.ExecContext, query string) (MemoryContext, error)

	// Store stores an event in the appropriate tier and scope.
	Store(ctx *nctx.ExecContext, event MemoryEvent) error

	// Consolidate promotes, merges, or expires memories across tiers.
	Consolidate(ctx *nctx.ExecContext) error
}
