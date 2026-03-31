import { describe, it, expect, vi } from "vitest";
import { ExecContext, InMemoryStreamSink, createPermissions } from "../src/context.js";
import { LLMRouter } from "../src/router/llm.js";
import type { LLMFn } from "../src/router/llm.js";
import { InProcessRuntime } from "../src/runtime/inprocess.js";
import type { HandlerFn, StreamingHandlerFn } from "../src/runtime/inprocess.js";
import { ContainerRuntime } from "../src/runtime/container.js";
import { CompositeToolManager } from "../src/tools/composite.js";
import { FunctionToolManager } from "../src/tools/function.js";
import {
  ToolStatus,
  type ToolSpec,
  type ToolResult,
  type ToolManager,
  createToolSpec,
  createToolResult,
} from "../src/tools/index.js";
import { ToneResponder } from "../src/responder/tone.js";
import type { ToneRewriteFn } from "../src/responder/tone.js";
import {
  MultimodalResponder,
  BlockType,
  createTextBlock,
  createImageBlock,
  createCardBlock,
  createButtonBlock,
} from "../src/responder/multimodal.js";
import { createChannel } from "../src/responder/index.js";
import type { AgentResult as ResponderAgentResult } from "../src/responder/index.js";
import { createAgentInput } from "../src/runtime/index.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCtx(): ExecContext {
  return ExecContext.create({ userId: "test-user" });
}

function makeResult(output: string, status = "success"): ResponderAgentResult {
  return { output, status, handler: "test" };
}

// ---------------------------------------------------------------------------
// N-620: LLMRouter
// ---------------------------------------------------------------------------

describe("LLMRouter", () => {
  it("classifies intent by asking the LLM and returns correct handler", async () => {
    const llm: LLMFn = vi.fn(async (_sys: string, _user: string) => {
      return '{"handler": "flights", "confidence": 0.95}';
    });
    const router = new LLMRouter(llm);
    router.register("flights", "Book airline flights");
    router.register("hotels", "Reserve hotel rooms");

    const result = await router.classify("I need a flight to Paris", makeCtx());
    expect(result.intent).toBe("llm");
    expect(result.confidence).toBeCloseTo(0.95);
    expect(result.bestHandler?.name).toBe("flights");
    expect(llm).toHaveBeenCalledTimes(1);
  });

  it("includes handler catalog in the system prompt", async () => {
    let capturedSystem = "";
    const llm: LLMFn = vi.fn(async (sys: string, _user: string) => {
      capturedSystem = sys;
      return '{"handler": "search", "confidence": 0.8}';
    });
    const router = new LLMRouter(llm);
    router.register("search", "Search the web");
    router.register("calc", "Perform calculations");

    await router.classify("find something", makeCtx());
    expect(capturedSystem).toContain("search: Search the web");
    expect(capturedSystem).toContain("calc: Perform calculations");
  });

  it("returns unknown for empty message", async () => {
    const llm: LLMFn = vi.fn(async () => "");
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
    expect(llm).not.toHaveBeenCalled();
  });

  it("returns unknown for whitespace-only message", async () => {
    const llm: LLMFn = vi.fn(async () => "");
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("   \t\n  ", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(llm).not.toHaveBeenCalled();
  });

  it("returns unknown when no handlers are registered", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"x","confidence":1}');
    const router = new LLMRouter(llm);

    const result = await router.classify("hello", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(llm).not.toHaveBeenCalled();
  });

  it("falls back to unknown on invalid JSON from LLM", async () => {
    const llm: LLMFn = vi.fn(async () => "I think you should use the flights handler");
    const router = new LLMRouter(llm);
    router.register("flights", "Book flights");

    const result = await router.classify("book a flight", makeCtx());
    expect(result.intent).toBe("unknown");
    expect(result.confidence).toBe(0.0);
  });

  it("falls back to unknown when LLM returns unknown handler name", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"nonexistent","confidence":0.9}');
    const router = new LLMRouter(llm);
    router.register("flights", "Book flights");

    const result = await router.classify("something", makeCtx());
    expect(result.intent).toBe("unknown");
  });

  it("extracts JSON from noisy LLM output", async () => {
    const llm: LLMFn = vi.fn(async () => {
      return 'Here is my answer:\n{"handler": "flights", "confidence": 0.85}\nHope this helps!';
    });
    const router = new LLMRouter(llm);
    router.register("flights", "Book flights");

    const result = await router.classify("book a flight", makeCtx());
    expect(result.intent).toBe("llm");
    expect(result.bestHandler?.name).toBe("flights");
  });

  it("clamps confidence above 1.0 to 1.0", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"h","confidence":5.0}');
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("test", makeCtx());
    expect(result.confidence).toBe(1.0);
  });

  it("clamps confidence below 0.0 to 0.0", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"h","confidence":-1.0}');
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("test", makeCtx());
    expect(result.confidence).toBe(0.0);
  });

  it("uses default confidence when LLM omits it", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"h"}');
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("test", makeCtx());
    expect(result.intent).toBe("llm");
    expect(result.confidence).toBe(0.5);
  });

  it("throws on empty handler name", () => {
    const router = new LLMRouter(vi.fn());
    expect(() => router.register("", "desc")).toThrow("Handler name must not be empty");
  });

  it("throws on empty handler description", () => {
    const router = new LLMRouter(vi.fn());
    expect(() => router.register("name", "")).toThrow("Handler description must not be empty");
  });

  it("throws on whitespace-only handler description", () => {
    const router = new LLMRouter(vi.fn());
    expect(() => router.register("name", "   \t")).toThrow("Handler description must not be empty");
  });

  it("throws on duplicate handler name", () => {
    const router = new LLMRouter(vi.fn());
    router.register("h", "handler one");
    expect(() => router.register("h", "handler two")).toThrow("already registered");
  });

  it("handles LLM returning empty string", async () => {
    const llm: LLMFn = vi.fn(async () => "");
    const router = new LLMRouter(llm);
    router.register("h", "handler");

    const result = await router.classify("test", makeCtx());
    expect(result.intent).toBe("unknown");
  });

  it("handles LLM returning an array instead of object", async () => {
    const llm: LLMFn = vi.fn(async () => '["flights", 0.9]');
    const router = new LLMRouter(llm);
    router.register("flights", "Book flights");

    const result = await router.classify("test", makeCtx());
    expect(result.intent).toBe("unknown");
  });

  it("handles LLM returning JSON with empty handler string", async () => {
    const llm: LLMFn = vi.fn(async () => '{"handler":"","confidence":0.9}');
    const router = new LLMRouter(llm);
    router.register("flights", "Book flights");

    const result = await router.classify("test", makeCtx());
    expect(result.intent).toBe("unknown");
  });
});

// ---------------------------------------------------------------------------
// N-621: InProcessRuntime
// ---------------------------------------------------------------------------

describe("InProcessRuntime", () => {
  it("invokes a registered handler and returns SUCCESS", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("greet", async (input) => `Hello, ${input.message}!`);

    const input = createAgentInput("world");
    const result = await runtime.invoke("greet", input, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("Hello, world!");
    expect(result.handler).toBe("greet");
  });

  it("returns ERROR for unknown handler", async () => {
    const runtime = new InProcessRuntime();
    const input = createAgentInput("test");
    const result = await runtime.invoke("nonexistent", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("not found");
  });

  it("returns ERROR when handler throws", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("fail", async () => {
      throw new Error("boom");
    });

    const input = createAgentInput("test");
    const result = await runtime.invoke("fail", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("boom");
  });

  it("returns TIMEOUT when handler exceeds deadline", async () => {
    const runtime = new InProcessRuntime({ timeoutMs: 50 });
    runtime.register("slow", async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
      return "done";
    });

    const input = createAgentInput("test");
    const result = await runtime.invoke("slow", input, makeCtx());
    expect(result.status).toBe("timeout");
    expect(result.error).toContain("timed out");
  });

  it("integrates circuit breaker — opens after failures", async () => {
    const runtime = new InProcessRuntime({
      circuitBreaker: { failureThreshold: 2, recoveryMs: 60_000 },
    });
    runtime.register("flaky", async () => {
      throw new Error("fail");
    });

    const input = createAgentInput("test");
    await runtime.invoke("flaky", input, makeCtx());
    await runtime.invoke("flaky", input, makeCtx());

    // Circuit should now be open
    const result = await runtime.invoke("flaky", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("circuit open");
  });

  it("streaming handler pushes chunks to ctx.stream", async () => {
    const runtime = new InProcessRuntime();

    const streamFn: StreamingHandlerFn = async function* (_input, _ctx) {
      yield "chunk1";
      yield "chunk2";
      yield "chunk3";
    };
    runtime.registerStreaming("stream", streamFn);

    const sink = new InMemoryStreamSink();
    const ctx = ExecContext.create({ userId: "test", stream: sink });
    const input = createAgentInput("test");

    const result = await runtime.invoke("stream", input, ctx);
    expect(result.status).toBe("success");
    expect(result.output).toBe("chunk1chunk2chunk3");
    expect(sink.chunks).toEqual(["chunk1", "chunk2", "chunk3"]);
  });

  it("streaming handler works without stream sink", async () => {
    const runtime = new InProcessRuntime();

    const streamFn: StreamingHandlerFn = async function* () {
      yield "a";
      yield "b";
    };
    runtime.registerStreaming("stream", streamFn);

    const input = createAgentInput("test");
    const result = await runtime.invoke("stream", input, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("ab");
  });

  it("invokeChain pipes output through handlers", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("upper", async (input) => input.message.toUpperCase());
    runtime.register("exclaim", async (input) => `${input.message}!!!`);

    const input = createAgentInput("hello");
    const result = await runtime.invokeChain(["upper", "exclaim"], input, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("HELLO!!!");
  });

  it("invokeChain stops early on non-SUCCESS", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("fail", async () => {
      throw new Error("broken");
    });
    runtime.register("never", async () => "should not run");

    const input = createAgentInput("test");
    const result = await runtime.invokeChain(["fail", "never"], input, makeCtx());
    expect(result.status).toBe("error");
  });

  it("invokeChain throws on empty handlers list", async () => {
    const runtime = new InProcessRuntime();
    const input = createAgentInput("test");
    await expect(runtime.invokeChain([], input, makeCtx())).rejects.toThrow(
      "handlers list must not be empty",
    );
  });

  it("delegate creates a child context", async () => {
    const runtime = new InProcessRuntime();
    let childTraceId = "";
    runtime.register("child", async (_input, ctx) => {
      childTraceId = ctx.traceId;
      return "delegated";
    });

    const parentCtx = makeCtx();
    const input = createAgentInput("test");
    const result = await runtime.delegate("child", input, parentCtx);
    expect(result.status).toBe("success");
    expect(result.output).toBe("delegated");
    // Child inherits parent trace
    expect(childTraceId).toBe(parentCtx.traceId);
  });

  it("throws on duplicate handler registration", () => {
    const runtime = new InProcessRuntime();
    runtime.register("h", async () => "ok");
    expect(() => runtime.register("h", async () => "dup")).toThrow("already registered");
  });

  it("throws on duplicate streaming handler registration", () => {
    const runtime = new InProcessRuntime();
    runtime.register("h", async () => "ok");
    const streamFn: StreamingHandlerFn = async function* () { yield "x"; };
    expect(() => runtime.registerStreaming("h", streamFn)).toThrow("already registered");
  });

  it("handles handler that returns empty string", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("empty", async () => "");

    const input = createAgentInput("test");
    const result = await runtime.invoke("empty", input, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("");
  });

  it("handles non-Error throw from handler", async () => {
    const runtime = new InProcessRuntime();
    runtime.register("weird", async () => {
      throw "string error"; // eslint-disable-line no-throw-literal
    });

    const input = createAgentInput("test");
    const result = await runtime.invoke("weird", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("string error");
  });
});

// ---------------------------------------------------------------------------
// N-622: ContainerRuntime (unit tests — no Docker dependency)
// ---------------------------------------------------------------------------

describe("ContainerRuntime", () => {
  it("returns ERROR for unregistered handler", async () => {
    const runtime = new ContainerRuntime();
    const input = createAgentInput("test");
    const result = await runtime.invoke("missing", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("not found");
  });

  it("register throws on duplicate handler", () => {
    const runtime = new ContainerRuntime();
    runtime.register("h", { image: "img:latest" });
    expect(() => runtime.register("h", { image: "img2:latest" })).toThrow("already registered");
  });

  it("invokeChain throws on empty handlers list", async () => {
    const runtime = new ContainerRuntime();
    const input = createAgentInput("test");
    await expect(runtime.invokeChain([], input, makeCtx())).rejects.toThrow(
      "handlers list must not be empty",
    );
  });

  it("circuit breaker opens after repeated failures", async () => {
    const runtime = new ContainerRuntime({
      timeoutMs: 100,
      circuitBreaker: { failureThreshold: 1, recoveryMs: 60_000 },
    });
    runtime.register("h", { image: "nonexistent:latest" });

    const input = createAgentInput("test");
    // First call will fail (docker not running or image not found)
    await runtime.invoke("h", input, makeCtx());

    // Circuit should now be open
    const result = await runtime.invoke("h", input, makeCtx());
    expect(result.status).toBe("error");
    expect(result.error).toContain("circuit open");
  });
});

// ---------------------------------------------------------------------------
// N-623: CompositeToolManager
// ---------------------------------------------------------------------------

describe("CompositeToolManager", () => {
  it("merges tools from multiple managers", async () => {
    const mgr1 = new FunctionToolManager();
    mgr1.tool("add", "Add numbers", undefined, (args) => Number(args["a"]) + Number(args["b"]));

    const mgr2 = new FunctionToolManager();
    mgr2.tool("subtract", "Subtract numbers", undefined, (args) => Number(args["a"]) - Number(args["b"]));

    const composite = new CompositeToolManager([mgr1, mgr2]);
    const tools = await composite.discover(makeCtx());

    const names = tools.map((t) => t.name);
    expect(names).toContain("add");
    expect(names).toContain("subtract");
  });

  it("deduplicates by name — first manager wins", async () => {
    const mgr1 = new FunctionToolManager();
    mgr1.tool("calc", "Calculator v1", undefined, () => "v1");

    const mgr2 = new FunctionToolManager();
    mgr2.tool("calc", "Calculator v2", undefined, () => "v2");

    const composite = new CompositeToolManager([mgr1, mgr2]);
    const tools = await composite.discover(makeCtx());

    const calcTools = tools.filter((t) => t.name === "calc");
    expect(calcTools).toHaveLength(1);
    expect(calcTools[0]?.description).toBe("Calculator v1");
  });

  it("routes call to the owning manager", async () => {
    const mgr1 = new FunctionToolManager();
    mgr1.tool("add", "Add", undefined, (args) => Number(args["a"]) + Number(args["b"]));

    const mgr2 = new FunctionToolManager();
    mgr2.tool("multiply", "Multiply", undefined, (args) => Number(args["a"]) * Number(args["b"]));

    const composite = new CompositeToolManager([mgr1, mgr2]);
    await composite.discover(makeCtx());

    const result = await composite.call("multiply", { a: 3, b: 4 }, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("12");
  });

  it("returns NOT_FOUND for unknown tool", async () => {
    const mgr = new FunctionToolManager();
    const composite = new CompositeToolManager([mgr]);
    await composite.discover(makeCtx());

    const result = await composite.call("nonexistent", {}, makeCtx());
    expect(result.status).toBe("not_found");
  });

  it("handles empty manager list", async () => {
    const composite = new CompositeToolManager([]);
    const tools = await composite.discover(makeCtx());
    expect(tools).toHaveLength(0);
  });

  it("auto-discovers when calling unknown tool", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("late", "Late tool", undefined, () => "found");

    const composite = new CompositeToolManager([mgr]);
    // No explicit discover() call — call() should trigger it
    const result = await composite.call("late", {}, makeCtx());
    expect(result.status).toBe("success");
    expect(result.output).toBe("found");
  });

  it("respects permission filtering from underlying managers", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("restricted", "Restricted tool", {
      requiredPermissions: new Set(["admin"]),
    }, () => "secret");

    const composite = new CompositeToolManager([mgr]);
    const ctx = ExecContext.create({
      permissions: createPermissions({ roles: new Set(["user"]) }),
    });

    const tools = await composite.discover(ctx);
    expect(tools.find((t) => t.name === "restricted")).toBeUndefined();
  });

  /** Stub ToolManager for testing composite with heterogeneous managers. */
  class StubToolManager implements ToolManager {
    private readonly _tools: ToolSpec[];
    private readonly _callResult: ToolResult;

    constructor(tools: ToolSpec[], callResult: ToolResult) {
      this._tools = tools;
      this._callResult = callResult;
    }

    async discover(_ctx: ExecContext): Promise<ToolSpec[]> {
      return [...this._tools];
    }

    async call(_tool: string, _args: Record<string, unknown>, _ctx: ExecContext): Promise<ToolResult> {
      return this._callResult;
    }
  }

  it("works with heterogeneous manager types", async () => {
    const stubSpec = createToolSpec("stub-tool", "A stub tool");
    const stubResult = createToolResult(ToolStatus.SUCCESS, { output: "stub-output" });
    const stubMgr = new StubToolManager([stubSpec], stubResult);

    const fnMgr = new FunctionToolManager();
    fnMgr.tool("fn-tool", "A function tool", undefined, () => "fn-output");

    const composite = new CompositeToolManager([stubMgr, fnMgr]);
    const tools = await composite.discover(makeCtx());
    expect(tools).toHaveLength(2);

    const result = await composite.call("stub-tool", {}, makeCtx());
    expect(result.output).toBe("stub-output");
  });
});

// ---------------------------------------------------------------------------
// N-624: ToneResponder
// ---------------------------------------------------------------------------

describe("ToneResponder", () => {
  it("rewrites text output in the configured tone", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_sys: string, text: string) => {
      return `[rewritten] ${text}`;
    });
    const responder = new ToneResponder(rewrite, "friendly");
    const channel = createChannel("api");
    const result = await responder.format(makeResult("Hello world"), channel, makeCtx());

    expect(result.text).toBe("[rewritten] Hello world");
    expect(rewrite).toHaveBeenCalledTimes(1);
  });

  it("includes tone in the system prompt", async () => {
    let capturedSystem = "";
    const rewrite: ToneRewriteFn = vi.fn(async (sys: string, text: string) => {
      capturedSystem = sys;
      return text;
    });
    const responder = new ToneResponder(rewrite, "formal and professional");
    const channel = createChannel("api");
    await responder.format(makeResult("Hi"), channel, makeCtx());

    expect(capturedSystem).toContain("formal and professional");
  });

  it("passes through error status without rewriting", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_s: string, t: string) => `[rewritten] ${t}`);
    const responder = new ToneResponder(rewrite, "casual");
    const channel = createChannel("api");

    const errorResult = makeResult("Something broke", "error");
    const response = await responder.format(errorResult, channel, makeCtx());

    expect(response.text).toBe("Something broke");
    expect(rewrite).not.toHaveBeenCalled();
  });

  it("passes through timeout status without rewriting", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_s: string, t: string) => `[rewritten] ${t}`);
    const responder = new ToneResponder(rewrite, "casual");
    const channel = createChannel("api");

    const result = makeResult("timed out", "timeout");
    const response = await responder.format(result, channel, makeCtx());

    expect(response.text).toBe("timed out");
    expect(rewrite).not.toHaveBeenCalled();
  });

  it("passes through empty output without rewriting", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_s: string, t: string) => `[rewritten] ${t}`);
    const responder = new ToneResponder(rewrite, "casual");
    const channel = createChannel("api");

    const response = await responder.format(makeResult(""), channel, makeCtx());
    expect(response.text).toBe("");
    expect(rewrite).not.toHaveBeenCalled();
  });

  it("truncates to channel maxLength after rewriting", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_s: string, _t: string) => "a very long rewritten response");
    const responder = new ToneResponder(rewrite, "verbose");
    const channel = createChannel("api", { maxLength: 10 });

    const response = await responder.format(makeResult("short"), channel, makeCtx());
    expect(response.text).toBe("a very lon");
  });

  it("passes through wrong_handler status", async () => {
    const rewrite: ToneRewriteFn = vi.fn(async (_s: string, t: string) => `[rewritten] ${t}`);
    const responder = new ToneResponder(rewrite, "casual");
    const channel = createChannel("api");

    const result = makeResult("wrong handler", "wrong_handler");
    const response = await responder.format(result, channel, makeCtx());
    expect(response.text).toBe("wrong handler");
    expect(rewrite).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// N-625: MultimodalResponder
// ---------------------------------------------------------------------------

describe("MultimodalResponder", () => {
  it("renders text blocks as plain text", async () => {
    const responder = new MultimodalResponder([createTextBlock("Extra info")]);
    const channel = createChannel("api");
    const response = await responder.format(makeResult("Main output"), channel, makeCtx());

    expect(response.text).toContain("Main output");
    expect(response.text).toContain("Extra info");
  });

  it("renders image blocks with markdown on capable channels", async () => {
    const responder = new MultimodalResponder([
      createImageBlock("https://example.com/img.png", "A chart"),
    ]);
    const channel = createChannel("web", { supportsMarkdown: true, supportsMedia: true });
    const response = await responder.format(makeResult("Results:"), channel, makeCtx());

    expect(response.text).toContain("![A chart](https://example.com/img.png)");
    expect(response.media).toContain("https://example.com/img.png");
  });

  it("degrades image blocks to alt text on non-media channels", async () => {
    const responder = new MultimodalResponder([
      createImageBlock("https://example.com/img.png", "A chart"),
    ]);
    const channel = createChannel("sms", { supportsMarkdown: false, supportsMedia: false });
    const response = await responder.format(makeResult("Results:"), channel, makeCtx());

    expect(response.text).toContain("[Image: A chart]");
    expect(response.text).not.toContain("https://example.com/img.png");
    expect(response.media).toHaveLength(0);
  });

  it("renders card blocks with markdown headers", async () => {
    const responder = new MultimodalResponder([
      createCardBlock("Summary", "Revenue up 15%"),
    ]);
    const channel = createChannel("web", { supportsMarkdown: true });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toContain("**Summary**");
    expect(response.text).toContain("Revenue up 15%");
  });

  it("renders card blocks as plain text on non-markdown channels", async () => {
    const responder = new MultimodalResponder([
      createCardBlock("Summary", "Revenue up 15%"),
    ]);
    const channel = createChannel("sms", { supportsMarkdown: false });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toContain("Summary: Revenue up 15%");
  });

  it("renders button blocks with markdown links", async () => {
    const responder = new MultimodalResponder([
      createButtonBlock("Click me", "do_something"),
    ]);
    const channel = createChannel("web", { supportsMarkdown: true });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toContain("[Click me](action:do_something)");
  });

  it("renders button blocks as plain text labels on non-markdown channels", async () => {
    const responder = new MultimodalResponder([
      createButtonBlock("Click me", "do_something"),
    ]);
    const channel = createChannel("sms", { supportsMarkdown: false });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toContain("[Click me]");
    expect(response.text).not.toContain("action:");
  });

  it("stores structured blocks in metadata", async () => {
    const blocks = [createTextBlock("info"), createImageBlock("url", "alt")];
    const responder = new MultimodalResponder(blocks);
    const channel = createChannel("api");
    const response = await responder.format(makeResult("out"), channel, makeCtx());

    expect(response.metadata["blocks"]).toBeDefined();
    const parsed: unknown = JSON.parse(response.metadata["blocks"] ?? "[]");
    expect(Array.isArray(parsed)).toBe(true);
  });

  it("truncates to channel maxLength", async () => {
    const responder = new MultimodalResponder([
      createTextBlock("This is a very long additional text block"),
    ]);
    const channel = createChannel("api", { maxLength: 15 });
    const response = await responder.format(makeResult("Output"), channel, makeCtx());

    expect(response.text.length).toBeLessThanOrEqual(15);
  });

  it("handles empty blocks list", async () => {
    const responder = new MultimodalResponder([]);
    const channel = createChannel("api");
    const response = await responder.format(makeResult("Just text"), channel, makeCtx());

    expect(response.text).toBe("Just text");
  });

  it("handles empty output with blocks", async () => {
    const responder = new MultimodalResponder([createTextBlock("only block")]);
    const channel = createChannel("api");
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toBe("only block");
  });

  it("handles no blocks and empty output", async () => {
    const responder = new MultimodalResponder([]);
    const channel = createChannel("api");
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.text).toBe("");
  });

  it("card with image includes image in media on media-capable channels", async () => {
    const responder = new MultimodalResponder([
      createCardBlock("Title", "Body", "https://example.com/card.png"),
    ]);
    const channel = createChannel("web", { supportsMarkdown: true, supportsMedia: true });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.media).toContain("https://example.com/card.png");
  });

  it("card without image does not add to media", async () => {
    const responder = new MultimodalResponder([
      createCardBlock("Title", "Body"),
    ]);
    const channel = createChannel("web", { supportsMarkdown: true, supportsMedia: true });
    const response = await responder.format(makeResult(""), channel, makeCtx());

    expect(response.media).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Content block factory functions
// ---------------------------------------------------------------------------

describe("Content block factories", () => {
  it("createTextBlock sets correct type", () => {
    const block = createTextBlock("hello");
    expect(block.type).toBe(BlockType.TEXT);
    expect(block.text).toBe("hello");
  });

  it("createImageBlock sets correct type and fields", () => {
    const block = createImageBlock("url", "alt");
    expect(block.type).toBe(BlockType.IMAGE);
    expect(block.url).toBe("url");
    expect(block.alt).toBe("alt");
  });

  it("createCardBlock sets correct type with optional image", () => {
    const withImage = createCardBlock("Title", "Body", "img-url");
    expect(withImage.type).toBe(BlockType.CARD);
    expect(withImage.imageUrl).toBe("img-url");

    const withoutImage = createCardBlock("Title", "Body");
    expect(withoutImage.type).toBe(BlockType.CARD);
    expect(withoutImage.imageUrl).toBeUndefined();
  });

  it("createButtonBlock sets correct type and fields", () => {
    const block = createButtonBlock("Click", "action_id");
    expect(block.type).toBe(BlockType.BUTTON);
    expect(block.label).toBe("Click");
    expect(block.action).toBe("action_id");
  });
});
