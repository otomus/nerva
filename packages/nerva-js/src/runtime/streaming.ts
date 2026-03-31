/**
 * Streaming runtime wrapper — push structured token chunks to ctx.stream
 * as the LLM produces them.
 *
 * Wraps any AgentRuntime and intercepts output to push structured chunks
 * to the context's stream sink.
 *
 * @module runtime/streaming
 */

import type { ExecContext } from "../context.js";
import type { AgentInput, AgentResult, AgentRuntime } from "./index.js";
import { AgentStatus } from "./index.js";

// ---------------------------------------------------------------------------
// StreamChunkType
// ---------------------------------------------------------------------------

/**
 * Kind of streaming chunk pushed to the sink.
 *
 * - `token` — an incremental text token from the LLM.
 * - `progress` — a progress indicator (e.g. percentage or status message).
 * - `complete` — final chunk signalling the stream is done.
 * - `error` — an error occurred during streaming.
 */
export type StreamChunkType = "token" | "progress" | "complete" | "error";

// ---------------------------------------------------------------------------
// StreamChunk
// ---------------------------------------------------------------------------

/**
 * A single streaming chunk pushed to the context's stream sink.
 */
export interface StreamChunk {
  /** The kind of chunk. */
  readonly type: StreamChunkType;
  /** Text payload of the chunk. */
  readonly content: string;
  /** Unix timestamp when the chunk was created. */
  readonly timestamp: number;
}

// ---------------------------------------------------------------------------
// StreamingRuntime
// ---------------------------------------------------------------------------

/**
 * Wraps any AgentRuntime to push structured stream chunks to `ctx.stream`.
 *
 * When a handler is invoked, this wrapper delegates to the inner runtime
 * and pushes a COMPLETE chunk with the full output. If the handler fails,
 * an ERROR chunk is pushed instead.
 */
export class StreamingRuntime implements AgentRuntime {
  private readonly _inner: AgentRuntime;

  /**
   * @param inner - The underlying runtime to delegate execution to.
   */
  constructor(inner: AgentRuntime) {
    this._inner = inner;
  }

  /**
   * Invoke a handler and push structured chunks to `ctx.stream`.
   *
   * @param handler - Handler name to invoke.
   * @param input - Structured input for the handler.
   * @param ctx - Execution context with optional stream sink.
   * @returns AgentResult from the underlying runtime.
   */
  async invoke(
    handler: string,
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    const result = await this._inner.invoke(handler, input, ctx);
    await pushResultChunk(result, ctx);
    return result;
  }

  /**
   * Run handlers in sequence, pushing a final chunk for the last result.
   *
   * @param handlers - Ordered list of handler names.
   * @param input - Initial input for the first handler.
   * @param ctx - Execution context shared across the chain.
   * @returns AgentResult from the last successfully executed handler.
   */
  async invokeChain(
    handlers: string[],
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    const result = await this._inner.invokeChain(handlers, input, ctx);
    await pushResultChunk(result, ctx);
    return result;
  }

  /**
   * Delegate to another handler, pushing a chunk for the result.
   *
   * @param handler - Handler name to delegate to.
   * @param input - Input for the delegated handler.
   * @param parentCtx - Parent's execution context.
   * @returns AgentResult from the delegated handler.
   */
  async delegate(
    handler: string,
    input: AgentInput,
    parentCtx: ExecContext,
  ): Promise<AgentResult> {
    const result = await this._inner.delegate(handler, input, parentCtx);
    await pushResultChunk(result, parentCtx);
    return result;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Create a StreamChunk with the current timestamp.
 *
 * @param type - Kind of chunk.
 * @param content - Text payload.
 * @returns A StreamChunk object.
 */
export function buildChunk(type: StreamChunkType, content: string): StreamChunk {
  return { type, content, timestamp: Date.now() / 1000 };
}

/**
 * Serialize a StreamChunk to a JSON string for the stream sink.
 *
 * @param chunk - The chunk to serialize.
 * @returns JSON string representation.
 */
export function serializeChunk(chunk: StreamChunk): string {
  return JSON.stringify({
    type: chunk.type,
    content: chunk.content,
    timestamp: chunk.timestamp,
  });
}

/**
 * Push a structured chunk to the stream sink based on the result status.
 *
 * No-ops if the context has no stream attached.
 *
 * @param result - The agent result to convert to a chunk.
 * @param ctx - Execution context with optional stream sink.
 */
async function pushResultChunk(
  result: AgentResult,
  ctx: ExecContext,
): Promise<void> {
  if (ctx.stream === null) return;

  const chunk =
    result.status === AgentStatus.SUCCESS
      ? buildChunk("complete", result.output)
      : buildChunk("error", result.error ?? `handler failed with status ${result.status}`);

  await ctx.stream.push(serializeChunk(chunk));
}
