/**
 * Warm memory — episodes and facts with key-value semantics.
 *
 * In-memory implementation of the {@link WarmTier} protocol from tiered.ts.
 * Stores episodes (ordered by insertion) and facts (deduplicated by content),
 * scoped by a session key. Suitable for testing and single-process deployments.
 *
 * @module memory/warm
 */

import type { WarmTier } from "./tiered.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum episodes per scope before oldest are pruned. */
export const DEFAULT_MAX_EPISODES = 50;

/** Maximum facts per scope before oldest are pruned. */
export const DEFAULT_MAX_FACTS = 200;

/** Minimum word-overlap score for an entry to be considered relevant. */
const MIN_RELEVANCE_SCORE = 0.1;

// ---------------------------------------------------------------------------
// InMemoryWarmMemory
// ---------------------------------------------------------------------------

/** Configuration options for {@link InMemoryWarmMemory}. */
export interface WarmMemoryOptions {
  /** Max episodes per scope before pruning oldest. */
  readonly maxEpisodes?: number;
  /** Max facts per scope before pruning oldest. */
  readonly maxFacts?: number;
}

/**
 * In-memory warm tier storing episodes and extracted facts.
 *
 * Scoped by a session key (user_id, session_id, etc).
 * Episodes are ordered by insertion. Facts are deduplicated by content.
 *
 * Retrieval uses simple word-overlap scoring against the query to return
 * the most relevant entries first.
 */
export class InMemoryWarmMemory implements WarmTier {
  private readonly _maxEpisodes: number;
  private readonly _maxFacts: number;
  private readonly _episodes = new Map<string, string[]>();
  private readonly _facts = new Map<string, string[]>();

  /**
   * @param options - Optional max episodes and max facts overrides.
   */
  constructor(options?: WarmMemoryOptions) {
    this._maxEpisodes = options?.maxEpisodes ?? DEFAULT_MAX_EPISODES;
    this._maxFacts = options?.maxFacts ?? DEFAULT_MAX_FACTS;
  }

  /**
   * Retrieve episodes relevant to the query for a session.
   *
   * Uses word-overlap scoring to rank results. Entries below
   * the relevance threshold are excluded.
   *
   * @param query - Search query text.
   * @param sessionId - Session scope key.
   * @returns List of episode strings, most relevant first.
   */
  async getEpisodes(query: string, sessionId: string): Promise<string[]> {
    const episodes = this._episodes.get(sessionId) ?? [];
    return rankByRelevance(query, episodes);
  }

  /**
   * Retrieve facts relevant to the query for a session.
   *
   * Uses word-overlap scoring to rank results. Entries below
   * the relevance threshold are excluded.
   *
   * @param query - Search query text.
   * @param sessionId - Session scope key.
   * @returns List of fact strings, most relevant first.
   */
  async getFacts(query: string, sessionId: string): Promise<string[]> {
    const facts = this._facts.get(sessionId) ?? [];
    return rankByRelevance(query, facts);
  }

  /**
   * Store content as an episode (default) or fact.
   *
   * Episodes are appended in insertion order. Facts are deduplicated
   * by exact content match. Both collections are pruned when they
   * exceed their configured maximums.
   *
   * To store as a fact, prefix content with "fact:" — otherwise it is
   * stored as an episode.
   *
   * @param content - Text content to store.
   * @param sessionId - Session scope key.
   */
  async store(content: string, sessionId: string): Promise<void> {
    if (!content || !content.trim()) {
      return;
    }

    if (content.startsWith("fact:")) {
      this.storeFact(content.slice(5).trim(), sessionId);
    } else {
      this.storeEpisode(content, sessionId);
    }
  }

  /**
   * Remove all episodes and facts for a session.
   *
   * @param sessionId - Session scope key to clear.
   */
  async clear(sessionId: string): Promise<void> {
    this._episodes.delete(sessionId);
    this._facts.delete(sessionId);
  }

  // -- Private helpers ------------------------------------------------------

  /**
   * Append an episode and prune if over limit.
   *
   * @param content - Episode text.
   * @param sessionId - Session scope key.
   */
  private storeEpisode(content: string, sessionId: string): void {
    const episodes = this.getOrCreate(this._episodes, sessionId);
    episodes.push(content);
    pruneOldest(episodes, this._maxEpisodes);
  }

  /**
   * Add a fact if not already present, pruning if over limit.
   *
   * @param content - Fact text.
   * @param sessionId - Session scope key.
   */
  private storeFact(content: string, sessionId: string): void {
    if (!content) {
      return;
    }
    const facts = this.getOrCreate(this._facts, sessionId);
    if (facts.includes(content)) {
      return;
    }
    facts.push(content);
    pruneOldest(facts, this._maxFacts);
  }

  /**
   * Get or create the entries array for a session in a given map.
   *
   * @param map - The storage map (episodes or facts).
   * @param sessionId - Session key.
   * @returns The mutable entries array.
   */
  private getOrCreate(map: Map<string, string[]>, sessionId: string): string[] {
    const existing = map.get(sessionId);
    if (existing !== undefined) {
      return existing;
    }
    const entries: string[] = [];
    map.set(sessionId, entries);
    return entries;
  }
}

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

/**
 * Remove oldest entries to enforce a size limit.
 *
 * @param items - Mutable list to prune in place.
 * @param maxSize - Maximum allowed length.
 */
function pruneOldest(items: string[], maxSize: number): void {
  const overflow = items.length - maxSize;
  if (overflow > 0) {
    items.splice(0, overflow);
  }
}

/**
 * Rank entries by word-overlap with the query.
 *
 * Scores each entry as |intersection| / |query_words|. Entries
 * below the relevance threshold are excluded. Results are sorted
 * descending by score (ties broken by insertion order).
 *
 * @param query - Search query text.
 * @param entries - Candidate entries to score.
 * @returns Entries above the relevance threshold, most relevant first.
 */
function rankByRelevance(query: string, entries: readonly string[]): string[] {
  const queryWords = toWordSet(query);
  if (queryWords.size === 0) {
    return [...entries];
  }

  const scored: Array<{ score: number; index: number; entry: string }> = [];
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]!;
    const score = wordOverlapScore(queryWords, entry);
    if (score >= MIN_RELEVANCE_SCORE) {
      scored.push({ score, index: i, entry });
    }
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) {
      return b.score - a.score;
    }
    return a.index - b.index;
  });

  return scored.map((item) => item.entry);
}

/**
 * Compute word-overlap ratio between query words and text.
 *
 * @param queryWords - Pre-computed lowercase word set from the query.
 * @param text - Text to score against the query.
 * @returns Ratio of overlapping words to total query words (0.0 to 1.0).
 */
function wordOverlapScore(queryWords: ReadonlySet<string>, text: string): number {
  const textWords = toWordSet(text);
  if (textWords.size === 0) {
    return 0.0;
  }
  let overlap = 0;
  for (const word of queryWords) {
    if (textWords.has(word)) {
      overlap++;
    }
  }
  return overlap / queryWords.size;
}

/**
 * Split text into a lowercase word set.
 *
 * @param text - Input text.
 * @returns Set of lowercase words (whitespace-split).
 */
function toWordSet(text: string): Set<string> {
  const trimmed = text.trim();
  if (!trimmed) {
    return new Set();
  }
  return new Set(trimmed.toLowerCase().split(/\s+/));
}
