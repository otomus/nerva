/**
 * Tests that pure JS functions work without the native module,
 * and (when available) that native results match pure JS.
 *
 * @module tests/native-fallback
 */

import { describe, it, expect } from "vitest";
import { cosineSimilarity } from "../src/router/embedding.js";

// ---------------------------------------------------------------------------
// Types for the native module interface
// ---------------------------------------------------------------------------

interface NativeModule {
  cosine_similarity(a: number[], b: number[]): number;
  count_tokens(text: string): number;
  truncate_to_tokens(text: string, max_tokens: number): string;
  validate_schema(instance: string, schema: string): string[];
}

/** Attempt to load the native module — null if not compiled. */
function loadNative(): NativeModule | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("@nerva/core") as NativeModule;
  } catch {
    return null;
  }
}

const nativeModule = loadNative();

// ---------------------------------------------------------------------------
// Pure JS always works
// ---------------------------------------------------------------------------

describe("pure JS cosine similarity (no native)", () => {
  it("identical vectors produce similarity ~1.0", () => {
    const v = [1, 2, 3, 4];
    const sim = cosineSimilarity(v, v);
    expect(sim).toBeCloseTo(1.0, 5);
  });

  it("orthogonal vectors produce similarity ~0.0", () => {
    const a = [1, 0];
    const b = [0, 1];
    const sim = cosineSimilarity(a, b);
    expect(Math.abs(sim)).toBeLessThan(1e-6);
  });

  it("opposite vectors produce similarity ~-1.0", () => {
    const a = [1, 2, 3];
    const b = [-1, -2, -3];
    const sim = cosineSimilarity(a, b);
    expect(sim).toBeCloseTo(-1.0, 5);
  });

  it("empty vectors return 0.0", () => {
    expect(cosineSimilarity([], [])).toBe(0.0);
  });

  it("mismatched lengths return 0.0", () => {
    expect(cosineSimilarity([1, 2], [1])).toBe(0.0);
  });

  it("zero vector returns 0.0", () => {
    const a = [0, 0, 0];
    const b = [1, 2, 3];
    expect(cosineSimilarity(a, b)).toBe(0.0);
  });
});

// ---------------------------------------------------------------------------
// Native module tests (skipped when not available)
// ---------------------------------------------------------------------------

describe("native module availability", () => {
  it("reports whether native module is loaded", () => {
    // This test always passes — it documents the state.
    if (nativeModule) {
      // eslint-disable-next-line no-console
      console.log("  native module IS available");
    } else {
      // eslint-disable-next-line no-console
      console.log("  native module is NOT available (pure JS fallback active)");
    }
    expect(true).toBe(true);
  });
});

const describeNative = nativeModule ? describe : describe.skip;

describeNative("native vs pure JS consistency", () => {
  it("cosine similarity matches for identical vectors", () => {
    const v = [1, 2, 3, 4];
    const jsResult = cosineSimilarity(v, v);
    const nativeResult = nativeModule!.cosine_similarity(v, v);
    expect(Math.abs(jsResult - nativeResult)).toBeLessThan(1e-4);
  });

  it("cosine similarity matches for orthogonal vectors", () => {
    const a = [1, 0];
    const b = [0, 1];
    const jsResult = cosineSimilarity(a, b);
    const nativeResult = nativeModule!.cosine_similarity(a, b);
    expect(Math.abs(jsResult - nativeResult)).toBeLessThan(1e-4);
  });

  it("cosine similarity matches for random-ish vectors", () => {
    const a = [0.5, -0.3, 0.9, 0.1, -0.7];
    const b = [0.2, 0.8, -0.4, 0.6, 0.3];
    const jsResult = cosineSimilarity(a, b);
    const nativeResult = nativeModule!.cosine_similarity(a, b);
    expect(Math.abs(jsResult - nativeResult)).toBeLessThan(1e-4);
  });

  it("count_tokens returns a number", () => {
    const result = nativeModule!.count_tokens("hello world");
    expect(typeof result).toBe("number");
    expect(result).toBeGreaterThan(0);
  });

  it("truncate_to_tokens returns a string", () => {
    const result = nativeModule!.truncate_to_tokens("hello world foo bar", 2);
    expect(typeof result).toBe("string");
    expect(result.length).toBeLessThanOrEqual("hello world foo bar".length);
  });

  it("validate_schema returns empty array for valid input", () => {
    const instance = JSON.stringify({ name: "alice" });
    const schema = JSON.stringify({
      type: "object",
      required: ["name"],
      properties: { name: { type: "string" } },
    });
    const errors = nativeModule!.validate_schema(instance, schema);
    expect(errors).toEqual([]);
  });

  it("validate_schema returns errors for invalid input", () => {
    const instance = JSON.stringify({});
    const schema = JSON.stringify({ type: "object", required: ["name"] });
    const errors = nativeModule!.validate_schema(instance, schema);
    expect(errors.length).toBeGreaterThan(0);
  });
});
