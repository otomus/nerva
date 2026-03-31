/**
 * Tests for the streaming system: runtime, tools, responder, and orchestrator integration.
 *
 * Covers N-660 through N-664.
 */

import { describe, it, expect } from "vitest";
import { ExecContext, InMemoryStreamSink } from "../src/context.js";
import {
  type AgentInput,
  type AgentResult,
  type AgentRuntime,
  AgentStatus,
  createAgentResult,
} from "../src/runtime/index.js";
import {
  StreamingRuntime,
  buildChunk,
  serializeChunk,
  type StreamChunk,
} from "../src/runtime/streaming.js";
import {
  type ToolManager,
  type ToolSpec,
  type ToolResult,
  ToolStatus,
  createToolResult,
  createToolSpec,
} from "../src/tools/index.js";
import {
  StreamingToolManager,
  TOOL_START_TYPE,
  TOOL_END_TYPE,
  TOOL_ERROR_TYPE,
} from "../src/tools/streaming.js";
import {
  StreamingResponder,
  formatSse,
  formatWebsocket,
  formatRaw,
  formatForChannel,
} from "../src/responder/streaming.js";
import { RuleRouter } from "../src/router/rule.js";
import { InProcessRuntime } from "../src/runtime/inprocess.js";
import { Orchestrator, type Responder, type Response, type Channel, API_CHANNEL } from "../src/orchestrator.js";

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

function makeFakeRuntime(
  output: string = "hello world",
  status: (typeof AgentStatus)[keyof typeof AgentStatus] = AgentStatus.SUCCESS,
): AgentRuntime {
  const error = status === AgentStatus.SUCCESS ? null : "boom";
  return {
    invoke: async (handler) =>
      createAgentResult(status, { output, handler, error }),
    invokeChain: async (handlers) =>
      createAgentResult(status, {
        output,
        handler: handlers[handlers.length - 1] ?? "",
        error,
      }),
    delegate: async (handler) =>
      createAgentResult(status, { output, handler, error }),
  };
}

function makeFakeToolManager(options?: {
  result?: ToolResult;
  shouldThrow?: boolean;
}): ToolManager {
  const result = options?.result ?? createToolResult(ToolStatus.SUCCESS, { output: "tool output" });
  return {
    discover: async () => [createToolSpec("test_tool", "A test tool")],
    call: async () => {
      if (options?.shouldThrow) throw new Error("tool exploded");
      return result;
    },
  };
}

function makeResponder(): Responder {
  return {
    format: async (result, channel): Promise<Response> => ({
      text: result.output,
      channel,
      media: [],
      metadata: {},
    }),
  };
}

function makeRouter() {
  return new RuleRouter(
    [{ pattern: ".*", handler: "echo", intent: "echo" }],
    null,
  );
}

// ---------------------------------------------------------------------------
// N-660: Runtime streaming
// ---------------------------------------------------------------------------

describe("StreamingRuntime", () => {
  it("pushes a COMPLETE chunk on successful invoke", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("hi"));
    const result = await runtime.invoke("greet", { message: "hey", args: {}, tools: [], history: [] }, ctx);

    expect(result.status).toBe(AgentStatus.SUCCESS);
    expect(result.output).toBe("hi");
    expect(sink.chunks).toHaveLength(1);

    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("complete");
    expect(parsed.content).toBe("hi");
    expect(parsed.timestamp).toBeGreaterThan(0);
  });

  it("pushes an ERROR chunk on failed invoke", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("", AgentStatus.ERROR));
    const result = await runtime.invoke("bad", { message: "x", args: {}, tools: [], history: [] }, ctx);

    expect(result.status).toBe(AgentStatus.ERROR);
    expect(sink.chunks).toHaveLength(1);

    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("error");
  });

  it("does not push when ctx.stream is null", async () => {
    const ctx = ExecContext.create();
    expect(ctx.stream).toBeNull();

    const runtime = new StreamingRuntime(makeFakeRuntime("hello"));
    const result = await runtime.invoke("greet", { message: "hi", args: {}, tools: [], history: [] }, ctx);

    expect(result.status).toBe(AgentStatus.SUCCESS);
  });

  it("pushes chunk on invokeChain", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("chained"));
    const result = await runtime.invokeChain(["a", "b"], { message: "x", args: {}, tools: [], history: [] }, ctx);

    expect(result.output).toBe("chained");
    expect(sink.chunks).toHaveLength(1);
    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("complete");
  });

  it("pushes chunk on delegate", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("delegated"));
    const result = await runtime.delegate("sub", { message: "x", args: {}, tools: [], history: [] }, ctx);

    expect(result.output).toBe("delegated");
    expect(sink.chunks).toHaveLength(1);
  });

  it("produces a single chunk for string-returning handler", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("full response"));
    await runtime.invoke("simple", { message: "q", args: {}, tools: [], history: [] }, ctx);

    expect(sink.chunks).toHaveLength(1);
    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("complete");
    expect(parsed.content).toBe("full response");
  });
});

describe("StreamChunk helpers", () => {
  it("buildChunk creates a valid chunk", () => {
    const chunk = buildChunk("token", "hello");
    expect(chunk.type).toBe("token");
    expect(chunk.content).toBe("hello");
    expect(chunk.timestamp).toBeGreaterThan(0);
  });

  it("serializeChunk produces valid JSON", () => {
    const chunk = buildChunk("complete", "done");
    const serialized = serializeChunk(chunk);
    const parsed = JSON.parse(serialized);
    expect(parsed.type).toBe("complete");
    expect(parsed.content).toBe("done");
  });

  it("buildChunk handles empty content", () => {
    const chunk = buildChunk("token", "");
    expect(chunk.content).toBe("");
  });
});

// ---------------------------------------------------------------------------
// N-661: Tool streaming
// ---------------------------------------------------------------------------

describe("StreamingToolManager", () => {
  it("emits start and end events on success", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const mgr = new StreamingToolManager(makeFakeToolManager());
    const result = await mgr.call("calc", { x: 1 }, ctx);

    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(sink.chunks).toHaveLength(2);

    const start = JSON.parse(sink.chunks[0]!);
    expect(start.type).toBe(TOOL_START_TYPE);
    expect(start.tool).toBe("calc");

    const end = JSON.parse(sink.chunks[1]!);
    expect(end.type).toBe(TOOL_END_TYPE);
    expect(end.tool).toBe("calc");
    expect(end.duration_ms).toBeGreaterThanOrEqual(0);
  });

  it("emits start and error events on failure", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const mgr = new StreamingToolManager(
      makeFakeToolManager({ result: createToolResult(ToolStatus.ERROR, { error: "bad input" }) }),
    );
    const result = await mgr.call("broken", {}, ctx);

    expect(result.status).toBe(ToolStatus.ERROR);
    expect(sink.chunks).toHaveLength(2);

    const errorEvt = JSON.parse(sink.chunks[1]!);
    expect(errorEvt.type).toBe(TOOL_ERROR_TYPE);
    expect(errorEvt.tool).toBe("broken");
    expect(errorEvt.error).toContain("bad input");
  });

  it("emits error event and re-throws on exception", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const mgr = new StreamingToolManager(makeFakeToolManager({ shouldThrow: true }));

    await expect(mgr.call("exploding", {}, ctx)).rejects.toThrow("tool exploded");

    expect(sink.chunks).toHaveLength(2);
    const errorEvt = JSON.parse(sink.chunks[1]!);
    expect(errorEvt.type).toBe(TOOL_ERROR_TYPE);
    expect(errorEvt.error).toContain("tool exploded");
  });

  it("discover delegates directly without events", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const mgr = new StreamingToolManager(makeFakeToolManager());
    const specs = await mgr.discover(ctx);

    expect(specs).toHaveLength(1);
    expect(specs[0]!.name).toBe("test_tool");
    expect(sink.chunks).toHaveLength(0);
  });

  it("no events when ctx.stream is null", async () => {
    const ctx = ExecContext.create();
    expect(ctx.stream).toBeNull();

    const mgr = new StreamingToolManager(makeFakeToolManager());
    const result = await mgr.call("calc", {}, ctx);

    expect(result.status).toBe(ToolStatus.SUCCESS);
  });
});

// ---------------------------------------------------------------------------
// N-662: Responder streaming
// ---------------------------------------------------------------------------

describe("StreamingResponder", () => {
  it("formats SSE correctly", () => {
    const responder = new StreamingResponder("sse");
    const formatted = responder.formatChunk("hello");

    expect(formatted).toMatch(/^data: /);
    expect(formatted).toMatch(/\n\n$/);
    const payload = JSON.parse(formatted.slice(6, -2));
    expect(payload.content).toBe("hello");
  });

  it("formats WebSocket correctly", () => {
    const responder = new StreamingResponder("websocket");
    const formatted = responder.formatChunk("world");

    const parsed = JSON.parse(formatted);
    expect(parsed.content).toBe("world");
  });

  it("formats raw correctly", () => {
    const responder = new StreamingResponder("raw");
    expect(responder.formatChunk("plain")).toBe("plain");
  });

  it("defaults to raw format", () => {
    const responder = new StreamingResponder();
    expect(responder.format).toBe("raw");
  });

  it("exposes format property", () => {
    const responder = new StreamingResponder("sse");
    expect(responder.format).toBe("sse");
  });

  it("handles special characters in SSE", () => {
    const formatted = formatSse('line1\nline2\t"quoted"');
    const payload = JSON.parse(formatted.slice(6, -2));
    expect(payload.content).toBe('line1\nline2\t"quoted"');
  });

  it("handles unicode in WebSocket", () => {
    const formatted = formatWebsocket("emoji: \u2764");
    const parsed = JSON.parse(formatted);
    expect(parsed.content).toBe("emoji: \u2764");
  });

  it("handles empty string in raw", () => {
    expect(formatRaw("")).toBe("");
  });

  it("handles empty string in SSE", () => {
    const formatted = formatSse("");
    const payload = JSON.parse(formatted.slice(6, -2));
    expect(payload.content).toBe("");
  });

  it("throws on unknown format", () => {
    expect(() => formatForChannel("x", "invalid" as any)).toThrow("Unknown stream format");
  });
});

// ---------------------------------------------------------------------------
// N-663: Orchestrator stream() end-to-end
// ---------------------------------------------------------------------------

describe("Orchestrator stream() integration", () => {
  it("yields chunks from the pipeline", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("echo", async (input, ctx) => {
      if (ctx.stream !== null) {
        await ctx.stream.push("tok1");
        await ctx.stream.push("tok2");
      }
      return "tok1tok2";
    });

    const router = makeRouter();
    const responder = makeResponder();

    const orch = new Orchestrator({ router, runtime, responder });
    const chunks: string[] = [];

    for await (const chunk of orch.stream("hello")) {
      chunks.push(chunk);
    }

    expect(chunks).toContain("tok1");
    expect(chunks).toContain("tok2");
  });

  it("yields chunks from async generator handler", async () => {
    const runtime = new InProcessRuntime();
    runtime.registerStreaming("gen", async function* (_input, _ctx) {
      yield "chunk_a";
      yield "chunk_b";
      yield "chunk_c";
    });

    const router = new RuleRouter(
      [{ pattern: ".*", handler: "gen", intent: "gen" }],
      null,
    );
    const responder = makeResponder();

    const orch = new Orchestrator({ router, runtime, responder });
    const chunks: string[] = [];

    for await (const chunk of orch.stream("test")) {
      chunks.push(chunk);
    }

    expect(chunks).toContain("chunk_a");
    expect(chunks).toContain("chunk_b");
    expect(chunks).toContain("chunk_c");
  });
});

// ---------------------------------------------------------------------------
// N-664: Edge cases
// ---------------------------------------------------------------------------

describe("Streaming edge cases", () => {
  it("empty output produces a COMPLETE chunk with empty content", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime(""));
    await runtime.invoke("empty", { message: "", args: {}, tools: [], history: [] }, ctx);

    expect(sink.chunks).toHaveLength(1);
    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("complete");
    expect(parsed.content).toBe("");
  });

  it("error status produces ERROR chunk", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const runtime = new StreamingRuntime(makeFakeRuntime("", AgentStatus.ERROR));
    const result = await runtime.invoke("fail", { message: "x", args: {}, tools: [], history: [] }, ctx);

    expect(result.status).toBe(AgentStatus.ERROR);
    const parsed = JSON.parse(sink.chunks[0]!);
    expect(parsed.type).toBe("error");
  });

  it("cancelled context still receives chunks", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });
    ctx.cancel();

    const runtime = new StreamingRuntime(makeFakeRuntime("still works"));
    const result = await runtime.invoke("x", { message: "y", args: {}, tools: [], history: [] }, ctx);

    expect(result.output).toBe("still works");
    expect(sink.chunks).toHaveLength(1);
  });

  it("tool with TIMEOUT status emits error event", async () => {
    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ stream: sink });

    const mgr = new StreamingToolManager(
      makeFakeToolManager({ result: createToolResult(ToolStatus.TIMEOUT, { error: "timed out" }) }),
    );
    const result = await mgr.call("slow_tool", {}, ctx);

    expect(result.status).toBe(ToolStatus.TIMEOUT);
    const errorEvt = JSON.parse(sink.chunks[1]!);
    expect(errorEvt.type).toBe(TOOL_ERROR_TYPE);
    expect(errorEvt.error).toContain("timed out");
  });
});
