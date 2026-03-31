/**
 * Tone responder — rewrite agent output in a configurable tone.
 *
 * Uses an LLM function to rewrite text responses while passing through
 * non-text content unchanged. Channel-aware: respects maxLength limits.
 *
 * @module responder/tone
 */

import type {
  AgentResult,
  Channel,
  ExecContext,
  Response,
  Responder,
} from "./index.js";
import { createResponse } from "./index.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Async function that rewrites text using an LLM.
 *
 * Takes a system prompt (containing tone instructions) and the text to rewrite,
 * returns the rewritten text.
 */
export type ToneRewriteFn = (systemPrompt: string, text: string) => Promise<string>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Status values that indicate non-text / error responses (passed through). */
const PASSTHROUGH_STATUSES = new Set(["error", "timeout", "wrong_handler"]);

// ---------------------------------------------------------------------------
// ToneResponder
// ---------------------------------------------------------------------------

/**
 * Rewrites agent text output in a configurable tone via an LLM.
 *
 * Non-text responses (errors, timeouts) are passed through unchanged.
 * Applies channel maxLength truncation after rewriting.
 *
 * @example
 * ```ts
 * const responder = new ToneResponder(myLlmFn, "friendly and casual");
 * const response = await responder.format(agentResult, channel, ctx);
 * ```
 */
export class ToneResponder implements Responder {
  private readonly _rewrite: ToneRewriteFn;
  private readonly _tone: string;

  /**
   * @param rewrite - Async LLM function for tone rewriting.
   * @param tone - Description of the desired tone (e.g. "formal", "friendly and casual").
   */
  constructor(rewrite: ToneRewriteFn, tone: string) {
    this._rewrite = rewrite;
    this._tone = tone;
  }

  /**
   * Format agent output with tone rewriting.
   *
   * Non-success statuses are passed through without rewriting.
   * Empty output is returned as-is.
   *
   * @param output - Raw agent result from the runtime.
   * @param channel - Target delivery channel.
   * @param _ctx - Execution context (unused by tone responder).
   * @returns Response with tone-rewritten text.
   */
  async format(
    output: AgentResult,
    channel: Channel,
    _ctx: ExecContext,
  ): Promise<Response> {
    if (shouldPassthrough(output)) {
      return createResponse(output.output, channel);
    }

    const systemPrompt = buildTonePrompt(this._tone);
    const rewritten = await this._rewrite(systemPrompt, output.output);

    const text = applyMaxLength(rewritten, channel.maxLength);
    return createResponse(text, channel);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Determine if the output should be passed through without rewriting.
 *
 * @param output - Agent result to check.
 * @returns True if the output should not be rewritten.
 */
function shouldPassthrough(output: AgentResult): boolean {
  if (PASSTHROUGH_STATUSES.has(output.status)) return true;
  if (output.output === "") return true;
  return false;
}

/**
 * Build the system prompt instructing the LLM on tone.
 *
 * @param tone - Desired tone description.
 * @returns System prompt string.
 */
function buildTonePrompt(tone: string): string {
  return [
    `Rewrite the following text in a ${tone} tone.`,
    "Preserve the factual content and meaning. Return only the rewritten text.",
    "Do not add any preamble or explanation.",
  ].join(" ");
}

/**
 * Truncate text to the channel's maxLength if set.
 *
 * @param text - Text to potentially truncate.
 * @param maxLength - Maximum character length (0 = unlimited).
 * @returns Truncated text.
 */
function applyMaxLength(text: string, maxLength: number): string {
  if (maxLength > 0) {
    return text.slice(0, maxLength);
  }
  return text;
}
