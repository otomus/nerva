import { describe, it, expect } from "vitest";
import {
  ExecContext,
  createPermissions,
  TokenUsage,
  InMemoryStreamSink,
  SCOPE_VALUES,
} from "../src/context.js";
import type { Scope } from "../src/context.js";

// ---------------------------------------------------------------------------
// ExecContext.create()
// ---------------------------------------------------------------------------

describe("ExecContext.create", () => {
  it("produces a context with non-empty requestId and traceId", () => {
    const ctx = ExecContext.create();
    expect(ctx.requestId).toBeTruthy();
    expect(ctx.traceId).toBeTruthy();
    expect(ctx.requestId).not.toBe(ctx.traceId);
  });

  it("generates hex-only IDs (no dashes from UUID)", () => {
    const ctx = ExecContext.create();
    expect(ctx.requestId).toMatch(/^[0-9a-f]+$/);
    expect(ctx.traceId).toMatch(/^[0-9a-f]+$/);
  });

  it("generates unique IDs on each call", () => {
    const a = ExecContext.create();
    const b = ExecContext.create();
    expect(a.requestId).not.toBe(b.requestId);
    expect(a.traceId).not.toBe(b.traceId);
  });

  it("defaults userId and sessionId to null", () => {
    const ctx = ExecContext.create();
    expect(ctx.userId).toBeNull();
    expect(ctx.sessionId).toBeNull();
  });

  it("defaults memoryScope to 'session'", () => {
    const ctx = ExecContext.create();
    expect(ctx.memoryScope).toBe("session");
  });

  it("respects custom options", () => {
    const perms = createPermissions({ roles: new Set(["admin"]) });
    const ctx = ExecContext.create({
      userId: "u-1",
      sessionId: "s-1",
      permissions: perms,
      memoryScope: "user",
      timeoutSeconds: 10,
    });
    expect(ctx.userId).toBe("u-1");
    expect(ctx.sessionId).toBe("s-1");
    expect(ctx.permissions.hasRole("admin")).toBe(true);
    expect(ctx.memoryScope).toBe("user");
    expect(ctx.timeoutAt).not.toBeNull();
  });

  it("starts with empty spans, events, and zero token usage", () => {
    const ctx = ExecContext.create();
    expect(ctx.spans).toHaveLength(0);
    expect(ctx.events).toHaveLength(0);
    expect(ctx.tokenUsage.totalTokens).toBe(0);
  });

  it("sets stream to null when not provided", () => {
    const ctx = ExecContext.create();
    expect(ctx.stream).toBeNull();
  });

  it("accepts a StreamSink via options", () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });
    expect(ctx.stream).toBe(sink);
  });

  it("sets timeoutAt to null when no timeout specified", () => {
    const ctx = ExecContext.create();
    expect(ctx.timeoutAt).toBeNull();
  });

  it("handles empty string userId", () => {
    const ctx = ExecContext.create({ userId: "" });
    expect(ctx.userId).toBe("");
  });

  it("handles very long userId without error", () => {
    const longId = "x".repeat(10_000);
    const ctx = ExecContext.create({ userId: longId });
    expect(ctx.userId).toBe(longId);
  });
});

// ---------------------------------------------------------------------------
// ExecContext.child()
// ---------------------------------------------------------------------------

describe("ExecContext.child", () => {
  it("inherits traceId from parent", () => {
    const parent = ExecContext.create({ userId: "u-1", sessionId: "s-1" });
    const child = parent.child("sub-handler");
    expect(child.traceId).toBe(parent.traceId);
  });

  it("gets a fresh requestId different from parent", () => {
    const parent = ExecContext.create();
    const child = parent.child("sub");
    expect(child.requestId).not.toBe(parent.requestId);
  });

  it("inherits permissions from parent", () => {
    const perms = createPermissions({ roles: new Set(["admin"]) });
    const parent = ExecContext.create({ permissions: perms });
    const child = parent.child("sub");
    expect(child.permissions.hasRole("admin")).toBe(true);
  });

  it("inherits userId and sessionId", () => {
    const parent = ExecContext.create({ userId: "u-1", sessionId: "s-1" });
    const child = parent.child("sub");
    expect(child.userId).toBe("u-1");
    expect(child.sessionId).toBe("s-1");
  });

  it("inherits memoryScope", () => {
    const parent = ExecContext.create({ memoryScope: "global" });
    const child = parent.child("sub");
    expect(child.memoryScope).toBe("global");
  });

  it("shares cancellation controller with parent", () => {
    const parent = ExecContext.create();
    const child = parent.child("sub");
    parent.cancel();
    expect(child.isCancelled()).toBe(true);
  });

  it("starts with a root span named after handlerName", () => {
    const parent = ExecContext.create();
    const child = parent.child("my-handler");
    expect(child.spans).toHaveLength(1);
    expect(child.spans[0]!.name).toBe("my-handler");
    expect(child.spans[0]!.parentId).toBe(parent.requestId);
  });

  it("starts with empty events and zero token usage", () => {
    const parent = ExecContext.create();
    const child = parent.child("sub");
    expect(child.events).toHaveLength(0);
    expect(child.tokenUsage.totalTokens).toBe(0);
  });

  it("inherits metadata as a shallow copy", () => {
    const parent = ExecContext.create();
    parent.metadata["key"] = "val";
    const child = parent.child("sub");
    expect(child.metadata["key"]).toBe("val");
    // mutation on child does not affect parent
    child.metadata["key"] = "changed";
    expect(parent.metadata["key"]).toBe("val");
  });
});

// ---------------------------------------------------------------------------
// Permissions
// ---------------------------------------------------------------------------

describe("createPermissions", () => {
  it("defaults to unrestricted (null allowlists)", () => {
    const p = createPermissions();
    expect(p.canUseTool("anything")).toBe(true);
    expect(p.canUseAgent("anything")).toBe(true);
  });

  it("canUseTool returns false for non-allowed tools", () => {
    const p = createPermissions({ allowedTools: new Set(["search"]) });
    expect(p.canUseTool("search")).toBe(true);
    expect(p.canUseTool("delete")).toBe(false);
  });

  it("canUseAgent returns false for non-allowed agents", () => {
    const p = createPermissions({ allowedAgents: new Set(["helper"]) });
    expect(p.canUseAgent("helper")).toBe(true);
    expect(p.canUseAgent("destroyer")).toBe(false);
  });

  it("hasRole checks role membership", () => {
    const p = createPermissions({ roles: new Set(["admin", "user"]) });
    expect(p.hasRole("admin")).toBe(true);
    expect(p.hasRole("superadmin")).toBe(false);
  });

  it("empty allowedTools set blocks all tools", () => {
    const p = createPermissions({ allowedTools: new Set<string>() });
    expect(p.canUseTool("anything")).toBe(false);
  });

  it("empty allowedAgents set blocks all agents", () => {
    const p = createPermissions({ allowedAgents: new Set<string>() });
    expect(p.canUseAgent("anything")).toBe(false);
  });

  it("empty roles set means hasRole always false", () => {
    const p = createPermissions();
    expect(p.hasRole("admin")).toBe(false);
  });

  it("is frozen (immutable)", () => {
    const p = createPermissions();
    expect(Object.isFrozen(p)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// TokenUsage
// ---------------------------------------------------------------------------

describe("TokenUsage", () => {
  it("defaults all fields to 0", () => {
    const u = new TokenUsage();
    expect(u.promptTokens).toBe(0);
    expect(u.completionTokens).toBe(0);
    expect(u.totalTokens).toBe(0);
    expect(u.costUsd).toBe(0);
  });

  it("add() returns a new instance with summed fields", () => {
    const a = new TokenUsage(10, 20, 30, 0.5);
    const b = new TokenUsage(5, 10, 15, 0.25);
    const result = a.add(b);
    expect(result.promptTokens).toBe(15);
    expect(result.completionTokens).toBe(30);
    expect(result.totalTokens).toBe(45);
    expect(result.costUsd).toBeCloseTo(0.75);
  });

  it("add() does not mutate the original", () => {
    const a = new TokenUsage(10, 20, 30, 0.5);
    const b = new TokenUsage(5, 10, 15, 0.25);
    a.add(b);
    expect(a.promptTokens).toBe(10);
    expect(a.completionTokens).toBe(20);
  });

  it("add() with two zero-value instances returns zero", () => {
    const a = new TokenUsage();
    const b = new TokenUsage();
    const result = a.add(b);
    expect(result.totalTokens).toBe(0);
    expect(result.costUsd).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// InMemoryStreamSink
// ---------------------------------------------------------------------------

describe("InMemoryStreamSink", () => {
  it("collects pushed chunks in order", async () => {
    const sink = new InMemoryStreamSink();
    await sink.push("hello ");
    await sink.push("world");
    expect(sink.chunks).toEqual(["hello ", "world"]);
  });

  it("starts not closed", () => {
    const sink = new InMemoryStreamSink();
    expect(sink.closed).toBe(false);
  });

  it("sets closed to true after close()", async () => {
    const sink = new InMemoryStreamSink();
    await sink.close();
    expect(sink.closed).toBe(true);
  });

  it("throws on push after close", async () => {
    const sink = new InMemoryStreamSink();
    await sink.close();
    await expect(sink.push("x")).rejects.toThrow("Cannot push to a closed StreamSink");
  });

  it("throws on double close", async () => {
    const sink = new InMemoryStreamSink();
    await sink.close();
    await expect(sink.close()).rejects.toThrow("StreamSink is already closed");
  });

  it("handles empty string push", async () => {
    const sink = new InMemoryStreamSink();
    await sink.push("");
    expect(sink.chunks).toEqual([""]);
  });

  it("handles unicode content", async () => {
    const sink = new InMemoryStreamSink();
    await sink.push("\u{1F600} hello \u4E16\u754C");
    expect(sink.chunks[0]).toContain("\u{1F600}");
  });
});

// ---------------------------------------------------------------------------
// isTimedOut / isCancelled
// ---------------------------------------------------------------------------

describe("ExecContext timeout and cancellation", () => {
  it("isTimedOut returns false when no timeout set", () => {
    const ctx = ExecContext.create();
    expect(ctx.isTimedOut()).toBe(false);
  });

  it("isTimedOut returns false when timeout is far in the future", () => {
    const ctx = ExecContext.create({ timeoutSeconds: 3600 });
    expect(ctx.isTimedOut()).toBe(false);
  });

  it("isTimedOut returns true when timeout is in the past", () => {
    const ctx = ExecContext.create({ timeoutSeconds: -1 });
    expect(ctx.isTimedOut()).toBe(true);
  });

  it("isCancelled returns false initially", () => {
    const ctx = ExecContext.create();
    expect(ctx.isCancelled()).toBe(false);
  });

  it("isCancelled returns true after cancel()", () => {
    const ctx = ExecContext.create();
    ctx.cancel();
    expect(ctx.isCancelled()).toBe(true);
  });

  it("cancelSignal is an AbortSignal", () => {
    const ctx = ExecContext.create();
    expect(ctx.cancelSignal).toBeInstanceOf(AbortSignal);
  });

  it("elapsedSeconds returns a non-negative number", () => {
    const ctx = ExecContext.create();
    expect(ctx.elapsedSeconds()).toBeGreaterThanOrEqual(0);
  });
});

// ---------------------------------------------------------------------------
// Span and Event helpers
// ---------------------------------------------------------------------------

describe("ExecContext span and event helpers", () => {
  it("addSpan appends a span with the given name", () => {
    const ctx = ExecContext.create();
    const span = ctx.addSpan("llm.call");
    expect(span.name).toBe("llm.call");
    expect(span.endedAt).toBeNull();
    expect(ctx.spans).toHaveLength(1);
  });

  it("addEvent appends an event with the given name", () => {
    const ctx = ExecContext.create();
    const event = ctx.addEvent("policy.denied", { target: "deploy" });
    expect(event.name).toBe("policy.denied");
    expect(event.attributes["target"]).toBe("deploy");
    expect(ctx.events).toHaveLength(1);
  });

  it("recordTokens accumulates usage immutably", () => {
    const ctx = ExecContext.create();
    const usage = new TokenUsage(100, 50, 150, 0.01);
    ctx.recordTokens(usage);
    expect(ctx.tokenUsage.totalTokens).toBe(150);
    ctx.recordTokens(usage);
    expect(ctx.tokenUsage.totalTokens).toBe(300);
  });
});

// ---------------------------------------------------------------------------
// Scope values
// ---------------------------------------------------------------------------

describe("SCOPE_VALUES", () => {
  it("contains all four valid scopes", () => {
    expect(SCOPE_VALUES).toContain("user");
    expect(SCOPE_VALUES).toContain("session");
    expect(SCOPE_VALUES).toContain("agent");
    expect(SCOPE_VALUES).toContain("global");
    expect(SCOPE_VALUES).toHaveLength(4);
  });
});
