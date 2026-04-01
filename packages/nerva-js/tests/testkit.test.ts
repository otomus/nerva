import { describe, it, expect } from "vitest";
import { ExecContext } from "../src/context.js";
import { RuleRouter } from "../src/router/rule.js";
import { InProcessRuntime } from "../src/runtime/inprocess.js";
import { NoopPolicyEngine } from "../src/policy/noop.js";
import { FunctionToolManager } from "../src/tools/function.js";
import { createHandlerCandidate, createIntentResult } from "../src/router/index.js";
import {
  SpyRouter,
  SpyRuntime,
  SpyResponder,
  SpyMemory,
  SpyPolicy,
  SpyToolManager,
} from "../src/testkit/spies.js";
import {
  createTestOrchestrator,
} from "../src/testkit/builders.js";
import {
  assertRoutedTo,
  assertHandlerInvoked,
  assertPolicyAllowed,
  assertPolicyDenied,
  assertMemoryStored,
  assertMemoryRecalled,
  assertNoUnconsumedExpectations,
  assertPipelineOrder,
} from "../src/testkit/assertions.js";
import {
  createTestCtx,
  createSpyRouter,
  createSpyRuntime,
} from "../src/testkit/factories.js";
import {
  StubLLMHandler,
  DenyAllPolicy,
  AllowAllPolicy,
} from "../src/testkit/boundaries.js";

function makeCtx(): ExecContext {
  return ExecContext.create({ userId: "test-user", sessionId: "test-session" });
}

// ---------------------------------------------------------------------------
// SpyRouter
// ---------------------------------------------------------------------------

describe("SpyRouter", () => {
  it("records classify calls in passthrough mode", async () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "catch", intent: "any" }],
      null,
    );
    const spy = new SpyRouter(inner);
    const ctx = makeCtx();

    const result = await spy.classify("hello", ctx);

    expect(spy.classifyCalls).toHaveLength(1);
    expect(spy.classifyCalls[0]!.message).toBe("hello");
    expect(spy.classifyCalls[0]!.wasExpected).toBe(false);
    expect(result.bestHandler?.name).toBe("catch");
  });

  it("consumes expectations FIFO", async () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "real", intent: "any" }],
      null,
    );
    const spy = new SpyRouter(inner);
    const ctx = makeCtx();

    spy.expectHandler("first_agent");
    spy.expectHandler("second_agent");

    const r1 = await spy.classify("msg1", ctx);
    const r2 = await spy.classify("msg2", ctx);
    const r3 = await spy.classify("msg3", ctx);

    expect(r1.bestHandler?.name).toBe("first_agent");
    expect(r2.bestHandler?.name).toBe("second_agent");
    expect(r3.bestHandler?.name).toBe("real");
    expect(spy.classifyCalls[0]!.wasExpected).toBe(true);
    expect(spy.classifyCalls[2]!.wasExpected).toBe(false);
  });

  it("expectIntent returns exact result", async () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "real", intent: "any" }],
      null,
    );
    const spy = new SpyRouter(inner);

    const custom = createIntentResult("custom", 0.8, [
      createHandlerCandidate("custom_handler", 0.8),
    ]);
    spy.expectIntent(custom);

    const result = await spy.classify("test", makeCtx());
    expect(result).toBe(custom);
  });

  it("reset clears everything", () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "r", intent: "i" }],
      null,
    );
    const spy = new SpyRouter(inner);
    spy.expectHandler("x");
    spy.reset();

    expect(spy.pendingExpectations).toBe(0);
    expect(spy.classifyCalls).toHaveLength(0);
  });

  it("verifyExpectationsConsumed throws with pending", () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "r", intent: "i" }],
      null,
    );
    const spy = new SpyRouter(inner);
    spy.expectHandler("unconsumed");

    expect(() => spy.verifyExpectationsConsumed()).toThrow("unconsumed");
  });
});

// ---------------------------------------------------------------------------
// SpyRuntime
// ---------------------------------------------------------------------------

describe("SpyRuntime", () => {
  it("expectLlmResponse returns SUCCESS with given output", async () => {
    const inner = new InProcessRuntime();
    const spy = new SpyRuntime(inner);
    spy.expectLlmResponse("Hello from LLM!");

    const result = await spy.invoke(
      "any_handler",
      { message: "hi", args: {}, tools: [], history: [] },
      makeCtx(),
    );

    expect(result.status).toBe("success");
    expect(result.output).toBe("Hello from LLM!");
    expect(spy.invokeCalls).toHaveLength(1);
    expect(spy.invokeCalls[0]!.wasExpected).toBe(true);
  });

  it("falls back to real runtime after expectations exhausted", async () => {
    const inner = new InProcessRuntime();
    inner.register("echo", async (input, _ctx) => `echo: ${input.message}`);
    const spy = new SpyRuntime(inner);
    spy.expectLlmResponse("expected");

    const r1 = await spy.invoke(
      "echo",
      { message: "first", args: {}, tools: [], history: [] },
      makeCtx(),
    );
    const r2 = await spy.invoke(
      "echo",
      { message: "second", args: {}, tools: [], history: [] },
      makeCtx(),
    );

    expect(r1.output).toBe("expected");
    expect(r2.output).toBe("echo: second");
  });
});

// ---------------------------------------------------------------------------
// SpyPolicy
// ---------------------------------------------------------------------------

describe("SpyPolicy", () => {
  it("expectAllow returns allowed decision", async () => {
    const spy = new SpyPolicy(new NoopPolicyEngine());
    spy.expectAllow();

    const result = await spy.evaluate(
      { kind: "test", subject: "u", target: "t", metadata: {} },
      makeCtx(),
    );

    expect(result.allowed).toBe(true);
    expect(spy.evaluateCalls[0]!.wasExpected).toBe(true);
  });

  it("expectDeny returns denial", async () => {
    const spy = new SpyPolicy(new NoopPolicyEngine());
    spy.expectDeny("over budget");

    const result = await spy.evaluate(
      { kind: "test", subject: "u", target: "t", metadata: {} },
      makeCtx(),
    );

    expect(result.allowed).toBe(false);
    expect(result.reason).toBe("over budget");
  });

  it("record calls are tracked", async () => {
    const spy = new SpyPolicy(new NoopPolicyEngine());
    const action = { kind: "test", subject: "u", target: "t", metadata: {} };
    const decision = { allowed: true, reason: null, requireApproval: false, approvers: null, budgetRemaining: null };
    await spy.record(action, decision, makeCtx());

    expect(spy.recordCalls).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// SpyToolManager
// ---------------------------------------------------------------------------

describe("SpyToolManager", () => {
  it("expectToolResult returns configured result", async () => {
    const inner = new FunctionToolManager();
    const spy = new SpyToolManager(inner);

    const expected = { status: "success" as const, output: "found 3", error: null, durationMs: 0 };
    spy.expectToolResult("search", expected);

    const result = await spy.call("search", { q: "cats" }, makeCtx());

    expect(result).toBe(expected);
    expect(spy.callCalls).toHaveLength(1);
    expect(spy.callCalls[0]!.wasExpected).toBe(true);
  });

  it("different tools have independent queues", async () => {
    const inner = new FunctionToolManager();
    const spy = new SpyToolManager(inner);

    spy.expectToolResult("search", { status: "success", output: "search result", error: null, durationMs: 0 });
    spy.expectToolResult("calc", { status: "success", output: "42", error: null, durationMs: 0 });

    const r1 = await spy.call("calc", {}, makeCtx());
    const r2 = await spy.call("search", {}, makeCtx());

    expect(r1.output).toBe("42");
    expect(r2.output).toBe("search result");
  });
});

// ---------------------------------------------------------------------------
// createTestOrchestrator
// ---------------------------------------------------------------------------

describe("createTestOrchestrator", () => {
  it("creates working orchestrator with spy-wrapped defaults", () => {
    const result = createTestOrchestrator();

    expect(result.orchestrator).toBeDefined();
    expect(result.router).toBeInstanceOf(SpyRouter);
    expect(result.runtime).toBeInstanceOf(SpyRuntime);
    expect(result.responder).toBeInstanceOf(SpyResponder);
    expect(result.memory).toBeInstanceOf(SpyMemory);
    expect(result.policy).toBeInstanceOf(SpyPolicy);
    expect(result.tools).toBeInstanceOf(SpyToolManager);
  });

  it("full pipeline with expectations", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("Hello from agent!");

    const response = await result.orchestrator.handle("hi");

    expect(response.text).toBe("Hello from agent!");
    assertRoutedTo(result.router, "default");
  });

  it("resetAll clears all spy state", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("test");
    await result.orchestrator.handle("hi");

    result.resetAll();

    expect(result.router.classifyCalls).toHaveLength(0);
    expect(result.runtime.invokeCalls).toHaveLength(0);
    expect(result.router.pendingExpectations).toBe(0);
  });

  it("accepts custom handlers", async () => {
    const result = createTestOrchestrator({
      handlers: {
        default: async (input, _ctx) => `Hi, ${input.message}!`,
      },
    });

    const response = await result.orchestrator.handle("world");
    expect(response.text).toBe("Hi, world!");
    assertHandlerInvoked(result.runtime, "default");
  });
});

// ---------------------------------------------------------------------------
// Assertions
// ---------------------------------------------------------------------------

describe("Assertions", () => {
  it("assertRoutedTo passes when handler matches", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("hi");
    assertRoutedTo(result.router, "default");
  });

  it("assertRoutedTo fails when handler doesn't match", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("hi");

    expect(() => assertRoutedTo(result.router, "wrong")).toThrow("wrong");
  });

  it("assertPolicyAllowed passes when policy allows", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("hi");
    assertPolicyAllowed(result.policy);
  });

  it("assertPolicyDenied passes when policy denies", async () => {
    const spy = new SpyPolicy(new NoopPolicyEngine());
    spy.expectDeny("budget exceeded");

    await spy.evaluate(
      { kind: "test", subject: "u", target: "t", metadata: {} },
      makeCtx(),
    );

    assertPolicyDenied(spy, { reason: "budget exceeded" });
  });

  it("assertMemoryStored passes with matching content", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("stored_content");
    await result.orchestrator.handle("hi");
    assertMemoryStored(result.memory, { content: "stored_content" });
  });

  it("assertMemoryRecalled passes with matching query", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("my query");
    assertMemoryRecalled(result.memory, { query: "my query" });
  });

  it("assertNoUnconsumedExpectations passes when all consumed", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("hi");
    assertNoUnconsumedExpectations(result);
  });

  it("assertNoUnconsumedExpectations fails with pending", () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("never consumed");

    expect(() => assertNoUnconsumedExpectations(result)).toThrow("unconsumed");
  });

  it("assertPipelineOrder verifies execution order", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("ok");
    await result.orchestrator.handle("hi");

    assertPipelineOrder(result, ["policy", "memory", "router", "runtime", "responder"]);
  });
});

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

describe("Factories", () => {
  it("createTestCtx creates context with defaults", () => {
    const ctx = createTestCtx();
    expect(ctx.userId).toBe("test-user");
    expect(ctx.sessionId).toBe("test-session");
  });

  it("createSpyRouter wraps default RuleRouter", () => {
    const spy = createSpyRouter();
    expect(spy).toBeInstanceOf(SpyRouter);
  });

  it("createSpyRuntime wraps default InProcessRuntime", () => {
    const spy = createSpyRuntime();
    expect(spy).toBeInstanceOf(SpyRuntime);
  });
});

// ---------------------------------------------------------------------------
// Boundaries
// ---------------------------------------------------------------------------

describe("Boundaries", () => {
  it("StubLLMHandler returns canned responses in order", async () => {
    const handler = new StubLLMHandler(["first", "second"], "default");
    const input = { message: "a", args: {}, tools: [], history: [] };
    const ctx = makeCtx();

    const r1 = await handler.handle(input, ctx);
    const r2 = await handler.handle(input, ctx);
    const r3 = await handler.handle(input, ctx);

    expect(r1.output).toBe("first");
    expect(r2.output).toBe("second");
    expect(r3.output).toBe("default");
    expect(handler.callCount).toBe(3);
  });

  it("DenyAllPolicy always denies", async () => {
    const policy = new DenyAllPolicy("test denial");
    const result = await policy.evaluate(
      { kind: "test", subject: "u", target: "t", metadata: {} },
      makeCtx(),
    );
    expect(result.allowed).toBe(false);
    expect(result.reason).toBe("test denial");
  });

  it("AllowAllPolicy always allows", async () => {
    const policy = new AllowAllPolicy();
    const result = await policy.evaluate(
      { kind: "test", subject: "u", target: "t", metadata: {} },
      makeCtx(),
    );
    expect(result.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("Edge cases", () => {
  it("handles unicode messages", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("❤️ 🚀");
    const response = await result.orchestrator.handle("你好");
    expect(response.text).toContain("❤");
  });

  it("multiple sequential expectations consumed in order", async () => {
    const result = createTestOrchestrator();
    result.runtime.expectLlmResponse("first");
    result.runtime.expectLlmResponse("second");
    result.runtime.expectLlmResponse("third");

    const r1 = await result.orchestrator.handle("a");
    const r2 = await result.orchestrator.handle("b");
    const r3 = await result.orchestrator.handle("c");

    expect(r1.text).toBe("first");
    expect(r2.text).toBe("second");
    expect(r3.text).toBe("third");
  });

  it("inner property exposes wrapped implementation", () => {
    const inner = new RuleRouter(
      [{ pattern: ".*", handler: "h", intent: "i" }],
      null,
    );
    const spy = new SpyRouter(inner);
    expect(spy.inner).toBe(inner);
  });
});
