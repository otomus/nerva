/**
 * Cost tracking tracer — calculates USD cost from token usage.
 *
 * Wraps any `Tracer` implementation and enriches span events with
 * `cost_usd` computed from a per-model pricing configuration.
 *
 * @module tracing/cost
 */

import type { ExecContext, Span, Event } from "./index.js";
import type { Tracer } from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Fallback cost when a model is not found in the pricing config. */
export const DEFAULT_COST_PER_1K_TOKENS = 0.0;

/** Span attribute key used to look up the model name for pricing. */
export const MODEL_ATTRIBUTE_KEY = "model";

/** Span attribute key where the computed cost is stored. */
export const COST_ATTRIBUTE_KEY = "cost_usd";

// ---------------------------------------------------------------------------
// Pricing types and helpers
// ---------------------------------------------------------------------------

/** Mapping of model name to cost per 1,000 tokens (USD). */
export type ModelPricing = Readonly<Record<string, number>>;

/**
 * Compute the USD cost for a given token count.
 *
 * @param totalTokens - Number of tokens consumed.
 * @param costPer1k - Cost in USD per 1,000 tokens.
 * @returns Computed cost in USD. Returns 0.0 for zero or negative tokens.
 */
export function calculateCost(totalTokens: number, costPer1k: number): number {
  if (totalTokens <= 0 || costPer1k <= 0) return 0.0;
  return (totalTokens / 1000) * costPer1k;
}

/**
 * Look up the per-1k-token cost for a model.
 *
 * @param modelName - The model identifier, or `null` if unknown.
 * @param pricing - Pricing configuration mapping model names to costs.
 * @returns Cost per 1,000 tokens, or 0.0 if the model is unknown.
 */
export function lookupModelCost(modelName: string | null, pricing: ModelPricing): number {
  if (modelName === null) return DEFAULT_COST_PER_1K_TOKENS;
  return pricing[modelName] ?? DEFAULT_COST_PER_1K_TOKENS;
}

// ---------------------------------------------------------------------------
// CostTracker
// ---------------------------------------------------------------------------

/**
 * Tracer wrapper that calculates and records cost from token usage.
 *
 * Delegates all tracing callbacks to the wrapped tracer, and additionally
 * computes `cost_usd` from the context's token usage on span end
 * and request completion.
 */
export class CostTracker implements Tracer {
  private readonly _inner: Tracer;
  private readonly _pricing: ModelPricing;
  private readonly _spanTokenSnapshots: Map<string, number> = new Map();

  /**
   * @param inner - The tracer to delegate callbacks to.
   * @param pricing - Mapping of model name to cost per 1,000 tokens (USD).
   */
  constructor(inner: Tracer, pricing: ModelPricing) {
    this._inner = inner;
    this._pricing = pricing;
  }

  /**
   * Record the token count at span start and delegate.
   *
   * @param span - The span that just started.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanStart(span: Span, ctx: ExecContext): void {
    this._spanTokenSnapshots.set(span.spanId, ctx.tokenUsage.totalTokens);
    this._inner.onSpanStart(span, ctx);
  }

  /**
   * Compute cost for the span's token delta and delegate.
   *
   * @param span - The span that just ended.
   * @param ctx - Execution context the span belongs to.
   */
  onSpanEnd(span: Span, ctx: ExecContext): void {
    const startTokens = this._spanTokenSnapshots.get(span.spanId) ?? 0;
    this._spanTokenSnapshots.delete(span.spanId);

    const deltaTokens = ctx.tokenUsage.totalTokens - startTokens;
    const modelName = span.attributes[MODEL_ATTRIBUTE_KEY] ?? null;
    const costPer1k = lookupModelCost(modelName, this._pricing);
    const cost = calculateCost(deltaTokens, costPer1k);

    ctx.addEvent("cost.calculated", {
      span_id: span.spanId,
      model: modelName ?? "unknown",
      delta_tokens: String(deltaTokens),
      cost_usd: cost.toFixed(6),
    });

    this._inner.onSpanEnd(span, ctx);
  }

  /**
   * Delegate event callback to the inner tracer.
   *
   * @param event - The point-in-time event that was recorded.
   * @param ctx - Execution context the event belongs to.
   */
  onEvent(event: Event, ctx: ExecContext): void {
    this._inner.onEvent(event, ctx);
  }

  /**
   * Calculate total request cost and delegate.
   *
   * @param ctx - The completed execution context.
   */
  onComplete(ctx: ExecContext): void {
    const totalCost = this._calculateTotalCost(ctx);
    ctx.addEvent("cost.total", {
      total_tokens: String(ctx.tokenUsage.totalTokens),
      cost_usd: totalCost.toFixed(6),
    });
    this._inner.onComplete(ctx);
  }

  /**
   * Sum cost across all cost.calculated events in the context.
   */
  private _calculateTotalCost(ctx: ExecContext): number {
    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    if (costEvents.length > 0) {
      return costEvents.reduce(
        (sum, e) => sum + parseFloat(e.attributes["cost_usd"] ?? "0"),
        0,
      );
    }
    return 0.0;
  }
}
