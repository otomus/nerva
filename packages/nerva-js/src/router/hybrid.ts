/**
 * Hybrid router — embedding pre-filter followed by LLM re-ranking.
 *
 * Two-stage routing: first narrow the candidate set via cosine similarity
 * on embeddings, then re-rank the survivors with an LLM call for
 * semantic precision.
 *
 * @module router/hybrid
 */

import type { ExecContext } from "../context.js";
import type { HandlerCandidate, IntentResult, IntentRouter } from "./index.js";
import { createHandlerCandidate, createIntentResult } from "./index.js";
import { cosineSimilarity, type EmbedFn, type Embedding } from "./embedding.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Async function that re-ranks candidates using an LLM.
 *
 * Takes the original message and pre-filtered candidates,
 * returns re-ranked candidates with updated scores.
 */
export type RerankFn = (
  message: string,
  candidates: readonly HandlerCandidate[],
) => Promise<HandlerCandidate[]>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Intent label when nothing matched. */
const NO_MATCH_INTENT = "unknown";

/** Confidence for empty results. */
const NO_MATCH_CONFIDENCE = 0.0;

/** Intent label for hybrid matches. */
const HYBRID_INTENT = "hybrid";

/** Default minimum cosine similarity to survive pre-filter. */
const DEFAULT_EMBEDDING_THRESHOLD = 0.2;

/** Default maximum candidates forwarded to the reranker. */
const DEFAULT_PRE_FILTER_K = 10;

/** Default maximum candidates in the final result. */
const DEFAULT_FINAL_K = 5;

// ---------------------------------------------------------------------------
// Internal value object
// ---------------------------------------------------------------------------

/** A handler with its pre-computed description embedding. */
interface RegisteredHandler {
  readonly name: string;
  readonly description: string;
  readonly embedding: number[];
}

// ---------------------------------------------------------------------------
// HybridRouter
// ---------------------------------------------------------------------------

/** Configuration options for {@link HybridRouter}. */
export interface HybridRouterOptions {
  /** Minimum cosine similarity to survive pre-filter. */
  readonly embeddingThreshold?: number;
  /** Maximum candidates forwarded to the reranker. */
  readonly preFilterK?: number;
  /** Maximum candidates in the final result. */
  readonly finalK?: number;
}

/**
 * Two-stage router: embedding pre-filter followed by LLM re-rank.
 *
 * Stage 1: Embed the incoming message, compute cosine similarity against
 * all registered handlers, and keep the top preFilterK candidates
 * that exceed embeddingThreshold.
 *
 * Stage 2: Pass surviving candidates to the rerank function (typically
 * an LLM call) which returns re-scored candidates.
 *
 * If no handlers are registered, or all fall below the embedding
 * threshold after stage 1, stage 2 is skipped and an empty result is
 * returned. If the reranker returns an empty list, the router falls
 * back to embedding-only results.
 */
export class HybridRouter implements IntentRouter {
  private readonly _embed: EmbedFn;
  private readonly _rerank: RerankFn;
  private readonly _embeddingThreshold: number;
  private readonly _preFilterK: number;
  private readonly _finalK: number;
  private readonly _handlers: RegisteredHandler[] = [];

  /**
   * @param embed - Async embedding function.
   * @param rerank - Async re-ranking function (LLM-based).
   * @param options - Optional threshold and k-value overrides.
   */
  constructor(embed: EmbedFn, rerank: RerankFn, options?: HybridRouterOptions) {
    this._embed = embed;
    this._rerank = rerank;
    this._embeddingThreshold = options?.embeddingThreshold ?? DEFAULT_EMBEDDING_THRESHOLD;
    this._preFilterK = options?.preFilterK ?? DEFAULT_PRE_FILTER_K;
    this._finalK = options?.finalK ?? DEFAULT_FINAL_K;
  }

  /**
   * Register a handler by embedding its description.
   *
   * @param name - Unique handler identifier.
   * @param description - Human-readable description to embed for matching.
   * @throws {Error} If name is empty or already registered.
   * @throws {Error} If description is empty or whitespace-only.
   */
  async register(name: string, description: string): Promise<void> {
    if (!name) {
      throw new Error("Handler name must not be empty");
    }
    if (!description || !description.trim()) {
      throw new Error("Handler description must not be empty");
    }
    if (findHandler(this._handlers, name) !== null) {
      throw new Error(`Handler '${name}' is already registered`);
    }

    const embedding = await this._embed(description);
    this._handlers.push({ name, description, embedding });
  }

  /**
   * Two-stage classification: embedding pre-filter followed by LLM re-rank.
   *
   * @param message - Raw user message text.
   * @param _ctx - Execution context (forwarded for observability; not used directly).
   * @returns IntentResult with ranked handler candidates.
   */
  async classify(message: string, _ctx: ExecContext): Promise<IntentResult> {
    if (!message || !message.trim()) {
      return emptyResult();
    }
    if (this._handlers.length === 0) {
      return emptyResult();
    }

    const embeddingCandidates = await this.embeddingPrefilter(message);
    if (embeddingCandidates.length === 0) {
      return emptyResult();
    }

    const reranked = await this.llmRerank(message, embeddingCandidates);

    // Fall back to embedding results if reranker returns nothing
    const finalCandidates = reranked.length > 0 ? reranked : embeddingCandidates;
    const trimmed = finalCandidates.slice(0, this._finalK);

    return buildResult(trimmed);
  }

  // -- Private stages -------------------------------------------------------

  /**
   * Stage 1: score all handlers by cosine similarity, keep top-k above threshold.
   *
   * @param message - Raw user message text.
   * @returns Sorted candidates (best first) that passed the embedding threshold.
   */
  private async embeddingPrefilter(message: string): Promise<HandlerCandidate[]> {
    const messageEmbedding = await this._embed(message);
    const scored = scoreHandlers(messageEmbedding, this._handlers, this._embeddingThreshold);
    const sorted = [...scored].sort((a, b) => b.score - a.score);
    return sorted.slice(0, this._preFilterK);
  }

  /**
   * Stage 2: re-rank pre-filtered candidates via the LLM reranker.
   *
   * @param message - Raw user message text.
   * @param candidates - Pre-filtered candidates from the embedding stage.
   * @returns Re-ranked candidates sorted by score (best first).
   */
  private async llmRerank(
    message: string,
    candidates: readonly HandlerCandidate[],
  ): Promise<HandlerCandidate[]> {
    const reranked = await this._rerank(message, candidates);
    return [...reranked].sort((a, b) => b.score - a.score);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Score each handler against the message embedding and filter by threshold.
 *
 * @param messageEmbedding - Embedding vector of the user message.
 * @param handlers - All registered handlers with their embeddings.
 * @param threshold - Minimum cosine similarity to include a candidate.
 * @returns Candidates whose similarity exceeds threshold (unordered).
 */
function scoreHandlers(
  messageEmbedding: Embedding,
  handlers: readonly RegisteredHandler[],
  threshold: number,
): HandlerCandidate[] {
  const candidates: HandlerCandidate[] = [];
  for (const handler of handlers) {
    const similarity = cosineSimilarity(messageEmbedding, handler.embedding);
    if (similarity < threshold) {
      continue;
    }
    const score = Math.max(0.0, Math.min(1.0, similarity));
    candidates.push(
      createHandlerCandidate(
        handler.name,
        score,
        `embedding similarity: ${similarity.toFixed(4)}`,
      ),
    );
  }
  return candidates;
}

/**
 * Look up a registered handler by name.
 *
 * @param handlers - List of registered handlers.
 * @param name - Handler name to search for.
 * @returns The matching handler, or null if not found.
 */
function findHandler(
  handlers: readonly RegisteredHandler[],
  name: string,
): RegisteredHandler | null {
  for (const handler of handlers) {
    if (handler.name === name) {
      return handler;
    }
  }
  return null;
}

/**
 * Build an IntentResult from a non-empty candidate list.
 *
 * @param candidates - Non-empty, sorted (best first) list of candidates.
 * @returns IntentResult with hybrid intent and top-candidate confidence.
 */
function buildResult(candidates: readonly HandlerCandidate[]): IntentResult {
  const rawScores: Record<string, number> = {};
  for (const c of candidates) {
    rawScores[c.name] = c.score;
  }
  const topCandidate = candidates[0];
  const confidence = topCandidate !== undefined ? topCandidate.score : NO_MATCH_CONFIDENCE;
  return createIntentResult(HYBRID_INTENT, confidence, candidates, rawScores);
}

/**
 * Build an empty IntentResult when nothing matched.
 *
 * @returns IntentResult with zero confidence and no handlers.
 */
function emptyResult(): IntentResult {
  return createIntentResult(NO_MATCH_INTENT, NO_MATCH_CONFIDENCE, []);
}
