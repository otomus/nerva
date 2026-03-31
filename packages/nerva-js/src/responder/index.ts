/**
 * Responder — format agent output for target channels.
 *
 * Defines the core interfaces, value types, and default channel constants
 * for Nerva's response formatting layer.
 *
 * @module responder
 */

import type { ExecContext } from "../context.js";

export type { ExecContext };

// ---------------------------------------------------------------------------
// AgentResult (referenced by Responder — minimal shape needed here)
// ---------------------------------------------------------------------------

/**
 * Result from an agent handler invocation.
 *
 * This is the minimal shape the responder needs. The full AgentResult
 * lives in the runtime module.
 */
export interface AgentResult {
  /** The agent's response text. */
  readonly output: string;
  /** Outcome status of the invocation. */
  readonly status: string;
  /** Structured data returned by the agent. */
  readonly data?: Readonly<Record<string, string>>;
  /** Error message when status is "error". */
  readonly error?: string;
  /** Name of the handler that produced this result. */
  readonly handler: string;
}

// ---------------------------------------------------------------------------
// Channel
// ---------------------------------------------------------------------------

/**
 * Target channel for a response.
 *
 * Immutable once created. Describes the capabilities and constraints
 * of the delivery endpoint.
 */
export interface Channel {
  /** Channel identifier (e.g. "slack", "api", "websocket"). */
  readonly name: string;
  /** Whether the channel renders markdown. */
  readonly supportsMarkdown: boolean;
  /** Whether the channel can display images/files. */
  readonly supportsMedia: boolean;
  /** Maximum response length in characters (0 = unlimited). */
  readonly maxLength: number;
}

/**
 * Create a {@link Channel} with sensible defaults.
 *
 * @param name - Channel identifier.
 * @param overrides - Optional fields to override defaults.
 * @returns A frozen Channel object.
 */
export function createChannel(
  name: string,
  overrides?: Partial<Omit<Channel, "name">>,
): Channel {
  return Object.freeze({
    name,
    supportsMarkdown: overrides?.supportsMarkdown ?? true,
    supportsMedia: overrides?.supportsMedia ?? false,
    maxLength: overrides?.maxLength ?? 0,
  });
}

// ---------------------------------------------------------------------------
// Response
// ---------------------------------------------------------------------------

/**
 * Formatted response ready for delivery.
 */
export interface Response {
  /** The formatted response text. */
  readonly text: string;
  /** Target channel this response was formatted for. */
  readonly channel: Channel;
  /** Optional media attachments (URLs or base64 strings). */
  readonly media: ReadonlyArray<string>;
  /** Extra key-value metadata for the channel. */
  readonly metadata: Readonly<Record<string, string>>;
}

/**
 * Create a {@link Response} with sensible defaults.
 *
 * @param text - The formatted response text.
 * @param channel - Target delivery channel.
 * @param overrides - Optional media and metadata overrides.
 * @returns A Response object.
 */
export function createResponse(
  text: string,
  channel: Channel,
  overrides?: { media?: string[]; metadata?: Record<string, string> },
): Response {
  return {
    text,
    channel,
    media: overrides?.media ?? [],
    metadata: overrides?.metadata ?? {},
  };
}

// ---------------------------------------------------------------------------
// Default channels
// ---------------------------------------------------------------------------

/** Default channel for programmatic API consumers. */
export const API_CHANNEL: Channel = createChannel("api", {
  supportsMarkdown: false,
  supportsMedia: true,
});

/** Default channel for WebSocket connections. */
export const WEBSOCKET_CHANNEL: Channel = createChannel("websocket", {
  supportsMarkdown: true,
  supportsMedia: true,
});

// ---------------------------------------------------------------------------
// Responder interface
// ---------------------------------------------------------------------------

/**
 * Format agent output for a target channel.
 *
 * Implementations adapt the raw {@link AgentResult} into a {@link Response}
 * appropriate for the delivery channel (truncation, markdown stripping,
 * media attachment, etc.).
 */
export interface Responder {
  /**
   * Format agent output for the target channel.
   *
   * @param output - Raw agent result from the runtime.
   * @param channel - Target delivery channel.
   * @param ctx - Execution context carrying identity and permissions.
   * @returns Formatted Response ready for delivery.
   */
  format(output: AgentResult, channel: Channel, ctx: ExecContext): Promise<Response>;
}
