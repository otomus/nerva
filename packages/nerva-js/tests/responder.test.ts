import { describe, it, expect } from "vitest";
import { PassthroughResponder } from "../src/responder/passthrough.js";
import { createChannel, createResponse, API_CHANNEL } from "../src/responder/index.js";
import { ExecContext } from "../src/context.js";
import type { AgentResult, Channel } from "../src/responder/index.js";

function makeCtx(): ExecContext {
  return ExecContext.create();
}

function makeResult(output: string): AgentResult {
  return { output, status: "success", handler: "test" };
}

// ---------------------------------------------------------------------------
// PassthroughResponder
// ---------------------------------------------------------------------------

describe("PassthroughResponder", () => {
  it("passes output through unchanged with maxLength=0", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 0 });
    const result = await responder.format(makeResult("hello world"), channel, makeCtx());
    expect(result.text).toBe("hello world");
    expect(result.channel).toBe(channel);
  });

  it("truncates output to channel maxLength", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 5 });
    const result = await responder.format(makeResult("hello world"), channel, makeCtx());
    expect(result.text).toBe("hello");
  });

  it("does not truncate when output is shorter than maxLength", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 100 });
    const result = await responder.format(makeResult("hi"), channel, makeCtx());
    expect(result.text).toBe("hi");
  });

  it("handles empty output", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 0 });
    const result = await responder.format(makeResult(""), channel, makeCtx());
    expect(result.text).toBe("");
  });

  it("handles maxLength=1", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 1 });
    const result = await responder.format(makeResult("hello"), channel, makeCtx());
    expect(result.text).toBe("h");
  });

  it("handles unicode truncation", async () => {
    const responder = new PassthroughResponder();
    const channel = createChannel("api", { maxLength: 2 });
    const result = await responder.format(makeResult("\u{1F600}\u{1F601}\u{1F602}"), channel, makeCtx());
    // JS slice works on code units, so behavior depends on surrogate pairs
    expect(result.text.length).toBeLessThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// createChannel / createResponse
// ---------------------------------------------------------------------------

describe("createChannel", () => {
  it("creates channel with defaults", () => {
    const ch = createChannel("test");
    expect(ch.name).toBe("test");
    expect(ch.supportsMarkdown).toBe(true);
    expect(ch.supportsMedia).toBe(false);
    expect(ch.maxLength).toBe(0);
  });

  it("freezes the channel", () => {
    const ch = createChannel("test");
    expect(Object.isFrozen(ch)).toBe(true);
  });

  it("respects overrides", () => {
    const ch = createChannel("slack", { supportsMedia: true, maxLength: 4000 });
    expect(ch.supportsMedia).toBe(true);
    expect(ch.maxLength).toBe(4000);
  });
});

describe("createResponse", () => {
  it("creates response with defaults", () => {
    const ch = createChannel("api");
    const r = createResponse("text", ch);
    expect(r.text).toBe("text");
    expect(r.channel).toBe(ch);
    expect(r.media).toEqual([]);
    expect(r.metadata).toEqual({});
  });

  it("accepts media and metadata overrides", () => {
    const ch = createChannel("api");
    const r = createResponse("text", ch, {
      media: ["img.png"],
      metadata: { key: "val" },
    });
    expect(r.media).toEqual(["img.png"]);
    expect(r.metadata).toEqual({ key: "val" });
  });
});

describe("API_CHANNEL constant", () => {
  it("has expected default values", () => {
    expect(API_CHANNEL.name).toBe("api");
    expect(API_CHANNEL.supportsMarkdown).toBe(false);
    expect(API_CHANNEL.supportsMedia).toBe(true);
    expect(API_CHANNEL.maxLength).toBe(0);
  });
});
