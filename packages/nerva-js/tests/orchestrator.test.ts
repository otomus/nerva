import { describe, it, expect } from "vitest";
import {
  Orchestrator,
  PolicyDeniedError,
  FALLBACK_HANDLER,
  POLICY_ACTION_ROUTE,
  POLICY_ACTION_INVOKE,
  API_CHANNEL,
  MIDDLEWARE_STAGES,
} from "../src/orchestrator.js";
import { ExecContext } from "../src/context.js";
import { RuleRouter } from "../src/router/rule.js";
import type {
  AgentResult,
  AgentInput,
  AgentRuntime,
  Responder,
  Response,
  Channel,
  PolicyEngine,
  PolicyAction,
  PolicyDecision,
  Memory,
  MemoryEvent,
  MiddlewareHandler,
} from "../src/orchestrator.js";
import type { Rule } from "../src/router/rule.js";

// ---------------------------------------------------------------------------
// Mock primitives
// ---------------------------------------------------------------------------

function makeRuntime(output: string = "ok"): AgentRuntime {
  return {
    invoke: async (handler, input, _ctx): Promise<AgentResult> => ({
      status: "success",
      output,
      handler,
      data: {},
    }),
  };
}

function makeResponder(): Responder {
  return {
    format: async (result, channel, _ctx): Promise<Response> => ({
      text: result.output,
      channel,
    }),
  };
}

function makeRouter(rules?: Rule[], defaultHandler?: string) {
  return new RuleRouter(
    rules ?? [{ pattern: ".*", handler: "catch-all", intent: "any" }],
    defaultHandler ?? null,
  );
}

function makeDenyPolicy(): PolicyEngine {
  return {
    evaluate: async (): Promise<PolicyDecision> => ({
      allowed: false,
      reason: "blocked",
    }),
    record: async () => {},
  };
}

function makeAllowPolicy(): PolicyEngine {
  return {
    evaluate: async (): Promise<PolicyDecision> => ({ allowed: true, reason: null }),
    record: async () => {},
  };
}

function makeMemory(): Memory & { stored: MemoryEvent[] } {
  const stored: MemoryEvent[] = [];
  return {
    stored,
    recall: async () => ({ conversation: [] }),
    store: async (event) => { stored.push(event); },
  };
}

// ---------------------------------------------------------------------------
// handle() — happy path
// ---------------------------------------------------------------------------

describe("Orchestrator.handle happy path", () => {
  it("processes a message through the full pipeline", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("hello world"),
      responder: makeResponder(),
    });
    const response = await orch.handle("hi");
    expect(response.text).toBe("hello world");
    expect(response.channel).toBe(API_CHANNEL);
  });

  it("creates ExecContext automatically when none provided", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    const response = await orch.handle("hi");
    expect(response.text).toBe("ok");
  });

  it("uses provided ExecContext", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    const ctx = ExecContext.create({ userId: "custom-user" });
    const response = await orch.handle("hi", ctx);
    expect(response.text).toBe("ok");
  });

  it("uses provided channel", async () => {
    const customChannel: Channel = {
      name: "slack",
      supportsMarkdown: true,
      supportsMedia: true,
      maxLength: 4000,
    };
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    const response = await orch.handle("hi", null, customChannel);
    expect(response.channel.name).toBe("slack");
  });
});

// ---------------------------------------------------------------------------
// handle() — fallback handler
// ---------------------------------------------------------------------------

describe("Orchestrator.handle routing", () => {
  it("uses FALLBACK_HANDLER when router returns no candidates", async () => {
    let invoked = "";
    const runtime: AgentRuntime = {
      invoke: async (handler, _input, _ctx) => {
        invoked = handler;
        return { status: "success", output: "fallback", handler, data: {} };
      },
    };
    const orch = new Orchestrator({
      router: makeRouter([], undefined), // no rules, no default
      runtime,
      responder: makeResponder(),
    });
    await orch.handle("unknown message");
    expect(invoked).toBe(FALLBACK_HANDLER);
  });
});

// ---------------------------------------------------------------------------
// Policy denial
// ---------------------------------------------------------------------------

describe("Orchestrator.handle policy denial", () => {
  it("throws PolicyDeniedError when policy denies route", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime(),
      responder: makeResponder(),
      policy: makeDenyPolicy(),
    });
    await expect(orch.handle("hi")).rejects.toThrow(PolicyDeniedError);
  });

  it("PolicyDeniedError contains the decision", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime(),
      responder: makeResponder(),
      policy: makeDenyPolicy(),
    });
    try {
      await orch.handle("hi");
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(PolicyDeniedError);
      const pde = err as PolicyDeniedError;
      expect(pde.decision.allowed).toBe(false);
      expect(pde.decision.reason).toBe("blocked");
    }
  });

  it("does not throw when policy allows", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
      policy: makeAllowPolicy(),
    });
    const response = await orch.handle("hi");
    expect(response.text).toBe("ok");
  });
});

// ---------------------------------------------------------------------------
// Memory integration
// ---------------------------------------------------------------------------

describe("Orchestrator.handle memory", () => {
  it("stores successful results in memory", async () => {
    const mem = makeMemory();
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("stored output"),
      responder: makeResponder(),
      memory: mem,
    });
    await orch.handle("hi");
    expect(mem.stored).toHaveLength(1);
    expect(mem.stored[0]!.content).toBe("stored output");
  });

  it("does not store non-success results", async () => {
    const mem = makeMemory();
    const runtime: AgentRuntime = {
      invoke: async (handler) => ({
        status: "error",
        output: "",
        error: "boom",
        handler,
        data: {},
      }),
    };
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime,
      responder: makeResponder(),
      memory: mem,
    });
    await orch.handle("hi");
    expect(mem.stored).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Middleware ordering
// ---------------------------------------------------------------------------

describe("Orchestrator middleware", () => {
  it("runs middleware in registration order", async () => {
    const order: string[] = [];
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });

    orch.use("before_route", async (_ctx, payload) => {
      order.push("mw1");
      return payload;
    });
    orch.use("before_route", async (_ctx, payload) => {
      order.push("mw2");
      return payload;
    });

    await orch.handle("hi");
    expect(order).toEqual(["mw1", "mw2"]);
  });

  it("middleware can replace the payload", async () => {
    const orch = new Orchestrator({
      router: makeRouter([
        { pattern: "modified", handler: "modified-handler", intent: "mod" },
      ]),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });

    orch.use("before_route", async () => {
      return "modified message";
    });

    let invoked = "";
    const runtime: AgentRuntime = {
      invoke: async (handler, input, _ctx) => {
        invoked = handler;
        return { status: "success", output: "ok", handler, data: {} };
      },
    };

    const orch2 = new Orchestrator({
      router: makeRouter([
        { pattern: "modified", handler: "modified-handler", intent: "mod" },
      ]),
      runtime,
      responder: makeResponder(),
    });
    orch2.use("before_route", async () => "modified message");
    await orch2.handle("original message");
    expect(invoked).toBe("modified-handler");
  });

  it("middleware returning null/undefined keeps original payload", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    orch.use("before_route", async () => null);
    orch.use("before_route", async () => undefined);
    const response = await orch.handle("hi");
    expect(response.text).toBe("ok");
  });

  it("supports all four middleware stages", async () => {
    const stages: string[] = [];
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });

    for (const stage of MIDDLEWARE_STAGES) {
      orch.use(stage, async (_ctx, payload) => {
        stages.push(stage);
        return payload;
      });
    }

    await orch.handle("hi");
    expect(stages).toEqual([
      "before_route",
      "before_invoke",
      "after_invoke",
      "before_respond",
    ]);
  });
});

// ---------------------------------------------------------------------------
// stream()
// ---------------------------------------------------------------------------

describe("Orchestrator.stream", () => {
  it("yields chunks from the stream sink", async () => {
    const runtime: AgentRuntime = {
      invoke: async (_handler, _input, ctx) => {
        if (ctx.stream) {
          await ctx.stream.push("chunk1");
          await ctx.stream.push("chunk2");
        }
        return { status: "success", output: "done", handler: "h", data: {} };
      },
    };
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime,
      responder: makeResponder(),
    });

    const chunks: string[] = [];
    for await (const chunk of orch.stream("hi")) {
      chunks.push(chunk);
    }
    expect(chunks).toContain("chunk1");
    expect(chunks).toContain("chunk2");
  });

  it("re-throws pipeline errors", async () => {
    const runtime: AgentRuntime = {
      invoke: async () => {
        throw new Error("runtime boom");
      },
    };
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime,
      responder: makeResponder(),
    });

    const chunks: string[] = [];
    await expect(async () => {
      for await (const chunk of orch.stream("hi")) {
        chunks.push(chunk);
      }
    }).rejects.toThrow("runtime boom");
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("Orchestrator edge cases", () => {
  it("handles empty message", async () => {
    const orch = new Orchestrator({
      router: makeRouter([], "default-h"),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    // Empty message hits the router, which returns empty for blank messages
    // then falls back to FALLBACK_HANDLER
    const response = await orch.handle("");
    expect(response.text).toBe("ok");
  });

  it("works without optional primitives (tools, memory, registry, policy)", async () => {
    const orch = new Orchestrator({
      router: makeRouter(),
      runtime: makeRuntime("ok"),
      responder: makeResponder(),
    });
    expect(orch.tools).toBeNull();
    expect(orch.registry).toBeNull();
    const response = await orch.handle("hi");
    expect(response.text).toBe("ok");
  });
});
