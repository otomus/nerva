/**
 * Rule-based router — deterministic regex/keyword matching (N-213).
 *
 * Routes messages by testing ordered regex rules. First match wins.
 * Falls back to a default handler (if configured) or an empty result.
 *
 * @module router/rule
 */

import type { ExecContext } from "../context.js";
import type { IntentResult, IntentRouter } from "./index.js";
import {
  createHandlerCandidate,
  createIntentResult,
} from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Confidence assigned when a rule pattern matches. */
const MATCH_CONFIDENCE = 1.0;

/** Confidence assigned to the default fallback handler. */
const DEFAULT_CONFIDENCE = 0.5;

/** Confidence assigned when nothing matches. */
const NO_MATCH_CONFIDENCE = 0.0;

/** Intent label for the default fallback handler. */
const DEFAULT_INTENT = "default";

/** Intent label when no rules matched. */
const NO_MATCH_INTENT = "unknown";

// ---------------------------------------------------------------------------
// Value objects
// ---------------------------------------------------------------------------

/**
 * A routing rule mapping a regex pattern to a handler.
 */
export interface Rule {
  /** Regex pattern to match against the message. */
  readonly pattern: string;

  /** Handler name to route to on match. */
  readonly handler: string;

  /** Intent label for this rule. */
  readonly intent: string;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

/**
 * Deterministic router using regex pattern matching.
 *
 * Routes messages by testing each rule's regex pattern in order.
 * First match wins. Falls back to `defaultHandler` (with reduced
 * confidence) or returns an empty result if no rules match.
 */
export class RuleRouter implements IntentRouter {
  private readonly _rules: readonly Rule[];
  private readonly _compiled: readonly RegExp[];
  private readonly _defaultHandler: string | null;

  /**
   * @param rules - Ordered list of routing rules.
   * @param defaultHandler - Optional fallback handler when no rules match.
   * @throws {TypeError} If `rules` is not an array.
   * @throws {SyntaxError} If any rule pattern is invalid regex.
   */
  constructor(rules: readonly Rule[], defaultHandler: string | null = null) {
    if (!Array.isArray(rules)) {
      throw new TypeError(
        `rules must be an array, got ${typeof rules}`,
      );
    }

    this._rules = rules;
    this._compiled = compileRules(rules);
    this._defaultHandler = defaultHandler;
  }

  /**
   * Classify a message by testing rules in order.
   *
   * @param message - Raw user message text.
   * @param _ctx - Execution context (unused by rule router, but required
   *   by the {@link IntentRouter} interface).
   * @returns {@link IntentResult} for the first matching rule, the default
   *   handler, or an empty no-match result.
   */
  async classify(message: string, _ctx: ExecContext): Promise<IntentResult> {
    if (!message.trim()) {
      return emptyResult();
    }

    const match = findFirstMatch(message, this._rules, this._compiled);
    if (match !== null) {
      return resultFromRule(match);
    }

    if (this._defaultHandler !== null) {
      return defaultResult(this._defaultHandler);
    }

    return emptyResult();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Pre-compile regex patterns for all rules.
 *
 * @param rules - List of routing rules.
 * @returns Compiled patterns in the same order as `rules`.
 * @throws {SyntaxError} If any pattern is not valid regex.
 */
function compileRules(rules: readonly Rule[]): RegExp[] {
  return rules.map((rule) => new RegExp(rule.pattern, "i"));
}

/**
 * Return the first rule whose pattern matches `message`.
 *
 * @param message - Text to match against.
 * @param rules - Ordered routing rules.
 * @param compiled - Pre-compiled patterns (same order as `rules`).
 * @returns The first matching {@link Rule}, or `null`.
 */
function findFirstMatch(
  message: string,
  rules: readonly Rule[],
  compiled: readonly RegExp[],
): Rule | null {
  for (let i = 0; i < rules.length; i++) {
    const pattern = compiled[i];
    const rule = rules[i];
    if (pattern !== undefined && rule !== undefined && pattern.test(message)) {
      return rule;
    }
  }
  return null;
}

/**
 * Build an IntentResult from a matched rule.
 *
 * @param rule - The matched routing rule.
 * @returns IntentResult with full confidence and a single handler candidate.
 */
function resultFromRule(rule: Rule): IntentResult {
  const candidate = createHandlerCandidate(
    rule.handler,
    MATCH_CONFIDENCE,
    `Matched pattern: ${rule.pattern}`,
  );
  return createIntentResult(rule.intent, MATCH_CONFIDENCE, [candidate]);
}

/**
 * Build an IntentResult for the default fallback handler.
 *
 * @param handler - Name of the default handler.
 * @returns IntentResult with reduced confidence and intent `"default"`.
 */
function defaultResult(handler: string): IntentResult {
  const candidate = createHandlerCandidate(
    handler,
    DEFAULT_CONFIDENCE,
    "No rules matched; using default handler",
  );
  return createIntentResult(DEFAULT_INTENT, DEFAULT_CONFIDENCE, [candidate]);
}

/**
 * Build an empty IntentResult when nothing matched.
 *
 * @returns IntentResult with zero confidence and no handlers.
 */
function emptyResult(): IntentResult {
  return createIntentResult(NO_MATCH_INTENT, NO_MATCH_CONFIDENCE, []);
}
