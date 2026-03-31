import { describe, it, expect, vi } from "vitest";
import { HybridRouter } from "../src/router/hybrid.js";
import { EmbeddingRouter, cosineSimilarity } from "../src/router/embedding.js";
import type { EmbedFn } from "../src/router/embedding.js";
import type { RerankFn } from "../src/router/hybrid.js";
import type { HandlerCandidate } from "../src/router/index.js";
import { createHandlerCandidate } from "../src/router/index.js";
import { ExecContext } from "../src/context.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCtx(): ExecContext {
  return ExecContext.create({ userId: "test-user" });
}

/**
 * Build a deterministic embed function that maps known strings to fixed vectors.
 * Unknown strings get a zero vector.
 */
function buildEmbedFn(mapping: Record<string, number[]>): EmbedFn {
  const fn = vi.fn(async (text: string): Promise<number[]> => {
    return mapping[text] ?? [0, 0, 0];
  });
  return fn;
}

/**
 * Build a rerank function that returns candidates with boosted scores.
 */
function buildRerankFn(boost: number = 0.1): RerankFn {
  const fn = vi.fn(
    async (
      _message: string,
      candidates: readonly HandlerCandidate[],
    ): Promise<HandlerCandidate[]> => {
      return candidates.map((c) =>
        createHandlerCandidate(
          c.name,
          Math.min(1.0, c.score + boost),
          "reranked",
        ),
      );
    },
  );
  return fn;
}

// ---------------------------------------------------------------------------
// cosineSimilarity — pure function tests
// ---------------------------------------------------------------------------

describe("cosineSimilarity", () => {
  it("returns 1.0 for identical unit vectors", () => {
    const v = [1, 0, 0];
    expect(cosineSimilarity(v, v)).toBeCloseTo(1.0, 10);
  });

  it("returns 0.0 for orthogonal vectors", () => {
    expect(cosineSimilarity([1, 0], [0, 1])).toBeCloseTo(0.0, 10);
  });

  it("returns -1.0 for opposite vectors", () => {
    expect(cosineSimilarity([1, 0], [-1, 0])).toBeCloseTo(-1.0, 10);
  });

  it("returns 0.0 for empty vectors", () => {
    expect(cosineSimilarity([], [])).toBe(0.0);
  });

  it("returns 0.0 for mismatched dimensions", () => {
    expect(cosineSimilarity([1, 2], [1, 2, 3])).toBe(0.0);
  });

  it("returns 0.0 for zero vector on one side", () => {
    expect(cosineSimilarity([0, 0, 0], [1, 2, 3])).toBe(0.0);
  });

  it("returns 0.0 for both zero vectors", () => {
    expect(cosineSimilarity([0, 0], [0, 0])).toBe(0.0);
  });

  it("handles non-unit vectors correctly", () => {
    // [3,4] and [6,8] are parallel — similarity should be 1.0
    expect(cosineSimilarity([3, 4], [6, 8])).toBeCloseTo(1.0, 10);
  });
});

// ---------------------------------------------------------------------------
// EmbeddingRouter
// ---------------------------------------------------------------------------

describe("EmbeddingRouter", () => {
  it("returns semantic intent with ranked candidates above threshold", async () => {
    const embed = buildEmbedFn({
      "book a flight": [1, 0, 0],
      "flight booking": [0.95, 0.31, 0],
      "hotel reservation": [0, 1, 0],
    });

    const router = new EmbeddingRouter(embed, { threshold: 0.3 });
    await router.register("flights", "flight booking");
    await router.register("hotels", "hotel reservation");

    const result = await router.classify("book a flight", makeCtx());
    expect(result.intent).toBe("semantic");
    expect(result.confidence).toBeGreaterThan(0);
    expect(result.handlers.length).toBeGreaterThan(0);
    // flights should rank higher since its embedding is closer to the query
    expect(result.bestHandler?.name).toBe("flights");
  });

  it("returns unknown intent for empty message", async () => {
    const embed = buildEmbedFn({});
    const router = new EmbeddingRouter(embed);
    await router.register("h", "some description");

    const result = await router.classify("", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
    expect(result.handlers).toHaveLength(0);
  });

  it("returns unknown intent for whitespace-only message", async () => {
    const embed = buildEmbedFn({});
    const router = new EmbeddingRouter(embed);
    await router.register("h", "some description");

    const result = await router.classify("   \t\n  ", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
  });

  it("returns unknown intent when no handlers registered", async () => {
    const embed = buildEmbedFn({ hello: [1, 0, 0] });
    const router = new EmbeddingRouter(embed);

    const result = await router.classify("hello", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.handlers).toHaveLength(0);
  });

  it("returns unknown when all similarities fall below threshold", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      "unrelated handler": [0, 1, 0],
    });
    // High threshold — orthogonal vectors won't pass
    const router = new EmbeddingRouter(embed, { threshold: 0.9 });
    await router.register("unrelated", "unrelated handler");

    const result = await router.classify("query", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
  });

  it("respects topK limit", async () => {
    const embed = buildEmbedFn({
      query: [1, 1, 0],
      "desc-a": [1, 0.9, 0],
      "desc-b": [0.9, 1, 0],
      "desc-c": [0.8, 0.8, 0],
    });
    const router = new EmbeddingRouter(embed, { threshold: 0.0, topK: 2 });
    await router.register("a", "desc-a");
    await router.register("b", "desc-b");
    await router.register("c", "desc-c");

    const result = await router.classify("query", makeCtx());
    expect(result.handlers.length).toBeLessThanOrEqual(2);
  });

  it("throws RangeError for threshold below 0", () => {
    const embed = buildEmbedFn({});
    expect(() => new EmbeddingRouter(embed, { threshold: -0.1 })).toThrow(
      RangeError,
    );
  });

  it("throws RangeError for threshold above 1", () => {
    const embed = buildEmbedFn({});
    expect(() => new EmbeddingRouter(embed, { threshold: 1.1 })).toThrow(
      RangeError,
    );
  });

  it("throws RangeError for topK below 1", () => {
    const embed = buildEmbedFn({});
    expect(() => new EmbeddingRouter(embed, { topK: 0 })).toThrow(RangeError);
  });

  it("accepts boundary threshold values 0.0 and 1.0", () => {
    const embed = buildEmbedFn({});
    expect(() => new EmbeddingRouter(embed, { threshold: 0.0 })).not.toThrow();
    expect(() => new EmbeddingRouter(embed, { threshold: 1.0 })).not.toThrow();
  });

  it("throws on empty handler name", async () => {
    const embed = buildEmbedFn({});
    const router = new EmbeddingRouter(embed);
    await expect(router.register("", "description")).rejects.toThrow(
      "Handler name must not be empty",
    );
  });

  it("throws on blank handler description", async () => {
    const embed = buildEmbedFn({});
    const router = new EmbeddingRouter(embed);
    await expect(router.register("name", "   ")).rejects.toThrow(
      "Handler description must not be blank",
    );
  });

  it("includes rawScores for each candidate", async () => {
    const embed = buildEmbedFn({
      msg: [1, 0, 0],
      desc: [1, 0, 0],
    });
    const router = new EmbeddingRouter(embed, { threshold: 0.0 });
    await router.register("exact", "desc");

    const result = await router.classify("msg", makeCtx());
    if (result.handlers.length > 0) {
      expect(result.rawScores["exact"]).toBeDefined();
    }
  });

  it("handles single handler that matches", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      "the handler": [1, 0, 0],
    });
    const router = new EmbeddingRouter(embed, { threshold: 0.5 });
    await router.register("solo", "the handler");

    const result = await router.classify("query", makeCtx());
    expect(result.handlers).toHaveLength(1);
    expect(result.bestHandler?.name).toBe("solo");
    expect(result.confidence).toBeCloseTo(1.0);
  });

  it("all handlers with identical embeddings get identical scores", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      "desc-a": [0.5, 0.5, 0],
      "desc-b": [0.5, 0.5, 0],
      "desc-c": [0.5, 0.5, 0],
    });
    const router = new EmbeddingRouter(embed, { threshold: 0.0 });
    await router.register("a", "desc-a");
    await router.register("b", "desc-b");
    await router.register("c", "desc-c");

    const result = await router.classify("query", makeCtx());
    const scores = result.handlers.map((h) => h.score);
    expect(new Set(scores).size).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// HybridRouter
// ---------------------------------------------------------------------------

describe("HybridRouter", () => {
  it("performs two-stage routing: embedding pre-filter then rerank", async () => {
    const embed = buildEmbedFn({
      "send an email": [1, 0, 0],
      "email sender": [0.9, 0.43, 0],
      "calendar app": [0, 1, 0],
    });
    const rerank = buildRerankFn(0.05);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.2,
    });
    await router.register("email", "email sender");
    await router.register("calendar", "calendar app");

    const result = await router.classify("send an email", makeCtx());
    expect(result.intent).toBe("hybrid");
    expect(result.confidence).toBeGreaterThan(0);
    expect(rerank).toHaveBeenCalled();
  });

  it("skips rerank when no handlers are registered", async () => {
    const embed = buildEmbedFn({ msg: [1, 0, 0] });
    const rerank = buildRerankFn();

    const router = new HybridRouter(embed, rerank);
    const result = await router.classify("msg", makeCtx());

    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
    expect(rerank).not.toHaveBeenCalled();
  });

  it("returns unknown for empty message", async () => {
    const embed = buildEmbedFn({});
    const rerank = buildRerankFn();

    const router = new HybridRouter(embed, rerank);
    await router.register("h", "handler description");

    const result = await router.classify("", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(rerank).not.toHaveBeenCalled();
  });

  it("returns unknown for whitespace-only message", async () => {
    const embed = buildEmbedFn({});
    const rerank = buildRerankFn();

    const router = new HybridRouter(embed, rerank);
    await router.register("h", "handler description");

    const result = await router.classify("  \n\t  ", makeCtx());
    expect(result.intent).toBe("unknown");
  });

  it("falls back to embedding results when reranker returns empty", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      desc: [0.9, 0.43, 0],
    });
    const rerank = vi.fn(async () => []);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.2,
    });
    await router.register("handler", "desc");

    const result = await router.classify("query", makeCtx());
    expect(result.intent).toBe("hybrid");
    expect(result.handlers.length).toBeGreaterThan(0);
    expect(rerank).toHaveBeenCalled();
  });

  it("returns unknown when all embeddings fall below threshold", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      desc: [0, 1, 0],
    });
    const rerank = buildRerankFn();

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.99,
    });
    await router.register("h", "desc");

    const result = await router.classify("query", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
    expect(rerank).not.toHaveBeenCalled();
  });

  it("respects finalK limit", async () => {
    const embed = buildEmbedFn({
      query: [1, 1, 0],
      "d1": [1, 0.9, 0],
      "d2": [0.9, 1, 0],
      "d3": [0.8, 0.8, 0],
    });
    const rerank = buildRerankFn(0);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.0,
      finalK: 1,
    });
    await router.register("a", "d1");
    await router.register("b", "d2");
    await router.register("c", "d3");

    const result = await router.classify("query", makeCtx());
    expect(result.handlers.length).toBe(1);
  });

  it("respects preFilterK limit — only top-k go to reranker", async () => {
    const embed = buildEmbedFn({
      query: [1, 1, 0],
      "d1": [1, 0.9, 0],
      "d2": [0.9, 1, 0],
      "d3": [0.8, 0.8, 0],
    });
    const rerank = buildRerankFn(0);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.0,
      preFilterK: 2,
    });
    await router.register("a", "d1");
    await router.register("b", "d2");
    await router.register("c", "d3");

    await router.classify("query", makeCtx());
    // Reranker should have received at most 2 candidates
    const rerankCandidates = (rerank as ReturnType<typeof vi.fn>).mock
      .calls[0][1] as HandlerCandidate[];
    expect(rerankCandidates.length).toBeLessThanOrEqual(2);
  });

  it("throws on empty handler name", async () => {
    const embed = buildEmbedFn({});
    const rerank = buildRerankFn();
    const router = new HybridRouter(embed, rerank);

    await expect(router.register("", "desc")).rejects.toThrow(
      "Handler name must not be empty",
    );
  });

  it("throws on empty handler description", async () => {
    const embed = buildEmbedFn({});
    const rerank = buildRerankFn();
    const router = new HybridRouter(embed, rerank);

    await expect(router.register("name", "")).rejects.toThrow(
      "Handler description must not be empty",
    );
  });

  it("throws on whitespace-only handler description", async () => {
    const embed = buildEmbedFn({});
    const rerank = buildRerankFn();
    const router = new HybridRouter(embed, rerank);

    await expect(router.register("name", "   \t\n")).rejects.toThrow(
      "Handler description must not be empty",
    );
  });

  it("throws on duplicate handler name", async () => {
    const embed = buildEmbedFn({
      "desc-1": [1, 0, 0],
      "desc-2": [0, 1, 0],
    });
    const rerank = buildRerankFn();
    const router = new HybridRouter(embed, rerank);

    await router.register("unique", "desc-1");
    await expect(router.register("unique", "desc-2")).rejects.toThrow(
      "already registered",
    );
  });

  it("all handlers with identical scores — reranker still returns them", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      "d1": [1, 0, 0],
      "d2": [1, 0, 0],
      "d3": [1, 0, 0],
    });
    const rerank = buildRerankFn(0);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.0,
    });
    await router.register("a", "d1");
    await router.register("b", "d2");
    await router.register("c", "d3");

    const result = await router.classify("query", makeCtx());
    expect(result.handlers).toHaveLength(3);
    const scores = result.handlers.map((h) => h.score);
    expect(new Set(scores).size).toBe(1);
  });

  it("single handler — goes through both stages", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      desc: [1, 0, 0],
    });
    const rerank = buildRerankFn(0);

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.0,
    });
    await router.register("solo", "desc");

    const result = await router.classify("query", makeCtx());
    expect(result.handlers).toHaveLength(1);
    expect(result.bestHandler?.name).toBe("solo");
    expect(rerank).toHaveBeenCalledTimes(1);
  });

  it("reranker can reorder candidates", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      "d-low": [0.8, 0.6, 0],
      "d-high": [0.9, 0.43, 0],
    });
    // Reranker boosts "low" above "high"
    const rerank = vi.fn(
      async (
        _msg: string,
        candidates: readonly HandlerCandidate[],
      ): Promise<HandlerCandidate[]> => {
        return candidates.map((c) =>
          createHandlerCandidate(
            c.name,
            c.name === "low" ? 0.99 : 0.1,
            "reranked",
          ),
        );
      },
    );

    const router = new HybridRouter(embed, rerank, {
      embeddingThreshold: 0.0,
    });
    await router.register("low", "d-low");
    await router.register("high", "d-high");

    const result = await router.classify("query", makeCtx());
    expect(result.bestHandler?.name).toBe("low");
  });

  it("uses default options when none provided", async () => {
    const embed = buildEmbedFn({
      query: [1, 0, 0],
      desc: [0.9, 0.43, 0],
    });
    const rerank = buildRerankFn(0);

    const router = new HybridRouter(embed, rerank);
    await router.register("h", "desc");

    const result = await router.classify("query", makeCtx());
    // Should work with defaults (threshold 0.2, preFilterK 10, finalK 5)
    expect(result.intent).toBe("hybrid");
  });
});
