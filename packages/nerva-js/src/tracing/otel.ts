/**
 * OpenTelemetry tracer adapter — maps Nerva spans/events to OTel spans.
 *
 * Uses `@opentelemetry/api` when available. Falls back gracefully to a no-op
 * when the package is not installed, allowing the adapter to be instantiated
 * without hard-failing.
 *
 * @module tracing/otel
 */

import type { ExecContext, Span, Event } from "./index.js";
import type { Tracer } from "./index.js";

// ---------------------------------------------------------------------------
// Optional import — graceful fallback
// ---------------------------------------------------------------------------

/** Minimal shape of the OTel trace API we need. */
interface OTelTraceApi {
  getTracer(name: string): OTelTracerInstance;
}

/** Minimal shape of an OTel tracer. */
interface OTelTracerInstance {
  startSpan(name: string, options?: { attributes?: Record<string, string> }): OTelSpanInstance;
}

/** Minimal shape of an OTel span. */
interface OTelSpanInstance {
  end(): void;
  addEvent(name: string, attributes?: Record<string, string>): void;
}

let otelTrace: OTelTraceApi | null = null;
let otelAvailable = false;

try {
  // Dynamic import is not possible in a sync constructor, so we try require
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const otelApi = require("@opentelemetry/api");
  otelTrace = otelApi.trace as OTelTraceApi;
  otelAvailable = true;
} catch {
  // @opentelemetry/api not installed — graceful fallback
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default OTel service name when none is provided. */
const DEFAULT_SERVICE_NAME = "nerva";

// ---------------------------------------------------------------------------
// OTelTracer
// ---------------------------------------------------------------------------

/**
 * Check whether the @opentelemetry/api package is importable.
 *
 * @returns `true` if the package was successfully loaded.
 */
export function isOTelAvailable(): boolean {
  return otelAvailable;
}

/**
 * Maps Nerva tracing callbacks to OpenTelemetry spans and events.
 *
 * Each Nerva `Span` becomes an OTel span. Nerva `Event` objects are
 * added as OTel span events on the most recently started active span.
 *
 * When `@opentelemetry/api` is not installed, all callbacks are no-ops.
 */
export class OTelTracer implements Tracer {
  private readonly _resourceAttributes: Readonly<Record<string, string>>;
  private readonly _activeSpans: Map<string, OTelSpanInstance> = new Map();
  private readonly _tracer: OTelTracerInstance | null;

  /**
   * @param serviceName - OTel service name. Defaults to `"nerva"`.
   * @param resourceAttributes - Extra key-value attributes added to every span.
   */
  constructor(
    serviceName: string = DEFAULT_SERVICE_NAME,
    resourceAttributes: Record<string, string> = {},
  ) {
    this._resourceAttributes = Object.freeze({ ...resourceAttributes });
    this._tracer = otelTrace !== null ? otelTrace.getTracer(serviceName) : null;
  }

  /** @internal Exposed for testing. */
  get activeSpanCount(): number {
    return this._activeSpans.size;
  }

  /**
   * Start an OTel span corresponding to the Nerva span.
   *
   * @param span - The Nerva span that just started.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanStart(span: Span, ctx: ExecContext): void {
    if (this._tracer === null) return;

    const attributes = buildSpanAttributes(span, ctx, this._resourceAttributes);
    const otelSpan = this._tracer.startSpan(span.name, { attributes });
    this._activeSpans.set(span.spanId, otelSpan);
  }

  /**
   * End the OTel span corresponding to the Nerva span.
   *
   * @param span - The Nerva span that just ended.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanEnd(span: Span, _ctx: ExecContext): void {
    const otelSpan = this._activeSpans.get(span.spanId);
    if (otelSpan === undefined) return;

    this._activeSpans.delete(span.spanId);
    try {
      otelSpan.end();
    } catch {
      // Silently ignore end failures.
    }
  }

  /**
   * Add an OTel span event for the Nerva event.
   *
   * @param event - The point-in-time event that was recorded.
   * @param ctx - Execution context the event belongs to.
   */
  onEvent(event: Event, _ctx: ExecContext): void {
    if (this._activeSpans.size === 0) return;

    const lastKey = Array.from(this._activeSpans.keys()).pop();
    if (lastKey === undefined) return;

    const otelSpan = this._activeSpans.get(lastKey);
    if (otelSpan === undefined) return;

    try {
      otelSpan.addEvent(event.name, { ...event.attributes });
    } catch {
      // Silently ignore event failures.
    }
  }

  /**
   * Flush any remaining active spans when the request completes.
   *
   * @param ctx - The completed execution context.
   */
  onComplete(_ctx: ExecContext): void {
    for (const otelSpan of this._activeSpans.values()) {
      try {
        otelSpan.end();
      } catch {
        // Silently ignore end failures.
      }
    }
    this._activeSpans.clear();
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build the attribute dict for an OTel span.
 *
 * @param span - The Nerva span providing base attributes.
 * @param ctx - Execution context for identity fields.
 * @param extra - Additional resource attributes from the tracer config.
 * @returns Merged attribute record.
 */
function buildSpanAttributes(
  span: Span,
  ctx: ExecContext,
  extra: Readonly<Record<string, string>>,
): Record<string, string> {
  const attributes: Record<string, string> = {
    "nerva.request_id": ctx.requestId,
    "nerva.trace_id": ctx.traceId,
    "nerva.span_id": span.spanId,
  };

  if (span.parentId !== null) {
    attributes["nerva.parent_id"] = span.parentId;
  }

  Object.assign(attributes, span.attributes, extra);
  return attributes;
}
