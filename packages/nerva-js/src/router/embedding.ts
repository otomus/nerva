/**
 * Embedding-based router — cosine similarity against handler descriptions.
 *
 * Routes messages by embedding the input text and comparing it against
 * pre-computed embeddings of handler descriptions. Top-k handlers above
 * the confidence threshold are returned. No LLM call needed.
 *
 * @module router/embedding
 */

import type { ExecContext } from "../context.js";
import type { HandlerCandidate, IntentResult, IntentRouter } from "./index.js";
import { createHandlerCandidate, createIntentResult } from "./index.js";

// ---------------------------------------------------------------------------
// Native module auto-detection
// ---------------------------------------------------------------------------

/** Shape of the @nerva/core native addon. */
interface NativeModule {
  cosine_similarity(a: number[], b: number[]): number;
}

/**
 * Attempt to load the native Rust module at startup.
 * Falls back to null when the addon is not installed or not compiled
 * for the current platform — pure JS takes over transparently.
 */
let nativeModule: NativeModule | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  nativeModule = require("@nerva/core") as NativeModule;
} catch {
  // Native module not available — pure JS fallback is used.
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Dense vector representation of a text string. */
export type Embedding = readonly number[];

/**
 * Async function that converts text into an embedding vector.
 *
 * Implementations may call an external API (OpenAI, Cohere) or run
 * a local model. The returned vector length must be consistent
 * across calls.
 */
export type EmbedFn = (text: string) => Promise<number[]>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Minimum valid threshold value. */
const MINIMUM_THRESHOLD = 0.0;

/** Maximum valid threshold value. */
const MAXIMUM_THRESHOLD = 1.0;

/** Default cosine similarity threshold to consider a match. */
const DEFAULT_THRESHOLD = 0.3;

/** Default number of top candidates to return. */
const DEFAULT_TOP_K = 5;

/** Confidence for empty results. */
const NO_MATCH_CONFIDENCE = 0.0;

/** Intent label when nothing matched. */
const NO_MATCH_INTENT = "unknown";

/** Intent label for semantic matches. */
const MATCH_INTENT = "semantic";

// ---------------------------------------------------------------------------
// Internal value object
// ---------------------------------------------------------------------------

/** A handler with its pre-computed description embedding. */
interface HandlerDescriptor {
  readonly name: string;
  readonly description: string;
  readonly embedding: number[];
}

// ---------------------------------------------------------------------------
// EmbeddingRouter
// ---------------------------------------------------------------------------

/** Configuration options for {@link EmbeddingRouter}. */
export interface EmbeddingRouterOptions {
  /** Minimum cosine similarity to consider a match (0.0-1.0). */
  readonly threshold?: number;
  /** Maximum number of candidates to return. */
  readonly topK?: number;
}

/**
 * Router using cosine similarity between message and handler descriptions.
 *
 * Handlers are registered with descriptions. At classify time the message
 * is embedded and compared against all handler embeddings. Top-k handlers
 * above the confidence threshold are returned, best first.
 */
export class EmbeddingRouter implements IntentRouter {
  private readonly _embed: EmbedFn;
  private readonly _threshold: number;
  private readonly _topK: number;
  private readonly _handlers: HandlerDescriptor[] = [];

  /**
   * @param embed - Async function that converts text to an embedding vector.
   * @param options - Optional threshold and topK overrides.
   * @throws {RangeError} If threshold is outside [0.0, 1.0].
   * @throws {RangeError} If topK is less than 1.
   */
  constructor(embed: EmbedFn, options?: EmbeddingRouterOptions) {
    const threshold = options?.threshold ?? DEFAULT_THRESHOLD;
    const topK = options?.topK ?? DEFAULT_TOP_K;

    if (threshold < MINIMUM_THRESHOLD || threshold > MAXIMUM_THRESHOLD) {
      throw new RangeError(
        `threshold must be between ${MINIMUM_THRESHOLD} and ${MAXIMUM_THRESHOLD}, got ${threshold}`,
      );
    }
    if (topK < 1) {
      throw new RangeError(`topK must be >= 1, got ${topK}`);
    }

    this._embed = embed;
    this._threshold = threshold;
    this._topK = topK;
  }

  /**
   * Register a handler by embedding its description.
   *
   * The description is embedded immediately via the injected embed
   * function and stored for later comparison.
   *
   * @param name - Handler name (must match a registry entry).
   * @param description - Human-readable description of what the handler does.
   * @throws {Error} If name is empty or description is blank.
   */
  async register(name: string, description: string): Promise<void> {
    if (!name) {
      throw new Error("Handler name must not be empty");
    }
    if (!description.trim()) {
      throw new Error("Handler description must not be blank");
    }

    const embedding = await this._embed(description);
    this._handlers.push({ name, description, embedding });
  }

  /**
   * Classify a message by cosine similarity against handler descriptions.
   *
   * @param message - Raw user message text.
   * @param _ctx - Execution context (carried for protocol conformance).
   * @returns IntentResult with ranked candidates.
   */
  async classify(message: string, _ctx: ExecContext): Promise<IntentResult> {
    if (!message.trim() || this._handlers.length === 0) {
      return emptyResult();
    }

    const queryEmbedding = await this._embed(message);
    const candidates = rankHandlers(
      queryEmbedding,
      this._handlers,
      this._threshold,
      this._topK,
    );

    if (candidates.length === 0) {
      return emptyResult();
    }

    return buildResult(candidates);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Compute cosine similarity between two vectors.
 *
 * Returns 0.0 for zero-length vectors or mismatched dimensions
 * rather than raising, so callers never hit division-by-zero.
 *
 * @param a - First embedding vector.
 * @param b - Second embedding vector.
 * @returns Cosine similarity in [-1.0, 1.0], or 0.0 for degenerate inputs.
 */
export function cosineSimilarity(a: Embedding, b: Embedding): number {
  // Delegate to native Rust SIMD implementation when available.
  if (nativeModule) {
    return nativeModule.cosine_similarity([...a], [...b]);
  }

  return cosineSimilarityJS(a, b);
}

/**
 * Pure JavaScript cosine similarity implementation.
 *
 * Used as fallback when the native @nerva/core addon is not available.
 *
 * @param a - First embedding vector.
 * @param b - Second embedding vector.
 * @returns Cosine similarity in [-1.0, 1.0], or 0.0 for degenerate inputs.
 */
function cosineSimilarityJS(a: Embedding, b: Embedding): number {
  if (a.length !== b.length || a.length === 0) {
    return 0.0;
  }

  let dot = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    const ai = a[i]!;
    const bi = b[i]!;
    dot += ai * bi;
    normA += ai * ai;
    normB += bi * bi;
  }

  const magA = Math.sqrt(normA);
  const magB = Math.sqrt(normB);

  if (magA === 0 || magB === 0) {
    return 0.0;
  }

  return dot / (magA * magB);
}

/**
 * Score all handlers against the query embedding and return top-k above threshold.
 *
 * @param queryEmbedding - Embedded user message.
 * @param handlers - All registered handler descriptors.
 * @param threshold - Minimum similarity to include.
 * @param topK - Maximum candidates to return.
 * @returns Sorted candidates (best first), capped at topK.
 */
function rankHandlers(
  queryEmbedding: number[],
  handlers: readonly HandlerDescriptor[],
  threshold: number,
  topK: number,
): HandlerCandidate[] {
  const scored: Array<{ score: number; descriptor: HandlerDescriptor }> = [];

  for (const handler of handlers) {
    const similarity = cosineSimilarity(queryEmbedding, handler.embedding);
    const clamped = Math.max(0.0, Math.min(1.0, similarity));
    if (clamped >= threshold) {
      scored.push({ score: clamped, descriptor: handler });
    }
  }

  scored.sort((a, b) => b.score - a.score);

  return scored.slice(0, topK).map(({ score, descriptor }) =>
    createHandlerCandidate(
      descriptor.name,
      score,
      `Cosine similarity ${score.toFixed(4)} with '${descriptor.description}'`,
    ),
  );
}

/**
 * Build an IntentResult from ranked candidates.
 *
 * @param candidates - Non-empty list of ranked handler candidates.
 * @returns IntentResult with semantic intent and top-candidate confidence.
 */
function buildResult(candidates: readonly HandlerCandidate[]): IntentResult {
  const rawScores: Record<string, number> = {};
  for (const c of candidates) {
    rawScores[c.name] = c.score;
  }
  const topCandidate = candidates[0];
  const confidence = topCandidate !== undefined ? topCandidate.score : NO_MATCH_CONFIDENCE;
  return createIntentResult(MATCH_INTENT, confidence, candidates, rawScores);
}

/**
 * Build an empty IntentResult when nothing matched.
 *
 * @returns IntentResult with zero confidence and no handlers.
 */
function emptyResult(): IntentResult {
  return createIntentResult(NO_MATCH_INTENT, NO_MATCH_CONFIDENCE, []);
}
