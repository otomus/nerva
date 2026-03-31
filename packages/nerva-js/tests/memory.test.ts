import { describe, it, expect } from "vitest";
import { InMemoryHotMemory, DEFAULT_MAX_MESSAGES } from "../src/memory/hot.js";
import {
  TieredMemory,
  estimateStringTokens,
  estimateStringsTokens,
  estimateMessagesTokens,
  DEFAULT_TOKEN_BUDGET,
  CHARS_PER_TOKEN,
} from "../src/memory/tiered.js";
import {
  createMemoryEvent,
  createEmptyMemoryContext,
  MemoryTier,
} from "../src/memory/index.js";
import { ExecContext } from "../src/context.js";
import type { WarmTier, ColdTier } from "../src/memory/tiered.js";
import type { Message } from "../src/memory/index.js";

function makeCtx(sessionId: string = "sess-1"): ExecContext {
  return ExecContext.create({ sessionId });
}

// ---------------------------------------------------------------------------
// InMemoryHotMemory
// ---------------------------------------------------------------------------

describe("InMemoryHotMemory", () => {
  it("stores and retrieves messages by session", async () => {
    const hot = new InMemoryHotMemory();
    await hot.addMessage("user", "hello", "s1");
    await hot.addMessage("assistant", "hi", "s1");
    const conv = await hot.getConversation("s1");
    expect(conv).toHaveLength(2);
    expect(conv[0]!.role).toBe("user");
    expect(conv[1]!.content).toBe("hi");
  });

  it("returns empty array for unknown session", async () => {
    const hot = new InMemoryHotMemory();
    const conv = await hot.getConversation("nonexistent");
    expect(conv).toEqual([]);
  });

  it("isolates sessions", async () => {
    const hot = new InMemoryHotMemory();
    await hot.addMessage("user", "hello s1", "s1");
    await hot.addMessage("user", "hello s2", "s2");
    const conv1 = await hot.getConversation("s1");
    const conv2 = await hot.getConversation("s2");
    expect(conv1).toHaveLength(1);
    expect(conv2).toHaveLength(1);
    expect(conv1[0]!.content).toBe("hello s1");
  });

  it("clears messages for a specific session", async () => {
    const hot = new InMemoryHotMemory();
    await hot.addMessage("user", "hello", "s1");
    await hot.clear("s1");
    const conv = await hot.getConversation("s1");
    expect(conv).toEqual([]);
  });

  it("clear on nonexistent session is a no-op", async () => {
    const hot = new InMemoryHotMemory();
    await hot.clear("no-such-session"); // should not throw
  });

  it("returns a copy of conversation (mutation safe)", async () => {
    const hot = new InMemoryHotMemory();
    await hot.addMessage("user", "hello", "s1");
    const conv = await hot.getConversation("s1");
    conv.push({ role: "injected", content: "evil" });
    const again = await hot.getConversation("s1");
    expect(again).toHaveLength(1);
  });

  it("prunes oldest messages when exceeding maxMessages", async () => {
    const hot = new InMemoryHotMemory(3);
    await hot.addMessage("user", "m1", "s");
    await hot.addMessage("user", "m2", "s");
    await hot.addMessage("user", "m3", "s");
    await hot.addMessage("user", "m4", "s");
    const conv = await hot.getConversation("s");
    expect(conv).toHaveLength(3);
    expect(conv[0]!.content).toBe("m2");
    expect(conv[2]!.content).toBe("m4");
  });

  it("uses DEFAULT_MAX_MESSAGES when no limit specified", () => {
    const hot = new InMemoryHotMemory();
    expect(DEFAULT_MAX_MESSAGES).toBe(100);
  });

  it("throws on empty role", async () => {
    const hot = new InMemoryHotMemory();
    await expect(hot.addMessage("", "content", "s")).rejects.toThrow("role");
  });

  it("throws on whitespace-only role", async () => {
    const hot = new InMemoryHotMemory();
    await expect(hot.addMessage("  ", "content", "s")).rejects.toThrow("role");
  });

  it("throws on empty content", async () => {
    const hot = new InMemoryHotMemory();
    await expect(hot.addMessage("user", "", "s")).rejects.toThrow("content");
  });

  it("throws on whitespace-only content", async () => {
    const hot = new InMemoryHotMemory();
    await expect(hot.addMessage("user", "   ", "s")).rejects.toThrow("content");
  });
});

// ---------------------------------------------------------------------------
// Token estimation helpers
// ---------------------------------------------------------------------------

describe("Token estimation", () => {
  it("estimateStringTokens returns 0 for empty string", () => {
    expect(estimateStringTokens("")).toBe(0);
  });

  it("estimateStringTokens returns at least 1 for non-empty string", () => {
    expect(estimateStringTokens("hi")).toBeGreaterThanOrEqual(1);
  });

  it("estimateStringTokens scales with length", () => {
    const short = estimateStringTokens("hi");
    const long = estimateStringTokens("a".repeat(1000));
    expect(long).toBeGreaterThan(short);
  });

  it("estimateStringsTokens sums individual estimates", () => {
    const items = ["hello", "world"];
    const total = estimateStringsTokens(items);
    const sum = estimateStringTokens("hello") + estimateStringTokens("world");
    expect(total).toBe(sum);
  });

  it("estimateMessagesTokens sums message content estimates", () => {
    const msgs: Message[] = [
      { role: "user", content: "hello world" },
      { role: "assistant", content: "hi" },
    ];
    const total = estimateMessagesTokens(msgs);
    expect(total).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// createMemoryEvent / createEmptyMemoryContext
// ---------------------------------------------------------------------------

describe("createMemoryEvent", () => {
  it("defaults tier to HOT", () => {
    const evt = createMemoryEvent("test");
    expect(evt.tier).toBe(MemoryTier.HOT);
    expect(evt.scope).toBeNull();
    expect(evt.source).toBe("");
  });

  it("respects overrides", () => {
    const evt = createMemoryEvent("test", {
      tier: MemoryTier.COLD,
      scope: "global",
      source: "agent-a",
    });
    expect(evt.tier).toBe(MemoryTier.COLD);
    expect(evt.scope).toBe("global");
  });

  it("is frozen", () => {
    const evt = createMemoryEvent("test");
    expect(Object.isFrozen(evt)).toBe(true);
  });
});

describe("createEmptyMemoryContext", () => {
  it("returns all empty fields", () => {
    const ctx = createEmptyMemoryContext();
    expect(ctx.conversation).toEqual([]);
    expect(ctx.episodes).toEqual([]);
    expect(ctx.facts).toEqual([]);
    expect(ctx.knowledge).toEqual([]);
    expect(ctx.tokenCount).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// TieredMemory
// ---------------------------------------------------------------------------

describe("TieredMemory", () => {
  it("works with no tiers (all empty results)", async () => {
    const mem = new TieredMemory();
    const result = await mem.recall("query", makeCtx());
    expect(result.conversation).toEqual([]);
    expect(result.episodes).toEqual([]);
    expect(result.facts).toEqual([]);
    expect(result.knowledge).toEqual([]);
  });

  it("recalls from hot tier", async () => {
    const hot = new InMemoryHotMemory();
    await hot.addMessage("user", "hello", "sess-1");
    const mem = new TieredMemory({ hot });
    const result = await mem.recall("query", makeCtx("sess-1"));
    expect(result.conversation).toHaveLength(1);
    expect(result.conversation[0]!.content).toBe("hello");
  });

  it("recalls from warm tier", async () => {
    const warm: WarmTier = {
      getEpisodes: async () => ["ep1", "ep2"],
      getFacts: async () => ["fact1"],
      store: async () => {},
    };
    const mem = new TieredMemory({ warm });
    const result = await mem.recall("query", makeCtx());
    expect(result.episodes).toEqual(["ep1", "ep2"]);
    expect(result.facts).toEqual(["fact1"]);
  });

  it("recalls from cold tier", async () => {
    const cold: ColdTier = {
      search: async () => ["knowledge-1"],
      store: async () => {},
    };
    const mem = new TieredMemory({ cold });
    const result = await mem.recall("query", makeCtx());
    expect(result.knowledge).toEqual(["knowledge-1"]);
  });

  it("store routes HOT event to hot tier", async () => {
    const hot = new InMemoryHotMemory();
    const mem = new TieredMemory({ hot });
    const evt = createMemoryEvent("test msg", { tier: MemoryTier.HOT, source: "user" });
    await mem.store(evt, makeCtx("sess-1"));
    const conv = await hot.getConversation("sess-1");
    expect(conv).toHaveLength(1);
  });

  it("store routes WARM event to warm tier", async () => {
    let stored = "";
    const warm: WarmTier = {
      getEpisodes: async () => [],
      getFacts: async () => [],
      store: async (content) => { stored = content; },
    };
    const mem = new TieredMemory({ warm });
    const evt = createMemoryEvent("warm data", { tier: MemoryTier.WARM });
    await mem.store(evt, makeCtx());
    expect(stored).toBe("warm data");
  });

  it("store routes COLD event to cold tier", async () => {
    let stored = "";
    const cold: ColdTier = {
      search: async () => [],
      store: async (content) => { stored = content; },
    };
    const mem = new TieredMemory({ cold });
    const evt = createMemoryEvent("cold data", { tier: MemoryTier.COLD });
    await mem.store(evt, makeCtx());
    expect(stored).toBe("cold data");
  });

  it("store is no-op when target tier is missing", async () => {
    const mem = new TieredMemory();
    const evt = createMemoryEvent("data", { tier: MemoryTier.WARM });
    await mem.store(evt, makeCtx()); // should not throw
  });

  it("consolidate is a no-op that does not throw", async () => {
    const mem = new TieredMemory();
    await mem.consolidate(makeCtx());
  });

  it("uses sessionId from ctx, falls back to requestId", async () => {
    const hot = new InMemoryHotMemory();
    const mem = new TieredMemory({ hot });
    const ctx = ExecContext.create(); // no sessionId
    const evt = createMemoryEvent("msg", { tier: MemoryTier.HOT, source: "user" });
    await mem.store(evt, ctx);
    // should use requestId as session key
    const conv = await hot.getConversation(ctx.requestId);
    expect(conv).toHaveLength(1);
  });

  it("respects token budget by truncating results", async () => {
    const hot = new InMemoryHotMemory();
    // Add many large messages
    for (let i = 0; i < 50; i++) {
      await hot.addMessage("user", "x".repeat(500), "s");
    }
    const mem = new TieredMemory({ hot, tokenBudget: 100 });
    const result = await mem.recall("q", makeCtx("s"));
    // With budget of 100 tokens and each message ~125 tokens,
    // we should get very few messages back
    expect(result.conversation.length).toBeLessThan(50);
    expect(result.tokenCount).toBeLessThanOrEqual(100);
  });
});
