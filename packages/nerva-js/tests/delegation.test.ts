import { describe, it, expect } from "vitest";
import {
  Orchestrator,
  DEFAULT_MAX_DELEGATION_DEPTH,
  DELEGATION_DEPTH_EXCEEDED_TEMPLATE,
} from "../src/orchestrator.js";
import { ExecContext, createPermissions, TokenUsage } from "../src/context.js";
import { RuleRouter } from "../src/router/rule.js";
import type {
  AgentResult,
  AgentInput,
  AgentRuntime,
  Responder,
  Response,
  Channel,
} from "../src/orchestrator.js";

// ---------------------------------------------------------------------------
// Mock primitives
// ---------------------------------------------------------------------------

function makeRouter() {
  return new RuleRouter(
    [{ pattern: ".*", handler: "catch-all", intent: "any" }],
    null,
  );
}

function makeResponder(): Responder {
  return {
    format: async (result, channel, _ctx): Promise<Response> => ({
      text: result.output,
      channel,
    }),
  };
}

/**
 * Create a runtime that records invocations and optionally records token usage.
 *
 * @param output - Fixed output text for all invocations.
 * @param tokenUsage - Optional token usage to record on the child context.
 * @returns A runtime and its call log.
 */
function makeDelegationRuntime(
  output: string = "delegated result",
  tokenUsage?: TokenUsage,
): AgentRuntime & { invokeCalls: Array<{ handler: string; input: AgentInput; ctx: ExecContext }> } {
  const invokeCalls: Array<{ handler: string; input: AgentInput; ctx: ExecContext }> = [];
  return {
    invokeCalls,
    invoke: async (handler, input, ctx) => {
      invokeCalls.push({ handler, input, ctx });
      if (tokenUsage !== undefined) {
        ctx.recordTokens(tokenUsage);
      }
      return {
        status: "success",
        output,
        data: {},
        error: null,
        handler,
      };
    },
  };
}

function buildOrchestrator(options?: {
  runtime?: AgentRuntime;
  maxDelegationDepth?: number;
}): Orchestrator {
  return new Orchestrator({
    router: makeRouter(),
    runtime: options?.runtime ?? makeDelegationRuntime(),
    responder: makeResponder(),
    maxDelegationDepth: options?.maxDelegationDepth,
  });
}

// ---------------------------------------------------------------------------
// Delegation chain A -> B -> C (3 levels)
// ---------------------------------------------------------------------------

describe("Delegation chain", () => {
  it("three-level chain A -> B -> C succeeds", async () => {
    const results: string[] = [];
    const orch = buildOrchestrator();

    const chainRuntime: AgentRuntime = {
      invoke: async (handler, _input, ctx) => {
        results.push(handler);
        if (handler === "A") {
          return orch.delegate("B", "from A", ctx);
        }
        if (handler === "B") {
          return orch.delegate("C", "from B", ctx);
        }
        return {
          status: "success",
          output: `final from ${handler}`,
          data: {},
          error: null,
          handler,
        };
      },
    };

    // Replace runtime with the chain runtime
    (orch as unknown as { _runtime: AgentRuntime })._runtime = chainRuntime;

    const ctx = ExecContext.create();
    const result = await orch.delegate("A", "start", ctx);

    expect(result.status).toBe("success");
    expect(result.output).toBe("final from C");
    expect(results).toEqual(["A", "B", "C"]);
  });

  it("chain preserves trace_id across all levels", async () => {
    const traceIds: string[] = [];
    const orch = buildOrchestrator();

    const traceRuntime: AgentRuntime = {
      invoke: async (handler, _input, ctx) => {
        traceIds.push(ctx.traceId);
        if (handler === "A") {
          return orch.delegate("B", "msg", ctx);
        }
        return { status: "success", output: "done", data: {}, error: null, handler };
      },
    };

    (orch as unknown as { _runtime: AgentRuntime })._runtime = traceRuntime;

    const ctx = ExecContext.create();
    await orch.delegate("A", "start", ctx);

    expect(traceIds).toHaveLength(2);
    expect(traceIds[0]).toBe(ctx.traceId);
    expect(traceIds[1]).toBe(ctx.traceId);
  });
});

// ---------------------------------------------------------------------------
// Depth limiting (N-631)
// ---------------------------------------------------------------------------

describe("Delegation depth limit", () => {
  it("exceeds default depth — returns error", async () => {
    const orch = buildOrchestrator();
    const ctx = ExecContext.create();
    // Manually set depth by creating nested children
    let current = ctx;
    for (let i = 0; i < DEFAULT_MAX_DELEGATION_DEPTH; i++) {
      current = current.child(`level-${i}`);
    }

    const result = await orch.delegate("deep_handler", "msg", current);

    expect(result.status).toBe("error");
    const expectedMsg = DELEGATION_DEPTH_EXCEEDED_TEMPLATE.replace(
      "{n}",
      String(DEFAULT_MAX_DELEGATION_DEPTH),
    );
    expect(result.error).toBe(expectedMsg);
  });

  it("custom depth limit of 2 blocks at depth 3", async () => {
    const orch = buildOrchestrator({ maxDelegationDepth: 2 });
    // Create a context at depth 2
    let ctx = ExecContext.create();
    ctx = ctx.child("level-1");
    ctx = ctx.child("level-2");
    // ctx.depth is now 2, child() will make 3 which exceeds max 2

    const result = await orch.delegate("handler", "msg", ctx);

    expect(result.status).toBe("error");
    expect(result.error).toContain("max: 2");
  });

  it("at exact limit succeeds", async () => {
    const orch = buildOrchestrator({ maxDelegationDepth: 3 });
    let ctx = ExecContext.create();
    ctx = ctx.child("level-1");
    ctx = ctx.child("level-2");
    // ctx.depth is 2, child() will make 3 which equals max — allowed

    const result = await orch.delegate("handler", "msg", ctx);

    expect(result.status).toBe("success");
  });

  it("root context (depth=0) can delegate", async () => {
    const orch = buildOrchestrator();
    const ctx = ExecContext.create();

    const result = await orch.delegate("handler", "msg", ctx);

    expect(result.status).toBe("success");
  });

  it("records depth_exceeded event on parent context", async () => {
    const orch = buildOrchestrator({ maxDelegationDepth: 1 });
    let ctx = ExecContext.create();
    ctx = ctx.child("level-1");

    await orch.delegate("handler", "msg", ctx);

    const eventNames = ctx.events.map((e) => e.name);
    expect(eventNames).toContain("delegation.depth_exceeded");
  });
});

// ---------------------------------------------------------------------------
// Permission denied
// ---------------------------------------------------------------------------

describe("Delegation permissions", () => {
  it("denied when agent not in allowlist", async () => {
    const perms = createPermissions({
      allowedAgents: new Set(["other_handler"]),
    });
    const ctx = ExecContext.create({ permissions: perms });
    const orch = buildOrchestrator();

    const result = await orch.delegate("forbidden_handler", "msg", ctx);

    expect(result.status).toBe("error");
    expect(result.error).toContain("permission denied");
  });

  it("allowed when agent in allowlist", async () => {
    const perms = createPermissions({
      allowedAgents: new Set(["my_handler"]),
    });
    const ctx = ExecContext.create({ permissions: perms });
    const orch = buildOrchestrator();

    const result = await orch.delegate("my_handler", "msg", ctx);

    expect(result.status).toBe("success");
  });

  it("allowed when no restrictions (null allowedAgents)", async () => {
    const ctx = ExecContext.create();
    const orch = buildOrchestrator();

    const result = await orch.delegate("any_handler", "msg", ctx);

    expect(result.status).toBe("success");
  });

  it("denied when allowlist is empty set", async () => {
    const perms = createPermissions({ allowedAgents: new Set() });
    const ctx = ExecContext.create({ permissions: perms });
    const orch = buildOrchestrator();

    const result = await orch.delegate("handler", "msg", ctx);

    expect(result.status).toBe("error");
  });

  it("records delegation.denied event", async () => {
    const perms = createPermissions({
      allowedAgents: new Set(["other"]),
    });
    const ctx = ExecContext.create({ permissions: perms });
    const orch = buildOrchestrator();

    await orch.delegate("blocked", "msg", ctx);

    const eventNames = ctx.events.map((e) => e.name);
    expect(eventNames).toContain("delegation.denied");
  });
});

// ---------------------------------------------------------------------------
// Token accumulation
// ---------------------------------------------------------------------------

describe("Delegation token accumulation", () => {
  it("child tokens accumulate to parent", async () => {
    const childTokens = new TokenUsage(100, 50, 150, 0.01);
    const runtime = makeDelegationRuntime("ok", childTokens);
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    await orch.delegate("handler", "msg", ctx);

    expect(ctx.tokenUsage.promptTokens).toBe(100);
    expect(ctx.tokenUsage.completionTokens).toBe(50);
    expect(ctx.tokenUsage.totalTokens).toBe(150);
    expect(ctx.tokenUsage.costUsd).toBeCloseTo(0.01);
  });

  it("multiple delegations sum token usage", async () => {
    const childTokens = new TokenUsage(50, 25, 75, 0.005);
    const runtime = makeDelegationRuntime("ok", childTokens);
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    await orch.delegate("handler_a", "msg1", ctx);
    await orch.delegate("handler_b", "msg2", ctx);

    expect(ctx.tokenUsage.promptTokens).toBe(100);
    expect(ctx.tokenUsage.totalTokens).toBe(150);
    expect(ctx.tokenUsage.costUsd).toBeCloseTo(0.01);
  });

  it("no tokens when runtime does not record", async () => {
    const runtime = makeDelegationRuntime("ok");
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    await orch.delegate("handler", "msg", ctx);

    expect(ctx.tokenUsage.promptTokens).toBe(0);
    expect(ctx.tokenUsage.totalTokens).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("Delegation edge cases", () => {
  it("delegate to self works until depth is hit", async () => {
    let callCount = 0;
    const orch = buildOrchestrator({ maxDelegationDepth: 5 });

    const selfRuntime: AgentRuntime = {
      invoke: async (handler, _input, ctx) => {
        callCount++;
        if (callCount < 3) {
          return orch.delegate("self_handler", "again", ctx);
        }
        return { status: "success", output: "finally done", data: {}, error: null, handler };
      },
    };

    (orch as unknown as { _runtime: AgentRuntime })._runtime = selfRuntime;

    const ctx = ExecContext.create();
    const result = await orch.delegate("self_handler", "start", ctx);

    expect(result.status).toBe("success");
    expect(callCount).toBe(3);
  });

  it("cancelled context still completes the invoke", async () => {
    const runtime = makeDelegationRuntime();
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();
    ctx.cancel();

    const result = await orch.delegate("handler", "msg", ctx);

    expect(result.status).toBe("success");
    expect(runtime.invokeCalls).toHaveLength(1);
  });

  it("empty handler name returns error immediately", async () => {
    const orch = buildOrchestrator();
    const ctx = ExecContext.create();

    const result = await orch.delegate("", "msg", ctx);

    expect(result.status).toBe("error");
    expect(result.error).toContain("must not be empty");
  });

  it("empty message is passed through to runtime", async () => {
    const runtime = makeDelegationRuntime();
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    const result = await orch.delegate("handler", "", ctx);

    expect(result.status).toBe("success");
    expect(runtime.invokeCalls[0]!.input.message).toBe("");
  });

  it("unicode message passes through cleanly", async () => {
    const msg = "Hola! \u2764\ufe0f \ud83d\ude80 \u00e4\u00f6\u00fc\u00df \u4f60\u597d";
    const runtime = makeDelegationRuntime();
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    const result = await orch.delegate("handler", msg, ctx);

    expect(result.status).toBe("success");
    expect(runtime.invokeCalls[0]!.input.message).toBe(msg);
  });

  it("child context has incremented depth", async () => {
    const runtime = makeDelegationRuntime();
    const orch = buildOrchestrator({ runtime });
    let ctx = ExecContext.create();
    ctx = ctx.child("level-1");
    ctx = ctx.child("level-2");

    await orch.delegate("handler", "msg", ctx);

    const childCtx = runtime.invokeCalls[0]!.ctx;
    expect(childCtx.depth).toBe(3);
  });

  it("child context has fresh requestId", async () => {
    const runtime = makeDelegationRuntime();
    const orch = buildOrchestrator({ runtime });
    const ctx = ExecContext.create();

    await orch.delegate("handler", "msg", ctx);

    const childCtx = runtime.invokeCalls[0]!.ctx;
    expect(childCtx.requestId).not.toBe(ctx.requestId);
  });

  it("depth limit of 1 allows root->child but blocks child->grandchild", async () => {
    const orch = buildOrchestrator({ maxDelegationDepth: 1 });
    const ctx = ExecContext.create();

    // Root (depth=0) -> child (depth=1) should succeed
    const result1 = await orch.delegate("handler", "msg", ctx);
    expect(result1.status).toBe("success");

    // child (depth=1) -> grandchild (depth=2) should fail
    const childCtx = ctx.child("level-1");
    const result2 = await orch.delegate("handler", "msg", childCtx);
    expect(result2.status).toBe("error");
    expect(result2.error).toContain("max: 1");
  });
});
