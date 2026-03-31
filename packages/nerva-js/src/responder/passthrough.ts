/**
 * Passthrough responder — returns raw output without formatting.
 *
 * Use for API consumers and programmatic access where the caller
 * handles its own formatting. Truncates to `channel.maxLength`
 * when set.
 *
 * @module responder/passthrough
 */

import type {
  AgentResult,
  Channel,
  ExecContext,
  Response,
} from "./index.js";
import { createResponse } from "./index.js";

// ---------------------------------------------------------------------------
// PassthroughResponder
// ---------------------------------------------------------------------------

/**
 * Returns agent output as-is, without any transformation.
 *
 * Truncates `output.output` to `channel.maxLength` if the channel
 * defines a positive limit. Media and metadata are left empty.
 */
export class PassthroughResponder {
  /**
   * Pass output through without transformation.
   *
   * Truncates to `channel.maxLength` if the channel defines a positive
   * limit. Media and metadata are left empty.
   *
   * @param output - Raw agent result from the runtime.
   * @param channel - Target delivery channel (used only for maxLength).
   * @param _ctx - Execution context (unused by passthrough).
   * @returns Response containing the raw output text.
   */
  async format(
    output: AgentResult,
    channel: Channel,
    _ctx: ExecContext,
  ): Promise<Response> {
    let text = output.output;

    if (channel.maxLength > 0) {
      text = text.slice(0, channel.maxLength);
    }

    return createResponse(text, channel);
  }
}
