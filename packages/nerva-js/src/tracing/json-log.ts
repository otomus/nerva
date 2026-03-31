/**
 * JSON log tracer — structured trace output to file or writable stream.
 *
 * Writes one JSON line per trace callback (span start, span end, event,
 * complete). Useful for local debugging, log aggregation pipelines, and
 * audit trails.
 *
 * @module tracing/json-log
 */

import * as fs from "node:fs";
import * as path from "node:path";

import type { ExecContext, Event, Span } from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Trace event type for span start records. */
const SPAN_START_TYPE = "span_start";

/** Trace event type for span end records. */
const SPAN_END_TYPE = "span_end";

/** Trace event type for discrete event records. */
const EVENT_TYPE = "event";

/** Trace event type for request completion records. */
const COMPLETE_TYPE = "complete";

// ---------------------------------------------------------------------------
// Writable abstraction
// ---------------------------------------------------------------------------

/** Minimal writable interface — anything with a `write` method. */
export interface Writable {
  write(data: string): void;
}

// ---------------------------------------------------------------------------
// JsonLogTracer
// ---------------------------------------------------------------------------

/**
 * Writes trace events as JSON lines to a file or writable stream.
 *
 * Each span start/end and event produces one JSON line containing a
 * timestamp, event type, request/trace identifiers, and relevant data
 * fields from the span or event.
 */
export class JsonLogTracer {
  private readonly stream: Writable;
  private readonly ownsFile: boolean;

  /**
   * @param output - Where to write JSON lines. Accepts a file path (string)
   *   which will be opened in append mode, or any object with a `write(string)`
   *   method. Defaults to `process.stderr`.
   */
  constructor(output?: string | Writable) {
    const resolved = resolveOutput(output);
    this.stream = resolved.stream;
    this.ownsFile = resolved.ownsFile;
  }

  /**
   * Write a JSON line when a span begins.
   *
   * @param span - The span that just started.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanStart(span: Span, ctx: ExecContext): void {
    const payload = buildSpanPayload(span);
    this.writeLine(SPAN_START_TYPE, ctx, payload);
  }

  /**
   * Write a JSON line when a span ends.
   *
   * Includes the span's duration in seconds when `endedAt` is available.
   *
   * @param span - The span that just ended.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanEnd(span: Span, ctx: ExecContext): void {
    const payload = buildSpanPayload(span);
    if (span.endedAt !== null) {
      payload["duration_s"] = round6(span.endedAt - span.startedAt);
    }
    this.writeLine(SPAN_END_TYPE, ctx, payload);
  }

  /**
   * Write a JSON line when an event is recorded.
   *
   * @param event - The point-in-time event.
   * @param ctx - Execution context the event belongs to.
   */
  onEvent(event: Event, ctx: ExecContext): void {
    const payload = buildEventPayload(event);
    this.writeLine(EVENT_TYPE, ctx, payload);
  }

  /**
   * Write a JSON line when a request completes.
   *
   * Includes summary fields: total span count, event count, and
   * accumulated token usage.
   *
   * @param ctx - The completed execution context.
   */
  onComplete(ctx: ExecContext): void {
    const payload = buildCompletePayload(ctx);
    this.writeLine(COMPLETE_TYPE, ctx, payload);
  }

  /**
   * Close the underlying stream if this tracer owns it.
   *
   * Safe to call multiple times. Only closes file handles that were
   * opened by this tracer (not caller-provided streams or stderr).
   */
  close(): void {
    if (this.ownsFile && "end" in this.stream) {
      (this.stream as fs.WriteStream).end();
    }
  }

  // -- Private helpers ----------------------------------------------------

  /**
   * Serialize and write a single JSON line to the output stream.
   *
   * @param eventType - One of the trace event type constants.
   * @param ctx - Execution context for identity fields.
   * @param payload - Type-specific data to merge into the line.
   */
  private writeLine(
    eventType: string,
    ctx: ExecContext,
    payload: Record<string, unknown>,
  ): void {
    const line = buildBaseRecord(eventType, ctx);
    Object.assign(line, payload);
    this.stream.write(JSON.stringify(line) + "\n");
  }
}

// ---------------------------------------------------------------------------
// Output resolution
// ---------------------------------------------------------------------------

/** Result of resolving the output target. */
interface ResolvedOutput {
  readonly stream: Writable;
  readonly ownsFile: boolean;
}

/**
 * Turn the user-provided output target into a writable stream.
 *
 * @param output - File path, writable stream, or undefined for stderr.
 * @returns The resolved stream and whether this tracer owns it.
 */
function resolveOutput(output: string | Writable | undefined): ResolvedOutput {
  if (output === undefined) {
    return { stream: process.stderr, ownsFile: false };
  }

  if (typeof output === "string") {
    const resolved = path.resolve(output);
    const dir = path.dirname(resolved);
    fs.mkdirSync(dir, { recursive: true });
    const stream = fs.createWriteStream(resolved, { flags: "a", encoding: "utf-8" });
    return { stream, ownsFile: true };
  }

  return { stream: output, ownsFile: false };
}

// ---------------------------------------------------------------------------
// Pure helpers — build JSON-serialisable records
// ---------------------------------------------------------------------------

/**
 * Create the common envelope shared by every JSON line.
 *
 * @param eventType - The trace event type string.
 * @param ctx - Execution context to pull identifiers from.
 * @returns Record with type, timestamp, request_id, and trace_id.
 */
function buildBaseRecord(
  eventType: string,
  ctx: ExecContext,
): Record<string, unknown> {
  return {
    type: eventType,
    timestamp: Date.now() / 1000,
    request_id: ctx.requestId,
    trace_id: ctx.traceId,
  };
}

/**
 * Extract span fields into a JSON-ready record.
 *
 * @param span - The span to serialise.
 * @returns Record containing span identity, name, timing, and attributes.
 */
function buildSpanPayload(span: Span): Record<string, unknown> {
  return {
    span_id: span.spanId,
    span_name: span.name,
    parent_id: span.parentId,
    started_at: span.startedAt,
    ended_at: span.endedAt,
    attributes: { ...span.attributes },
  };
}

/**
 * Extract event fields into a JSON-ready record.
 *
 * @param event - The event to serialise.
 * @returns Record containing event name, timestamp, and attributes.
 */
function buildEventPayload(event: Event): Record<string, unknown> {
  return {
    event_name: event.name,
    event_timestamp: event.timestamp,
    attributes: { ...event.attributes },
  };
}

/**
 * Build summary data for the completion record.
 *
 * @param ctx - The completed execution context.
 * @returns Record with span/event counts and token usage breakdown.
 */
function buildCompletePayload(ctx: ExecContext): Record<string, unknown> {
  const elapsedSeconds = (Date.now() / 1000) - ctx.createdAt;
  return {
    span_count: ctx.spans.length,
    event_count: ctx.events.length,
    token_usage: {
      prompt_tokens: ctx.tokenUsage.promptTokens,
      completion_tokens: ctx.tokenUsage.completionTokens,
      total_tokens: ctx.tokenUsage.totalTokens,
      cost_usd: ctx.tokenUsage.costUsd,
    },
    elapsed_s: round6(elapsedSeconds),
  };
}

/**
 * Round a number to 6 decimal places.
 *
 * @param value - The number to round.
 * @returns The rounded number.
 */
function round6(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}
