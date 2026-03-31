/**
 * Intent routing — classify messages and select handlers.
 *
 * Defines the {@link IntentRouter} interface and supporting value types.
 * Strategy implementations (rule-based, embedding, LLM) live in
 * separate modules and satisfy this interface.
 *
 * @module router
 */

import type { ExecContext } from "../context.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Minimum valid confidence score. */
export const MIN_CONFIDENCE = 0.0;

/** Maximum valid confidence score. */
export const MAX_CONFIDENCE = 1.0;

/** Minimum valid handler match score. */
export const MIN_SCORE = 0.0;

/** Maximum valid handler match score. */
export const MAX_SCORE = 1.0;

// ---------------------------------------------------------------------------
// Value objects
// ---------------------------------------------------------------------------

/**
 * A candidate handler returned by the router.
 */
export interface HandlerCandidate {
  /** Handler name (must match a registry entry). */
  readonly name: string;

  /** Match score between 0.0 and 1.0. */
  readonly score: number;

  /** Why this handler was selected (for observability). */
  readonly reason: string;
}

/**
 * Create a validated {@link HandlerCandidate}.
 *
 * @param name - Handler name (must match a registry entry).
 * @param score - Match score between 0.0 and 1.0.
 * @param reason - Why this handler was selected. Defaults to `""`.
 * @returns A frozen `HandlerCandidate`.
 * @throws {RangeError} If `score` is outside [0.0, 1.0].
 */
export function createHandlerCandidate(
  name: string,
  score: number,
  reason = "",
): HandlerCandidate {
  if (score < MIN_SCORE || score > MAX_SCORE) {
    throw new RangeError(
      `score must be between ${MIN_SCORE} and ${MAX_SCORE}, got ${score}`,
    );
  }
  return Object.freeze({ name, score, reason });
}

/**
 * Result of intent classification.
 */
export interface IntentResult {
  /** Classified intent label (e.g. `"book_flight"`). */
  readonly intent: string;

  /** Classification confidence between 0.0 and 1.0. */
  readonly confidence: number;

  /** Ranked list of handler candidates, best first. */
  readonly handlers: readonly HandlerCandidate[];

  /** Optional per-handler scores for debugging. */
  readonly rawScores: Readonly<Record<string, number>>;

  /**
   * Return the top-ranked handler, or `null` if no candidates.
   *
   * @returns The first element of `handlers`, or `null` when the list is empty.
   */
  readonly bestHandler: HandlerCandidate | null;
}

/**
 * Create a validated {@link IntentResult}.
 *
 * @param intent - Classified intent label.
 * @param confidence - Classification confidence between 0.0 and 1.0.
 * @param handlers - Ranked list of handler candidates.
 * @param rawScores - Optional per-handler scores for debugging.
 * @returns A frozen `IntentResult`.
 * @throws {RangeError} If `confidence` is outside [0.0, 1.0].
 */
export function createIntentResult(
  intent: string,
  confidence: number,
  handlers: readonly HandlerCandidate[],
  rawScores: Record<string, number> = {},
): IntentResult {
  if (confidence < MIN_CONFIDENCE || confidence > MAX_CONFIDENCE) {
    throw new RangeError(
      `confidence must be between ${MIN_CONFIDENCE} and ${MAX_CONFIDENCE}, got ${confidence}`,
    );
  }

  const frozenHandlers = Object.freeze([...handlers]);
  const frozenScores = Object.freeze({ ...rawScores });
  const bestHandler =
    frozenHandlers.length > 0 ? (frozenHandlers[0] ?? null) : null;

  return Object.freeze({
    intent,
    confidence,
    handlers: frozenHandlers,
    rawScores: frozenScores,
    bestHandler,
  });
}

// ---------------------------------------------------------------------------
// Protocol
// ---------------------------------------------------------------------------

/**
 * Classify a user message and select the best handler.
 *
 * Every router strategy implements this interface. The orchestrator
 * calls {@link IntentRouter.classify} and uses the result to dispatch
 * to the appropriate handler in the runtime.
 */
export interface IntentRouter {
  /**
   * Classify intent and return ranked handler candidates.
   *
   * @param message - Raw user message text.
   * @param ctx - Execution context carrying permissions, trace id, and session metadata.
   * @returns An {@link IntentResult} with confidence and ranked handlers.
   */
  classify(message: string, ctx: ExecContext): Promise<IntentResult>;
}
