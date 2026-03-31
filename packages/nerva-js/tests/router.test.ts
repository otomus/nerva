import { describe, it, expect } from "vitest";
import { RuleRouter } from "../src/router/rule.js";
import {
  createHandlerCandidate,
  createIntentResult,
} from "../src/router/index.js";
import { ExecContext } from "../src/context.js";
import type { Rule } from "../src/router/rule.js";

function makeCtx(): ExecContext {
  return ExecContext.create({ userId: "test-user" });
}

// ---------------------------------------------------------------------------
// createHandlerCandidate
// ---------------------------------------------------------------------------

describe("createHandlerCandidate", () => {
  it("creates a frozen candidate with valid score", () => {
    const c = createHandlerCandidate("handler-a", 0.8, "keyword match");
    expect(c.name).toBe("handler-a");
    expect(c.score).toBe(0.8);
    expect(c.reason).toBe("keyword match");
    expect(Object.isFrozen(c)).toBe(true);
  });

  it("defaults reason to empty string", () => {
    const c = createHandlerCandidate("h", 0.5);
    expect(c.reason).toBe("");
  });

  it("throws RangeError for score below 0", () => {
    expect(() => createHandlerCandidate("h", -0.1)).toThrow(RangeError);
  });

  it("throws RangeError for score above 1", () => {
    expect(() => createHandlerCandidate("h", 1.1)).toThrow(RangeError);
  });

  it("accepts boundary scores 0.0 and 1.0", () => {
    expect(createHandlerCandidate("h", 0.0).score).toBe(0.0);
    expect(createHandlerCandidate("h", 1.0).score).toBe(1.0);
  });

  it("does not throw for NaN score (NaN bypasses range check)", () => {
    // NaN < 0 and NaN > 1 are both false, so the guard passes.
    // This is a known edge case in the implementation.
    const c = createHandlerCandidate("h", NaN);
    expect(c.score).toBeNaN();
  });
});

// ---------------------------------------------------------------------------
// createIntentResult
// ---------------------------------------------------------------------------

describe("createIntentResult", () => {
  it("creates a frozen result with bestHandler pointing to first candidate", () => {
    const c = createHandlerCandidate("h", 0.9);
    const r = createIntentResult("greet", 0.9, [c]);
    expect(r.intent).toBe("greet");
    expect(r.confidence).toBe(0.9);
    expect(r.bestHandler).toBe(c);
    expect(Object.isFrozen(r)).toBe(true);
  });

  it("bestHandler is null when handlers list is empty", () => {
    const r = createIntentResult("unknown", 0.0, []);
    expect(r.bestHandler).toBeNull();
  });

  it("throws RangeError for invalid confidence", () => {
    expect(() => createIntentResult("x", -0.1, [])).toThrow(RangeError);
    expect(() => createIntentResult("x", 1.1, [])).toThrow(RangeError);
  });

  it("does not throw for NaN confidence (NaN bypasses range check)", () => {
    // NaN < 0 and NaN > 1 are both false, so the guard passes.
    // This is a known edge case in the implementation.
    const r = createIntentResult("x", NaN, []);
    expect(r.confidence).toBeNaN();
  });

  it("freezes handlers array", () => {
    const c = createHandlerCandidate("h", 1.0);
    const r = createIntentResult("x", 1.0, [c]);
    expect(Object.isFrozen(r.handlers)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// RuleRouter
// ---------------------------------------------------------------------------

describe("RuleRouter", () => {
  const rules: Rule[] = [
    { pattern: "\\bhello\\b", handler: "greeter", intent: "greet" },
    { pattern: "\\bweather\\b", handler: "weather-agent", intent: "weather" },
  ];

  it("matches the first rule and returns confidence 1.0", async () => {
    const router = new RuleRouter(rules);
    const result = await router.classify("hello there", makeCtx());
    expect(result.intent).toBe("greet");
    expect(result.confidence).toBe(1.0);
    expect(result.bestHandler?.name).toBe("greeter");
  });

  it("first match wins when multiple rules match", async () => {
    const overlapping: Rule[] = [
      { pattern: ".*", handler: "catch-all", intent: "any" },
      { pattern: "\\bhello\\b", handler: "greeter", intent: "greet" },
    ];
    const router = new RuleRouter(overlapping);
    const result = await router.classify("hello", makeCtx());
    expect(result.bestHandler?.name).toBe("catch-all");
  });

  it("falls back to default handler when no rules match", async () => {
    const router = new RuleRouter(rules, "fallback-handler");
    const result = await router.classify("something random", makeCtx());
    expect(result.intent).toBe("default");
    expect(result.confidence).toBe(0.5);
    expect(result.bestHandler?.name).toBe("fallback-handler");
  });

  it("returns unknown intent with no candidates when no match and no default", async () => {
    const router = new RuleRouter(rules);
    const result = await router.classify("something random", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
    expect(result.bestHandler).toBeNull();
    expect(result.handlers).toHaveLength(0);
  });

  it("returns empty result for empty string message", async () => {
    const router = new RuleRouter(rules, "fallback");
    const result = await router.classify("", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
  });

  it("returns empty result for whitespace-only message", async () => {
    const router = new RuleRouter(rules, "fallback");
    const result = await router.classify("   \t\n  ", makeCtx());
    expect(result.intent).toBe("unknown");
  });

  it("handles unicode in message", async () => {
    const unicodeRules: Rule[] = [
      { pattern: "\u4F60\u597D", handler: "chinese-greeter", intent: "greet_cn" },
    ];
    const router = new RuleRouter(unicodeRules);
    const result = await router.classify("\u4F60\u597D\u4E16\u754C", makeCtx());
    expect(result.intent).toBe("greet_cn");
  });

  it("is case-insensitive (patterns compiled with 'i' flag)", async () => {
    const router = new RuleRouter(rules);
    const result = await router.classify("HELLO world", makeCtx());
    expect(result.intent).toBe("greet");
  });

  it("throws TypeError for non-array rules", () => {
    expect(
      () => new RuleRouter("not-an-array" as unknown as Rule[]),
    ).toThrow(TypeError);
  });

  it("throws SyntaxError for invalid regex pattern", () => {
    const bad: Rule[] = [{ pattern: "[invalid", handler: "h", intent: "i" }];
    expect(() => new RuleRouter(bad)).toThrow();
  });

  it("works with empty rules list and a default handler", async () => {
    const router = new RuleRouter([], "default-h");
    const result = await router.classify("anything", makeCtx());
    expect(result.bestHandler?.name).toBe("default-h");
  });

  it("works with empty rules list and no default", async () => {
    const router = new RuleRouter([]);
    const result = await router.classify("anything", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.bestHandler).toBeNull();
  });
});
