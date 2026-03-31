import { describe, it, expect } from "vitest";
import { ExecContext, TokenUsage } from "../src/context.js";
import type { Span, Event } from "../src/context.js";
import { OTelTracer, isOTelAvailable } from "../src/tracing/otel.js";
import {
  CostTracker,
  calculateCost,
  lookupModelCost,
  DEFAULT_COST_PER_1K_TOKENS,
} from "../src/tracing/cost.js";
import type { ModelPricing } from "../src/tracing/cost.js";
import type { Tracer } from "../src/tracing/index.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSpan(overrides: Partial<Span> = {}): Span {
  return {
    spanId: "span-1",
    name: "test.span",
    parentId: null,
    startedAt: 1000.0,
    endedAt: null,
    attributes: {},
    ...overrides,
  };
}

function makeEvent(overrides: Partial<Event> = {}): Event {
  return {
    timestamp: 1000.0,
    name: "test.event",
    attributes: {},
    ...overrides,
  };
}

class FakeTracer implements Tracer {
  spanStarts: Span[] = [];
  spanEnds: Span[] = [];
  events: Event[] = [];
  completes: ExecContext[] = [];

  onSpanStart(span: Span, _ctx: ExecContext): void {
    this.spanStarts.push(span);
  }
  onSpanEnd(span: Span, _ctx: ExecContext): void {
    this.spanEnds.push(span);
  }
  onEvent(event: Event, _ctx: ExecContext): void {
    this.events.push(event);
  }
  onComplete(ctx: ExecContext): void {
    this.completes.push(ctx);
  }
}

const SAMPLE_PRICING: ModelPricing = {
  "gpt-4": 0.03,
  "gpt-3.5-turbo": 0.002,
  "claude-3-opus": 0.015,
};

// ---------------------------------------------------------------------------
// OTel adapter (N-651)
// ---------------------------------------------------------------------------

describe("OTelTracer", () => {
  it("isOTelAvailable returns boolean", () => {
    expect(typeof isOTelAvailable()).toBe("boolean");
  });

  it("instantiates without OTel installed", () => {
    const tracer = new OTelTracer("test-service");
    expect(tracer).toBeTruthy();
  });

  it("onSpanStart is safe without OTel", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    tracer.onSpanStart(makeSpan(), ctx);
  });

  it("onSpanEnd is safe without OTel", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    tracer.onSpanEnd(makeSpan({ endedAt: 1001.0 }), ctx);
  });

  it("onEvent is safe without OTel", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    tracer.onEvent(makeEvent(), ctx);
  });

  it("onComplete is safe without OTel", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    tracer.onComplete(ctx);
  });

  it("onComplete clears active spans", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    tracer.onSpanStart(makeSpan(), ctx);
    tracer.onComplete(ctx);
    expect(tracer.activeSpanCount).toBe(0);
  });

  it("onEvent with no active spans is safe", () => {
    const tracer = new OTelTracer();
    const ctx = ExecContext.create();
    // Should not throw
    tracer.onEvent(makeEvent(), ctx);
  });
});

// ---------------------------------------------------------------------------
// Cost calculation (N-652)
// ---------------------------------------------------------------------------

describe("Cost calculation", () => {
  it("basic cost calculation", () => {
    const cost = calculateCost(1000, 0.03);
    expect(cost).toBeCloseTo(0.03);
  });

  it("500 tokens at $0.03/1k", () => {
    expect(calculateCost(500, 0.03)).toBeCloseTo(0.015);
  });

  it("zero tokens returns zero", () => {
    expect(calculateCost(0, 0.03)).toBe(0.0);
  });

  it("negative tokens returns zero", () => {
    expect(calculateCost(-100, 0.03)).toBe(0.0);
  });

  it("zero cost per 1k returns zero", () => {
    expect(calculateCost(5000, 0.0)).toBe(0.0);
  });

  it("negative cost per 1k returns zero", () => {
    expect(calculateCost(5000, -0.01)).toBe(0.0);
  });

  it("large token count", () => {
    expect(calculateCost(1_000_000, 0.03)).toBeCloseTo(30.0);
  });

  it("lookup known model", () => {
    expect(lookupModelCost("gpt-4", SAMPLE_PRICING)).toBe(0.03);
  });

  it("lookup unknown model", () => {
    expect(lookupModelCost("unknown", SAMPLE_PRICING)).toBe(DEFAULT_COST_PER_1K_TOKENS);
  });

  it("lookup null model", () => {
    expect(lookupModelCost(null, SAMPLE_PRICING)).toBe(DEFAULT_COST_PER_1K_TOKENS);
  });

  it("lookup empty pricing", () => {
    expect(lookupModelCost("gpt-4", {})).toBe(DEFAULT_COST_PER_1K_TOKENS);
  });
});

// ---------------------------------------------------------------------------
// CostTracker (N-652)
// ---------------------------------------------------------------------------

describe("CostTracker", () => {
  it("delegates span start", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();
    const span = makeSpan();

    tracker.onSpanStart(span, ctx);

    expect(inner.spanStarts).toHaveLength(1);
    expect(inner.spanStarts[0]).toBe(span);
  });

  it("delegates span end", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();
    const span = makeSpan({ endedAt: 1001.0 });

    tracker.onSpanStart(span, ctx);
    tracker.onSpanEnd(span, ctx);

    expect(inner.spanEnds).toHaveLength(1);
  });

  it("delegates event", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();
    const event = makeEvent();

    tracker.onEvent(event, ctx);

    expect(inner.events).toHaveLength(1);
    expect(inner.events[0]).toBe(event);
  });

  it("delegates complete", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    tracker.onComplete(ctx);

    expect(inner.completes).toHaveLength(1);
  });

  it("span end emits cost.calculated event", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    const span = makeSpan({ attributes: { model: "gpt-4" } });
    tracker.onSpanStart(span, ctx);

    ctx.recordTokens(new TokenUsage(500, 500, 1000));
    tracker.onSpanEnd(span, ctx);

    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    expect(costEvents).toHaveLength(1);
    expect(costEvents[0]!.attributes["model"]).toBe("gpt-4");
    expect(costEvents[0]!.attributes["delta_tokens"]).toBe("1000");
    expect(parseFloat(costEvents[0]!.attributes["cost_usd"]!)).toBeCloseTo(0.03);
  });

  it("unknown model gets zero cost", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    const span = makeSpan({ attributes: { model: "unknown-model" } });
    tracker.onSpanStart(span, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 1000));
    tracker.onSpanEnd(span, ctx);

    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    expect(parseFloat(costEvents[0]!.attributes["cost_usd"]!)).toBe(0.0);
  });

  it("no model attribute uses default", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    const span = makeSpan();
    tracker.onSpanStart(span, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 500));
    tracker.onSpanEnd(span, ctx);

    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    expect(costEvents[0]!.attributes["model"]).toBe("unknown");
  });

  it("token accumulation across spans", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    // First span: 500 tokens
    const span1 = makeSpan({ spanId: "s1", attributes: { model: "gpt-4" } });
    tracker.onSpanStart(span1, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 500));
    tracker.onSpanEnd(span1, ctx);

    // Second span: 300 more tokens
    const span2 = makeSpan({ spanId: "s2", attributes: { model: "gpt-3.5-turbo" } });
    tracker.onSpanStart(span2, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 300));
    tracker.onSpanEnd(span2, ctx);

    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    expect(costEvents).toHaveLength(2);

    expect(costEvents[0]!.attributes["delta_tokens"]).toBe("500");
    expect(parseFloat(costEvents[0]!.attributes["cost_usd"]!)).toBeCloseTo(0.015);

    expect(costEvents[1]!.attributes["delta_tokens"]).toBe("300");
    expect(parseFloat(costEvents[1]!.attributes["cost_usd"]!)).toBeCloseTo(0.0006);
  });

  it("on_complete emits total cost", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    const span = makeSpan({ attributes: { model: "gpt-4" } });
    tracker.onSpanStart(span, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 2000));
    tracker.onSpanEnd(span, ctx);

    tracker.onComplete(ctx);

    const totalEvents = ctx.events.filter((e) => e.name === "cost.total");
    expect(totalEvents).toHaveLength(1);
    expect(parseFloat(totalEvents[0]!.attributes["cost_usd"]!)).toBeCloseTo(0.06);
  });

  it("on_complete with zero tokens emits zero cost", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, SAMPLE_PRICING);
    const ctx = ExecContext.create();

    tracker.onComplete(ctx);

    const totalEvents = ctx.events.filter((e) => e.name === "cost.total");
    expect(totalEvents).toHaveLength(1);
    expect(parseFloat(totalEvents[0]!.attributes["cost_usd"]!)).toBe(0.0);
  });

  it("empty pricing yields zero cost for all models", () => {
    const inner = new FakeTracer();
    const tracker = new CostTracker(inner, {});
    const ctx = ExecContext.create();

    const span = makeSpan({ attributes: { model: "gpt-4" } });
    tracker.onSpanStart(span, ctx);
    ctx.recordTokens(new TokenUsage(0, 0, 1000));
    tracker.onSpanEnd(span, ctx);

    const costEvents = ctx.events.filter((e) => e.name === "cost.calculated");
    expect(parseFloat(costEvents[0]!.attributes["cost_usd"]!)).toBe(0.0);
  });
});
