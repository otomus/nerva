import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/**
 * Storage tier for memory events.
 *
 * Each tier trades off speed for capacity:
 * - HOT: current session state, in-memory, fast but ephemeral.
 * - WARM: recent episodes and facts, persisted in a key-value store.
 * - COLD: long-term knowledge, stored in a vector database for semantic search.
 */
@jsonSchema
enum MemoryTier {
  hot: "hot",
  warm: "warm",
  cold: "cold",
}

/**
 * An event to be stored in memory.
 *
 * Immutable once created. A null scope means "inherit from the execution context".
 */
@jsonSchema
model MemoryEvent {
  /** The content to store. */
  content: string;

  /** Target storage tier. */
  tier: MemoryTier;

  /** Access scope for this memory. Null inherits from ctx. */
  scope?: Scope | null;

  /** Metadata tags for filtering and retrieval. */
  tags?: string[];

  /** Origin of this memory (agent name, tool, user). */
  source?: string;
}

/**
 * Retrieved memory context for an agent.
 *
 * Assembled by the memory system from one or more tiers.
 */
@jsonSchema
model MemoryContext {
  /** Recent conversation messages (role/content dicts). */
  conversation?: Record<string>[];

  /** Relevant past episodes from the warm tier. */
  episodes?: string[];

  /** Extracted facts from the warm tier. */
  facts?: string[];

  /** Long-term knowledge entries from the cold tier. */
  knowledge?: string[];

  /** Estimated tokens consumed by this context. */
  token_count?: int32;
}

/**
 * Memory interface — tiered context storage that agents read from and write to.
 *
 * Implementations provide three operations: recall (read),
 * store (write), and consolidate (maintenance).
 */
@jsonSchema
model MemoryContract {
  /** Retrieve relevant context scoped by ctx.memory_scope. */
  recall: {
    input: {
      /** Search query for relevant memories. */
      query: string;

      /** Execution context with memory scope and session identity. */
      ctx: ExecContext;
    };
    output: MemoryContext;
  };

  /** Store an event in the appropriate tier and scope. */
  store: {
    input: {
      /** Memory event to store. */
      event: MemoryEvent;

      /** Execution context providing scope and identity. */
      ctx: ExecContext;
    };
  };

  /** Promote, merge, or expire memories across tiers. */
  consolidate: {
    input: {
      /** Execution context. */
      ctx: ExecContext;
    };
  };
}
