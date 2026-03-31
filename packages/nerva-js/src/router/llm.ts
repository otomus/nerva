/**
 * LLM router — classify intent by asking an LLM to select a handler.
 *
 * Builds a prompt containing the full handler catalog, sends it to the
 * provided LLM function, and parses the JSON response into an
 * {@link IntentResult}. Falls back gracefully on invalid JSON.
 *
 * @module router/llm
 */

import type { ExecContext } from "../context.js";
import type { IntentResult, IntentRouter } from "./index.js";
import { createHandlerCandidate, createIntentResult } from "./index.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Async function that sends a system prompt and user prompt to an LLM
 * and returns the raw completion text.
 */
export type LLMFn = (systemPrompt: string, userPrompt: string) => Promise<string>;

/**
 * Shape of the JSON the LLM is expected to return.
 */
interface LLMRouterResponse {
  readonly handler: string;
  readonly confidence: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Intent label for LLM-routed matches. */
const LLM_INTENT = "llm";

/** Intent label when nothing matched. */
const NO_MATCH_INTENT = "unknown";

/** Confidence for empty or failed results. */
const NO_MATCH_CONFIDENCE = 0.0;

/** Default confidence assigned when the LLM omits one. */
const DEFAULT_LLM_CONFIDENCE = 0.5;

/** Regex to locate a JSON object in noisy LLM output. */
const JSON_EXTRACT_PATTERN = /\{[^{}]*\}/s;

// ---------------------------------------------------------------------------
// Registered handler
// ---------------------------------------------------------------------------

/** A handler registered in the catalog. */
interface RegisteredHandler {
  readonly name: string;
  readonly description: string;
}

// ---------------------------------------------------------------------------
// LLMRouter
// ---------------------------------------------------------------------------

/**
 * Routes messages by asking an LLM to select the best handler from a catalog.
 *
 * Builds a system prompt listing all registered handlers and their
 * descriptions, then sends the user message as a user prompt. The LLM
 * must return JSON with `handler` and `confidence` fields.
 *
 * On parse failure or unknown handler, returns an "unknown" intent.
 *
 * @example
 * ```ts
 * const router = new LLMRouter(myLlmFn);
 * router.register("flights", "Book airline flights");
 * router.register("hotels", "Reserve hotel rooms");
 * const result = await router.classify("I need a flight to Paris", ctx);
 * ```
 */
export class LLMRouter implements IntentRouter {
  private readonly _llm: LLMFn;
  private readonly _handlers: RegisteredHandler[] = [];

  /**
   * @param llm - Async function that calls an LLM with system and user prompts.
   */
  constructor(llm: LLMFn) {
    this._llm = llm;
  }

  /**
   * Register a handler in the catalog.
   *
   * @param name - Unique handler identifier.
   * @param description - Human-readable description shown to the LLM.
   * @throws {Error} If name is empty or already registered.
   * @throws {Error} If description is empty or whitespace-only.
   */
  register(name: string, description: string): void {
    if (!name) {
      throw new Error("Handler name must not be empty");
    }
    if (!description || !description.trim()) {
      throw new Error("Handler description must not be empty");
    }
    if (this._handlers.some((h) => h.name === name)) {
      throw new Error(`Handler '${name}' is already registered`);
    }
    this._handlers.push({ name, description });
  }

  /**
   * Classify intent by asking the LLM to select a handler.
   *
   * @param message - Raw user message text.
   * @param _ctx - Execution context (forwarded for observability; not used directly).
   * @returns IntentResult with the LLM's selected handler and confidence.
   */
  async classify(message: string, _ctx: ExecContext): Promise<IntentResult> {
    if (!message || !message.trim()) {
      return emptyResult();
    }
    if (this._handlers.length === 0) {
      return emptyResult();
    }

    const systemPrompt = buildSystemPrompt(this._handlers);
    const rawResponse = await this._llm(systemPrompt, message);
    return parseResponse(rawResponse, this._handlers);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build the system prompt listing all available handlers.
 *
 * @param handlers - Registered handler catalog.
 * @returns A system prompt instructing the LLM to return JSON.
 */
function buildSystemPrompt(handlers: readonly RegisteredHandler[]): string {
  const catalog = handlers
    .map((h) => `- ${h.name}: ${h.description}`)
    .join("\n");

  return [
    "You are an intent classifier. Given a user message, select the best handler from the catalog below.",
    "Return ONLY a JSON object with two fields: \"handler\" (the handler name) and \"confidence\" (a number between 0.0 and 1.0).",
    "Do not include any other text.",
    "",
    "Available handlers:",
    catalog,
  ].join("\n");
}

/**
 * Parse the LLM response into an IntentResult.
 *
 * Tries full JSON parse first, then regex extraction. Falls back to
 * an empty result on invalid JSON or unknown handler names.
 *
 * @param rawResponse - Raw text from the LLM.
 * @param handlers - Registered handlers for validation.
 * @returns IntentResult with the parsed handler, or unknown on failure.
 */
function parseResponse(
  rawResponse: string,
  handlers: readonly RegisteredHandler[],
): IntentResult {
  const parsed = extractJson(rawResponse);
  if (parsed === null) {
    return emptyResult();
  }

  const handlerName = parsed.handler;
  if (!handlers.some((h) => h.name === handlerName)) {
    return emptyResult();
  }

  const confidence = clampConfidence(parsed.confidence);
  const candidate = createHandlerCandidate(handlerName, confidence, "llm selection");
  return createIntentResult(LLM_INTENT, confidence, [candidate], {
    [handlerName]: confidence,
  });
}

/**
 * Extract and validate a JSON object from potentially noisy LLM output.
 *
 * @param raw - Raw LLM response text.
 * @returns Parsed response with handler and confidence, or null on failure.
 */
function extractJson(raw: string): LLMRouterResponse | null {
  const stripped = raw.trim();
  if (stripped === "") return null;

  const fullParse = tryParseResponse(stripped);
  if (fullParse !== null) return fullParse;

  const match = JSON_EXTRACT_PATTERN.exec(stripped);
  if (match !== null) {
    return tryParseResponse(match[0]);
  }

  return null;
}

/**
 * Try to parse text as a valid LLM router response.
 *
 * @param text - Candidate JSON string.
 * @returns Validated response or null if invalid.
 */
function tryParseResponse(text: string): LLMRouterResponse | null {
  try {
    const data: unknown = JSON.parse(text);
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      return null;
    }
    const record = data as Record<string, unknown>;
    const handler = record["handler"];
    if (typeof handler !== "string" || handler === "") {
      return null;
    }
    const confidence = typeof record["confidence"] === "number"
      ? record["confidence"]
      : DEFAULT_LLM_CONFIDENCE;
    return { handler, confidence };
  } catch {
    return null;
  }
}

/**
 * Clamp a confidence value to the [0.0, 1.0] range.
 *
 * @param value - Raw confidence from the LLM.
 * @returns Clamped confidence between 0.0 and 1.0.
 */
function clampConfidence(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_LLM_CONFIDENCE;
  return Math.max(0.0, Math.min(1.0, value));
}

/**
 * Build an empty IntentResult when nothing matched.
 *
 * @returns IntentResult with zero confidence and no handlers.
 */
function emptyResult(): IntentResult {
  return createIntentResult(NO_MATCH_INTENT, NO_MATCH_CONFIDENCE, []);
}
