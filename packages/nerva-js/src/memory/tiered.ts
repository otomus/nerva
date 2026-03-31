/**
 * Tiered memory — orchestrates hot, warm, and cold tiers with scope isolation.
 *
 * Assembles {@link MemoryContext} by querying each tier independently and
 * merging results under a token budget. Each tier is optional; missing
 * tiers produce empty results.
 *
 * @module memory/tiered
 */

import type { ExecContext } from "../context.js";
import type { MemoryContext, MemoryEvent, Message } from "./index.js";
import { MemoryTier } from "./index.js";
import type { InMemoryHotMemory } from "./hot.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum estimated tokens for recalled context. */
export const DEFAULT_TOKEN_BUDGET = 4000;

/** Rough character-to-token ratio for budget estimation. */
export const CHARS_PER_TOKEN = 4;

// ---------------------------------------------------------------------------
// Tier protocols — minimal interfaces for warm and cold backends
// ---------------------------------------------------------------------------

/**
 * Key-value store for episodes and facts (warm tier).
 */
export interface WarmTier {
  /**
   * Retrieve relevant episodes for a query.
   *
   * @param query - Search query.
   * @param sessionId - Session scope.
   * @returns List of episode strings, most relevant first.
   */
  getEpisodes(query: string, sessionId: string): Promise<string[]>;

  /**
   * Retrieve relevant facts for a query.
   *
   * @param query - Search query.
   * @param sessionId - Session scope.
   * @returns List of fact strings, most relevant first.
   */
  getFacts(query: string, sessionId: string): Promise<string[]>;

  /**
   * Store content in the warm tier.
   *
   * @param content - Content to persist.
   * @param sessionId - Session scope.
   */
  store(content: string, sessionId: string): Promise<void>;
}

/**
 * Vector search store for long-term knowledge (cold tier).
 */
export interface ColdTier {
  /**
   * Search for relevant knowledge entries.
   *
   * @param query - Semantic search query.
   * @param scope - Memory scope string for filtering.
   * @returns List of knowledge strings, most relevant first.
   */
  search(query: string, scope: string): Promise<string[]>;

  /**
   * Store content in the cold tier.
   *
   * @param content - Content to persist.
   * @param scope - Memory scope string for filtering.
   */
  store(content: string, scope: string): Promise<void>;
}

// ---------------------------------------------------------------------------
// TieredMemory options
// ---------------------------------------------------------------------------

/** Configuration options for {@link TieredMemory}. */
export interface TieredMemoryOptions {
  /** Hot tier implementation (session state). */
  readonly hot?: InMemoryHotMemory | undefined;
  /** Warm tier implementation (episodes/facts). */
  readonly warm?: WarmTier | undefined;
  /** Cold tier implementation (vector search). */
  readonly cold?: ColdTier | undefined;
  /** Maximum estimated tokens for recalled context. */
  readonly tokenBudget?: number | undefined;
}

// ---------------------------------------------------------------------------
// TieredMemory
// ---------------------------------------------------------------------------

/**
 * Memory implementation that orchestrates three tiers.
 *
 * - Hot: session conversation (in-memory or external store).
 * - Warm: episodes and facts (key-value store).
 * - Cold: long-term knowledge (vector search).
 *
 * Each tier is optional. If a tier is not provided, that part
 * of `recall` returns empty results and `store` is a no-op
 * for that tier.
 */
export class TieredMemory {
  private readonly hot: InMemoryHotMemory | null;
  private readonly warm: WarmTier | null;
  private readonly cold: ColdTier | null;
  private readonly tokenBudget: number;

  /**
   * @param options - Configuration for the three tiers and token budget.
   */
  constructor(options: TieredMemoryOptions = {}) {
    this.hot = options.hot ?? null;
    this.warm = options.warm ?? null;
    this.cold = options.cold ?? null;
    this.tokenBudget = options.tokenBudget ?? DEFAULT_TOKEN_BUDGET;
  }

  /**
   * Retrieve relevant context from all available tiers.
   *
   * Queries each tier independently, then assembles and truncates
   * results to fit within the token budget.
   *
   * @param query - Search query for relevant memories.
   * @param ctx - Execution context with session identity and memory scope.
   * @returns MemoryContext assembled from all available tiers.
   */
  async recall(query: string, ctx: ExecContext): Promise<MemoryContext> {
    const sessionId = ctx.sessionId ?? ctx.requestId;
    const scopeValue = ctx.memoryScope;

    const conversation = await this.recallHot(sessionId);
    const episodes = await this.recallWarmEpisodes(query, sessionId);
    const facts = await this.recallWarmFacts(query, sessionId);
    const knowledge = await this.recallCold(query, scopeValue);

    return this.assembleWithinBudget(conversation, episodes, facts, knowledge);
  }

  /**
   * Route an event to the appropriate tier based on `event.tier`.
   *
   * @param event - Memory event to store.
   * @param ctx - Execution context providing scope and identity.
   */
  async store(event: MemoryEvent, ctx: ExecContext): Promise<void> {
    const sessionId = ctx.sessionId ?? ctx.requestId;
    const scopeValue = event.scope ?? ctx.memoryScope;

    if (event.tier === MemoryTier.HOT) {
      await this.storeHot(event, sessionId);
    } else if (event.tier === MemoryTier.WARM) {
      await this.storeWarm(event, sessionId);
    } else if (event.tier === MemoryTier.COLD) {
      await this.storeCold(event, scopeValue);
    }
  }

  /**
   * Promote, merge, or expire memories across tiers.
   *
   * Currently a no-op placeholder. Future implementations will
   * move hot conversations into warm episodes and warm facts
   * into cold knowledge based on age and relevance signals.
   *
   * @param _ctx - Execution context (unused in placeholder).
   */
  async consolidate(_ctx: ExecContext): Promise<void> {
    // No-op placeholder for future tier promotion logic.
  }

  // -- Hot tier helpers ---------------------------------------------------

  /**
   * Retrieve conversation from the hot tier.
   *
   * @param sessionId - Session to retrieve.
   * @returns List of message records, or empty if no hot tier.
   */
  private async recallHot(sessionId: string): Promise<Message[]> {
    if (!this.hot) {
      return [];
    }
    return this.hot.getConversation(sessionId);
  }

  /**
   * Store a message in the hot tier.
   *
   * @param event - Memory event with content to store.
   * @param sessionId - Target session.
   */
  private async storeHot(event: MemoryEvent, sessionId: string): Promise<void> {
    if (!this.hot) {
      return;
    }
    await this.hot.addMessage(
      event.source || "system",
      event.content,
      sessionId,
    );
  }

  // -- Warm tier helpers --------------------------------------------------

  /**
   * Retrieve episodes from the warm tier.
   *
   * @param query - Search query.
   * @param sessionId - Session scope.
   * @returns List of episode strings, or empty if no warm tier.
   */
  private async recallWarmEpisodes(query: string, sessionId: string): Promise<string[]> {
    if (!this.warm) {
      return [];
    }
    return this.warm.getEpisodes(query, sessionId);
  }

  /**
   * Retrieve facts from the warm tier.
   *
   * @param query - Search query.
   * @param sessionId - Session scope.
   * @returns List of fact strings, or empty if no warm tier.
   */
  private async recallWarmFacts(query: string, sessionId: string): Promise<string[]> {
    if (!this.warm) {
      return [];
    }
    return this.warm.getFacts(query, sessionId);
  }

  /**
   * Store content in the warm tier.
   *
   * @param event - Memory event with content to store.
   * @param sessionId - Target session.
   */
  private async storeWarm(event: MemoryEvent, sessionId: string): Promise<void> {
    if (!this.warm) {
      return;
    }
    await this.warm.store(event.content, sessionId);
  }

  // -- Cold tier helpers --------------------------------------------------

  /**
   * Retrieve knowledge from the cold tier.
   *
   * @param query - Semantic search query.
   * @param scope - Memory scope for filtering.
   * @returns List of knowledge strings, or empty if no cold tier.
   */
  private async recallCold(query: string, scope: string): Promise<string[]> {
    if (!this.cold) {
      return [];
    }
    return this.cold.search(query, scope);
  }

  /**
   * Store content in the cold tier.
   *
   * @param event - Memory event with content to store.
   * @param scope - Memory scope for filtering.
   */
  private async storeCold(event: MemoryEvent, scope: string): Promise<void> {
    if (!this.cold) {
      return;
    }
    await this.cold.store(event.content, scope);
  }

  // -- Budget management --------------------------------------------------

  /**
   * Assemble a MemoryContext, truncating to fit the token budget.
   *
   * Priority order: conversation > facts > episodes > knowledge.
   * Each category is trimmed until the total fits within the budget.
   *
   * @param conversation - Conversation messages.
   * @param episodes - Episode strings.
   * @param facts - Fact strings.
   * @param knowledge - Knowledge strings.
   * @returns A MemoryContext that fits within the configured token budget.
   */
  private assembleWithinBudget(
    conversation: Message[],
    episodes: string[],
    facts: string[],
    knowledge: string[],
  ): MemoryContext {
    let budgetRemaining = this.tokenBudget;

    const keptConversation = fitMessages(conversation, budgetRemaining);
    budgetRemaining -= estimateMessagesTokens(keptConversation);

    const keptFacts = fitStrings(facts, budgetRemaining);
    budgetRemaining -= estimateStringsTokens(keptFacts);

    const keptEpisodes = fitStrings(episodes, budgetRemaining);
    budgetRemaining -= estimateStringsTokens(keptEpisodes);

    const keptKnowledge = fitStrings(knowledge, budgetRemaining);

    const totalTokens =
      estimateMessagesTokens(keptConversation) +
      estimateStringsTokens(keptFacts) +
      estimateStringsTokens(keptEpisodes) +
      estimateStringsTokens(keptKnowledge);

    return {
      conversation: keptConversation,
      episodes: keptEpisodes,
      facts: keptFacts,
      knowledge: keptKnowledge,
      tokenCount: totalTokens,
    };
  }
}

// ---------------------------------------------------------------------------
// Token estimation helpers
// ---------------------------------------------------------------------------

/**
 * Estimate token count for a string using a character-based heuristic.
 *
 * @param text - Input text.
 * @returns Estimated token count (at least 1 for non-empty text).
 */
export function estimateStringTokens(text: string): number {
  if (!text) {
    return 0;
  }
  return Math.max(1, Math.floor(text.length / CHARS_PER_TOKEN));
}

/**
 * Estimate total tokens for a list of strings.
 *
 * @param items - List of text strings.
 * @returns Sum of estimated token counts.
 */
export function estimateStringsTokens(items: ReadonlyArray<string>): number {
  let total = 0;
  for (const item of items) {
    total += estimateStringTokens(item);
  }
  return total;
}

/**
 * Estimate total tokens for conversation messages.
 *
 * @param messages - List of role/content records.
 * @returns Sum of estimated token counts for all message contents.
 */
export function estimateMessagesTokens(messages: ReadonlyArray<Message>): number {
  let total = 0;
  for (const msg of messages) {
    total += estimateStringTokens(msg.content ?? "");
  }
  return total;
}

/**
 * Keep as many recent messages as fit in the budget.
 *
 * Drops oldest messages first to preserve recency.
 *
 * @param messages - Conversation messages, oldest first.
 * @param budget - Available token budget.
 * @returns Suffix of messages that fits within the budget.
 */
function fitMessages(messages: ReadonlyArray<Message>, budget: number): Message[] {
  if (budget <= 0) {
    return [];
  }

  const kept: Message[] = [];
  let used = 0;

  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]!;
    const cost = estimateStringTokens(msg.content ?? "");
    if (used + cost > budget) {
      break;
    }
    kept.push(msg);
    used += cost;
  }

  kept.reverse();
  return kept;
}

/**
 * Keep as many items as fit within the token budget.
 *
 * Items are kept in order; excess items at the end are dropped.
 *
 * @param items - Strings to fit, in priority order.
 * @param budget - Available token budget.
 * @returns Prefix of items that fits within the budget.
 */
function fitStrings(items: ReadonlyArray<string>, budget: number): string[] {
  if (budget <= 0) {
    return [];
  }

  const kept: string[] = [];
  let used = 0;

  for (const item of items) {
    const cost = estimateStringTokens(item);
    if (used + cost > budget) {
      break;
    }
    kept.push(item);
    used += cost;
  }

  return kept;
}
