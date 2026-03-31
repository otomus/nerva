/**
 * Streaming tool manager — push progress events during tool execution.
 *
 * Wraps any ToolManager and emits structured streaming events before, after,
 * and on error of each tool call. These events flow through `ctx.stream`
 * so consumers can show real-time tool progress.
 *
 * @module tools/streaming
 */

import type { ExecContext } from "../context.js";
import type { ToolManager, ToolSpec, ToolResult } from "./index.js";
import { ToolStatus } from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Event type emitted before a tool call begins. */
export const TOOL_START_TYPE = "tool_start";

/** Event type emitted after a tool call completes successfully. */
export const TOOL_END_TYPE = "tool_end";

/** Event type emitted when a tool call fails. */
export const TOOL_ERROR_TYPE = "tool_error";

// ---------------------------------------------------------------------------
// StreamingToolManager
// ---------------------------------------------------------------------------

/**
 * Wraps a ToolManager to push progress events during tool execution.
 *
 * Before each call, a `tool_start` event is pushed. After completion,
 * either `tool_end` (with duration) or `tool_error` is pushed.
 */
export class StreamingToolManager implements ToolManager {
  private readonly _inner: ToolManager;

  /**
   * @param inner - The underlying tool manager to delegate to.
   */
  constructor(inner: ToolManager) {
    this._inner = inner;
  }

  /**
   * Discover available tools — delegates directly to the inner manager.
   *
   * @param ctx - Execution context carrying identity and permission set.
   * @returns List of tool specs.
   */
  async discover(ctx: ExecContext): Promise<ToolSpec[]> {
    return this._inner.discover(ctx);
  }

  /**
   * Execute a tool call, emitting streaming progress events.
   *
   * Pushes a `tool_start` event before execution, then either
   * `tool_end` (on success) or `tool_error` (on failure).
   *
   * @param tool - Tool name to invoke.
   * @param args - Arguments matching the tool's parameter schema.
   * @param ctx - Execution context carrying identity and permission set.
   * @returns ToolResult from the underlying tool manager.
   */
  async call(
    tool: string,
    args: Record<string, unknown>,
    ctx: ExecContext,
  ): Promise<ToolResult> {
    await pushEvent(ctx, buildStartEvent(tool));
    const startedAt = performance.now();

    let result: ToolResult;
    try {
      result = await this._inner.call(tool, args, ctx);
    } catch (err: unknown) {
      const durationMs = performance.now() - startedAt;
      const errorMsg = err instanceof Error ? err.message : String(err);
      await pushEvent(ctx, buildErrorEvent(tool, errorMsg, durationMs));
      throw err;
    }

    const durationMs = performance.now() - startedAt;

    if (result.status === ToolStatus.SUCCESS) {
      await pushEvent(ctx, buildEndEvent(tool, durationMs));
    } else {
      const errorMsg = result.error ?? `tool failed with status ${result.status}`;
      await pushEvent(ctx, buildErrorEvent(tool, errorMsg, durationMs));
    }

    return result;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build a tool_start event payload.
 *
 * @param tool - Tool name.
 * @returns Event object with type and tool name.
 */
function buildStartEvent(tool: string): Record<string, unknown> {
  return { type: TOOL_START_TYPE, tool };
}

/**
 * Build a tool_end event payload.
 *
 * @param tool - Tool name.
 * @param durationMs - Execution duration in milliseconds.
 * @returns Event object with type, tool name, and duration.
 */
function buildEndEvent(tool: string, durationMs: number): Record<string, unknown> {
  return { type: TOOL_END_TYPE, tool, duration_ms: Math.round(durationMs * 100) / 100 };
}

/**
 * Build a tool_error event payload.
 *
 * @param tool - Tool name.
 * @param error - Error message.
 * @param durationMs - Execution duration in milliseconds.
 * @returns Event object with type, tool name, error, and duration.
 */
function buildErrorEvent(
  tool: string,
  error: string,
  durationMs: number,
): Record<string, unknown> {
  return {
    type: TOOL_ERROR_TYPE,
    tool,
    error,
    duration_ms: Math.round(durationMs * 100) / 100,
  };
}

/**
 * Push a JSON-serialised event to the context's stream sink.
 * No-ops if the context has no stream attached.
 *
 * @param ctx - Execution context with optional stream sink.
 * @param event - Event payload to serialize and push.
 */
async function pushEvent(
  ctx: ExecContext,
  event: Record<string, unknown>,
): Promise<void> {
  if (ctx.stream === null) return;
  await ctx.stream.push(JSON.stringify(event));
}
