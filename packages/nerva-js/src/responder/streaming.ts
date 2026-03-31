/**
 * Streaming responder — format chunks per channel capabilities.
 *
 * Provides SSE, WebSocket, and raw text formatting for streamed chunks.
 * Each channel format wraps the content differently:
 *
 * - SSE: `data: {"content": "..."}\n\n`
 * - WebSocket: JSON message `{"content": "..."}`
 * - Raw: plain text chunks
 *
 * @module responder/streaming
 */

// ---------------------------------------------------------------------------
// StreamFormat
// ---------------------------------------------------------------------------

/**
 * Output format for streaming chunks.
 *
 * - `sse` — Server-Sent Events format (`data: ...\n\n`).
 * - `websocket` — JSON message format.
 * - `raw` — Plain text, no framing.
 */
export type StreamFormat = "sse" | "websocket" | "raw";

// ---------------------------------------------------------------------------
// StreamingResponder
// ---------------------------------------------------------------------------

/**
 * Formats streamed chunks based on the target channel format.
 *
 * The responder is configured with a format and converts raw content
 * strings into framed output appropriate for the transport layer.
 */
export class StreamingResponder {
  private readonly _format: StreamFormat;

  /**
   * @param format - The streaming format to use. Defaults to "raw".
   */
  constructor(format: StreamFormat = "raw") {
    this._format = format;
  }

  /** The configured streaming format. */
  get format(): StreamFormat {
    return this._format;
  }

  /**
   * Format a single content string for the configured channel.
   *
   * @param content - Raw text content to format.
   * @returns The formatted chunk string.
   */
  formatChunk(content: string): string {
    return formatForChannel(content, this._format);
  }
}

// ---------------------------------------------------------------------------
// Pure formatting functions
// ---------------------------------------------------------------------------

/**
 * Format content as a Server-Sent Event.
 *
 * @param content - Raw text content.
 * @returns SSE-formatted string: `data: {"content": "..."}\n\n`
 */
export function formatSse(content: string): string {
  const payload = JSON.stringify({ content });
  return `data: ${payload}\n\n`;
}

/**
 * Format content as a WebSocket JSON message.
 *
 * @param content - Raw text content.
 * @returns JSON string: `{"content": "..."}`
 */
export function formatWebsocket(content: string): string {
  return JSON.stringify({ content });
}

/**
 * Return content unchanged (raw text).
 *
 * @param content - Raw text content.
 * @returns The content string unchanged.
 */
export function formatRaw(content: string): string {
  return content;
}

/**
 * Route content through the appropriate formatter.
 *
 * @param content - Raw text content.
 * @param format - Target format.
 * @returns Formatted chunk string.
 * @throws {Error} If the format is not recognised.
 */
export function formatForChannel(content: string, format: StreamFormat): string {
  if (format === "sse") return formatSse(content);
  if (format === "websocket") return formatWebsocket(content);
  if (format === "raw") return formatRaw(content);
  throw new Error(`Unknown stream format: ${format as string}`);
}
