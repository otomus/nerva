/**
 * Memory — tiered context storage with scope isolation.
 *
 * Defines the core interfaces, enums, and value types for Nerva's
 * three-tier memory system (hot, warm, cold). All memory operations
 * are scoped by an {@link ExecContext}.
 *
 * @module memory
 */

import type { ExecContext, Scope } from "../context.js";

export type { ExecContext, Scope };

// ---------------------------------------------------------------------------
// MemoryTier
// ---------------------------------------------------------------------------

/**
 * Storage tier for memory events.
 *
 * Each tier trades off speed for capacity:
 * - HOT: current session state, in-memory, fast but ephemeral.
 * - WARM: recent episodes and facts, persisted in a key-value store.
 * - COLD: long-term knowledge, stored in a vector database for semantic search.
 */
export enum MemoryTier {
  HOT = "hot",
  WARM = "warm",
  COLD = "cold",
}

// ---------------------------------------------------------------------------
// MemoryEvent
// ---------------------------------------------------------------------------

/**
 * An immutable event to be stored in memory.
 *
 * The `scope` field controls visibility: `null` means "inherit from
 * the execution context".
 */
export interface MemoryEvent {
  /** The content to store. */
  readonly content: string;
  /** Target storage tier. */
  readonly tier: MemoryTier;
  /** Access scope for this memory. `null` inherits from ctx. */
  readonly scope: Scope | null;
  /** Metadata tags for filtering and retrieval. */
  readonly tags: ReadonlySet<string>;
  /** Origin of this memory (agent name, tool, user). */
  readonly source: string;
}

/**
 * Create a {@link MemoryEvent} with sensible defaults.
 *
 * @param content - The content to store.
 * @param overrides - Optional fields to override defaults.
 * @returns A frozen MemoryEvent.
 */
export function createMemoryEvent(
  content: string,
  overrides?: Partial<Omit<MemoryEvent, "content">>,
): MemoryEvent {
  return Object.freeze({
    content,
    tier: overrides?.tier ?? MemoryTier.HOT,
    scope: overrides?.scope ?? null,
    tags: overrides?.tags ?? new Set<string>(),
    source: overrides?.source ?? "",
  });
}

// ---------------------------------------------------------------------------
// MemoryContext
// ---------------------------------------------------------------------------

/** A single conversation message with a role and content. */
export interface Message {
  readonly role: string;
  readonly content: string;
}

/**
 * Retrieved memory context for an agent.
 *
 * Assembled by the memory system from one or more tiers. Consumers
 * use the fields directly to build LLM prompts or agent state.
 */
export interface MemoryContext {
  /** Recent conversation messages (role/content records). */
  readonly conversation: ReadonlyArray<Readonly<Message>>;
  /** Relevant past episodes from the warm tier. */
  readonly episodes: ReadonlyArray<string>;
  /** Extracted facts from the warm tier. */
  readonly facts: ReadonlyArray<string>;
  /** Long-term knowledge entries from the cold tier. */
  readonly knowledge: ReadonlyArray<string>;
  /** Estimated tokens consumed by this context. */
  readonly tokenCount: number;
}

/**
 * Create an empty {@link MemoryContext}.
 *
 * @returns A MemoryContext with all fields set to empty/zero.
 */
export function createEmptyMemoryContext(): MemoryContext {
  return {
    conversation: [],
    episodes: [],
    facts: [],
    knowledge: [],
    tokenCount: 0,
  };
}

// ---------------------------------------------------------------------------
// Memory interface
// ---------------------------------------------------------------------------

/**
 * Tiered context storage that agents read from and write to.
 *
 * Implementations must provide three operations: recall (read),
 * store (write), and consolidate (maintenance). All operations
 * are scoped by the {@link ExecContext}.
 */
export interface Memory {
  /**
   * Retrieve relevant context, scoped by `ctx.memoryScope`.
   *
   * @param query - Search query for relevant memories.
   * @param ctx - Execution context with memory scope and session identity.
   * @returns MemoryContext with relevant conversation, episodes, facts, knowledge.
   */
  recall(query: string, ctx: ExecContext): Promise<MemoryContext>;

  /**
   * Store an event in the appropriate tier and scope.
   *
   * @param event - Memory event to store.
   * @param ctx - Execution context providing scope and identity.
   */
  store(event: MemoryEvent, ctx: ExecContext): Promise<void>;

  /**
   * Promote, merge, or expire memories across tiers.
   *
   * Called periodically to move hot memories to warm, warm to cold,
   * and expire stale entries. The exact policy is implementation-defined.
   *
   * @param ctx - Execution context.
   */
  consolidate(ctx: ExecContext): Promise<void>;
}
