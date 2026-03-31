/**
 * Tracing — observe execution flow across primitives.
 *
 * Defines the {@link Tracer} interface that all tracer implementations must
 * satisfy. Tracers receive callbacks for span lifecycle events, discrete
 * events, and request completion — enabling structured logging, metrics
 * export, and distributed tracing integrations.
 *
 * @module tracing
 */

import type { ExecContext, Span, Event } from "../context.js";

export type { ExecContext, Span, Event };

// ---------------------------------------------------------------------------
// Tracer interface
// ---------------------------------------------------------------------------

/**
 * Observes execution spans and events from the pipeline.
 *
 * Implement this interface to receive structured callbacks as requests
 * flow through Nerva primitives. Multiple tracers can be active
 * simultaneously (e.g. one for JSON logging, one for metrics).
 */
export interface Tracer {
  /**
   * Called when a new span begins.
   *
   * @param span - The span that just started (`endedAt` will be null).
   * @param ctx - Execution context the span belongs to.
   */
  onSpanStart(span: Span, ctx: ExecContext): void;

  /**
   * Called when a span completes.
   *
   * @param span - The span that just ended (`endedAt` will be populated).
   * @param ctx - Execution context the span belongs to.
   */
  onSpanEnd(span: Span, ctx: ExecContext): void;

  /**
   * Called when a structured event is recorded.
   *
   * @param event - The point-in-time event that was recorded.
   * @param ctx - Execution context the event belongs to.
   */
  onEvent(event: Event, ctx: ExecContext): void;

  /**
   * Called when the entire request completes.
   *
   * This fires once per root context, after all spans have ended.
   * Use it for flushing buffers, emitting summary metrics, or
   * closing file handles.
   *
   * @param ctx - The completed execution context with final token usage and spans.
   */
  onComplete(ctx: ExecContext): void;
}
