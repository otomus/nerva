/**
 * Cold memory — long-term knowledge with keyword search.
 *
 * In-memory implementation of the {@link ColdTier} protocol from tiered.ts.
 * Uses simple word-overlap scoring instead of vector embeddings. Suitable
 * for testing; swap with a vector DB implementation for production.
 *
 * @module memory/cold
 */

import type { ColdTier } from "./tiered.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum search results returned per query. */
export const DEFAULT_MAX_RESULTS = 10;

/** Minimum word-overlap score for an entry to appear in search results. */
const MIN_RELEVANCE_SCORE = 0.1;

// ---------------------------------------------------------------------------
// InMemoryColdMemory
// ---------------------------------------------------------------------------

/** Configuration options for {@link InMemoryColdMemory}. */
export interface ColdMemoryOptions {
  /** Maximum number of search results to return. */
  readonly maxResults?: number;
}

/**
 * In-memory cold tier with basic keyword search.
 *
 * No actual vector embeddings — uses simple word-overlap scoring
 * (|query intersection entry| / |query words|) to rank stored entries.
 * For production use, swap with a vector DB implementation.
 */
export class InMemoryColdMemory implements ColdTier {
  private readonly _maxResults: number;
  private readonly _entries = new Map<string, string[]>();

  /**
   * @param options - Optional max results override.
   */
  constructor(options?: ColdMemoryOptions) {
    this._maxResults = options?.maxResults ?? DEFAULT_MAX_RESULTS;
  }

  /**
   * Search stored entries by word overlap with the query.
   *
   * Scores each entry as |intersection| / |query_words|.
   * Returns the top-k entries above the relevance threshold,
   * sorted by score descending.
   *
   * @param query - Search query text.
   * @param scope - Memory scope string for filtering.
   * @returns List of matching knowledge strings, most relevant first.
   */
  async search(query: string, scope: string): Promise<string[]> {
    const queryWords = toWordSet(query);
    if (queryWords.size === 0) {
      return [];
    }

    const entries = this._entries.get(scope) ?? [];
    const scored = scoreEntries(queryWords, entries);
    return selectTopResults(scored, this._maxResults);
  }

  /**
   * Store a knowledge entry in the cold tier.
   *
   * Duplicate entries (exact match) within the same scope are skipped.
   *
   * @param content - Knowledge text to store.
   * @param scope - Memory scope string for isolation.
   */
  async store(content: string, scope: string): Promise<void> {
    if (!content || !content.trim()) {
      return;
    }

    const entries = this.getOrCreate(scope);
    if (!entries.includes(content)) {
      entries.push(content);
    }
  }

  /**
   * Remove all entries for a scope.
   *
   * @param scope - Memory scope to clear.
   */
  async clear(scope: string): Promise<void> {
    this._entries.delete(scope);
  }

  // -- Private helpers ------------------------------------------------------

  /**
   * Get or create the entries array for a scope.
   *
   * @param scope - Memory scope key.
   * @returns The mutable entries array.
   */
  private getOrCreate(scope: string): string[] {
    const existing = this._entries.get(scope);
    if (existing !== undefined) {
      return existing;
    }
    const entries: string[] = [];
    this._entries.set(scope, entries);
    return entries;
  }
}

// ---------------------------------------------------------------------------
// Scoring helpers
// ---------------------------------------------------------------------------

/**
 * Score entries by word overlap with pre-computed query words.
 *
 * @param queryWords - Lowercase word set from the search query.
 * @param entries - Candidate knowledge strings.
 * @returns List of [score, entry] tuples above the relevance threshold.
 */
function scoreEntries(
  queryWords: ReadonlySet<string>,
  entries: readonly string[],
): Array<{ score: number; entry: string }> {
  const scored: Array<{ score: number; entry: string }> = [];
  for (const entry of entries) {
    const score = wordOverlapScore(queryWords, entry);
    if (score >= MIN_RELEVANCE_SCORE) {
      scored.push({ score, entry });
    }
  }
  return scored;
}

/**
 * Sort scored entries descending and return top-k texts.
 *
 * @param scored - List of scored entries.
 * @param maxResults - Maximum entries to return.
 * @returns Entry texts sorted by relevance, capped at maxResults.
 */
function selectTopResults(
  scored: Array<{ score: number; entry: string }>,
  maxResults: number,
): string[] {
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, maxResults).map((item) => item.entry);
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
