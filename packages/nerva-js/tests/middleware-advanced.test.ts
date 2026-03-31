import { describe, it, expect } from "vitest";
import {
  Orchestrator,
  DEFAULT_MIDDLEWARE_PRIORITY,
} from "../src/orchestrator.js";
import { ExecContext, TokenUsage, createPermissions } from "../src/context.js";
import { RuleRouter } from "../src/router/rule.js";
import { requestLogger, permissionChecker, usageTracker } from "../src/middleware/builtins.js";
import type {
  AgentResult,
  AgentInput,
  AgentRuntime,
  Responder,
  Response,
  Channel,
  MiddlewareHandler,
  MiddlewareErrorHandler,
} from "../src/orchestrator.js";

// ---------------------------------------------------------------------------
// Mock primitives
// ---------------------------------------------------------------------------

function makeRuntime(output = "ok"): AgentRuntime {
  return {
    invoke: async (handler, _input, _ctx): Promise<AgentResult> => ({
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

function makeRouter() {
  return new RuleRouter(
    [{ pattern: ".*", handler: "catch-all", intent: "any" }],
    null,
  );
}

function makeOrch(): Orchestrator {
  return new Orchestrator({
    router: makeRouter(),
    runtime: makeRuntime(),
    responder: makeResponder(),
  });
}

// ---------------------------------------------------------------------------
// Priority ordering (N-641)
// ---------------------------------------------------------------------------

describe("Middleware priority ordering", () => {
  it("lower priority runs first", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push("low"); return undefined; }, 200);
    orch.use("before_route", async () => { order.push("high"); return undefined; }, 10);

    await orch.handle("test");

    expect(order).toEqual(["high", "low"]);
  });

  it("equal priority preserves registration order", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push("first"); return undefined; }, 50);
    orch.use("before_route", async () => { order.push("second"); return undefined; }, 50);

    await orch.handle("test");

    expect(order).toEqual(["first", "second"]);
  });

  it("default priority is 100", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push("default"); return undefined; });
    orch.use("before_route", async () => { order.push("early"); return undefined; }, 50);

    await orch.handle("test");

    expect(order).toEqual(["early", "default"]);
  });

  it("three priorities in correct order", async () => {
    const order: number[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push(200); return undefined; }, 200);
    orch.use("before_route", async () => { order.push(1); return undefined; }, 1);
    orch.use("before_route", async () => { order.push(50); return undefined; }, 50);

    await orch.handle("test");

    expect(order).toEqual([1, 50, 200]);
  });

  it("negative priority runs first", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push("positive"); return undefined; }, 100);
    orch.use("before_route", async () => { order.push("negative"); return undefined; }, -10);

    await orch.handle("test");

    expect(order).toEqual(["negative", "positive"]);
  });
});

// ---------------------------------------------------------------------------
// Decorator-style registration (N-640)
// ---------------------------------------------------------------------------

describe("Decorator-style registration", () => {
  it("beforeRoute registers middleware", async () => {
    let called = false;
    const orch = makeOrch();

    orch.beforeRoute(async () => { called = true; return undefined; });

    await orch.handle("test");
    expect(called).toBe(true);
  });

  it("beforeInvoke registers with priority", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.beforeInvoke(async () => { order.push("late"); return undefined; }, 200);
    orch.beforeInvoke(async () => { order.push("early"); return undefined; }, 10);

    await orch.handle("test");
    expect(order).toEqual(["early", "late"]);
  });

  it("afterInvoke receives AgentResult", async () => {
    const payloads: unknown[] = [];
    const orch = makeOrch();

    orch.afterInvoke(async (_ctx, payload) => { payloads.push(payload); return undefined; });

    await orch.handle("test");

    expect(payloads).toHaveLength(1);
    expect((payloads[0] as AgentResult).status).toBe("success");
  });

  it("beforeRespond receives Response", async () => {
    const payloads: unknown[] = [];
    const orch = makeOrch();

    orch.beforeRespond(async (_ctx, payload) => { payloads.push(payload); return undefined; });

    await orch.handle("test");

    expect(payloads).toHaveLength(1);
    expect((payloads[0] as Response).text).toBe("ok");
  });
});

// ---------------------------------------------------------------------------
// Error handling (N-642)
// ---------------------------------------------------------------------------

describe("Middleware error handling", () => {
  it("error in middleware does not crash request", async () => {
    const orch = makeOrch();

    orch.use("before_route", async () => { throw new Error("boom"); });

    const resp = await orch.handle("test");
    expect(resp.text).toBe("ok");
  });

  it("error skips remaining middleware in stage", async () => {
    const order: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { order.push("first"); return undefined; }, 10);
    orch.use("before_route", async () => { order.push("bad"); throw new Error("boom"); }, 20);
    orch.use("before_route", async () => { order.push("third"); return undefined; }, 30);

    await orch.handle("test");

    expect(order).toEqual(["first", "bad"]);
  });

  it("error emits middleware.error event", async () => {
    const orch = makeOrch();
    orch.use("before_route", async () => { throw new TypeError("type error"); });

    const ctx = ExecContext.create();
    await orch.handle("test", ctx);

    const errorEvents = ctx.events.filter((e) => e.name === "middleware.error");
    expect(errorEvents).toHaveLength(1);
    expect(errorEvents[0]!.attributes["stage"]).toBe("before_route");
    expect(errorEvents[0]!.attributes["error"]).toContain("type error");
    expect(errorEvents[0]!.attributes["error_type"]).toBe("TypeError");
  });

  it("onError handler is called", async () => {
    const errors: Array<{ error: unknown; stage: string }> = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { throw new Error("oops"); });
    orch.onError(async (err, stage) => { errors.push({ error: err, stage }); });

    await orch.handle("test");

    expect(errors).toHaveLength(1);
    expect(errors[0]!.stage).toBe("before_route");
  });

  it("error handler failure does not crash request", async () => {
    const orch = makeOrch();

    orch.use("before_route", async () => { throw new Error("middleware error"); });
    orch.onError(async () => { throw new Error("error handler also failed"); });

    const resp = await orch.handle("test");
    expect(resp.text).toBe("ok");
  });

  it("other stages continue after error in one stage", async () => {
    const stages: string[] = [];
    const orch = makeOrch();

    orch.use("before_route", async () => { throw new Error("route error"); });
    orch.use("before_invoke", async () => { stages.push("before_invoke"); return undefined; });

    await orch.handle("test");

    expect(stages).toContain("before_invoke");
  });
});

// ---------------------------------------------------------------------------
// Built-in middleware (N-643)
// ---------------------------------------------------------------------------

describe("Built-in middleware", () => {
  it("requestLogger handlers return undefined (passthrough)", async () => {
    const logs: string[] = [];
    const { beforeRoute, afterInvoke } = requestLogger((msg) => logs.push(msg));

    const ctx = ExecContext.create();
    const result1 = await beforeRoute(ctx, "test message");
    expect(result1).toBeUndefined();

    const agentResult: AgentResult = { status: "success", output: "ok", handler: "test" };
    const result2 = await afterInvoke(ctx, agentResult);
    expect(result2).toBeUndefined();

    expect(logs).toHaveLength(2);
  });

  it("permissionChecker allows correct roles", async () => {
    const checker = permissionChecker(new Set(["admin"]));
    const ctx = ExecContext.create({
      permissions: createPermissions({ roles: new Set(["admin", "user"]) }),
    });

    const result = await checker(ctx, { message: "test" });
    expect(result).toBeUndefined();
  });

  it("permissionChecker denies missing role", async () => {
    const checker = permissionChecker(new Set(["admin"]));
    const ctx = ExecContext.create({
      permissions: createPermissions({ roles: new Set(["user"]) }),
    });

    const result = await checker(ctx, { message: "test" });
    expect(result).not.toBeUndefined();
    expect((result as AgentInput).message).toContain("Permission denied");
  });

  it("permissionChecker emits event on denial", async () => {
    const checker = permissionChecker(new Set(["superadmin"]));
    const ctx = ExecContext.create();

    await checker(ctx, { message: "test" });

    const denied = ctx.events.filter((e) => e.name === "permission.denied");
    expect(denied).toHaveLength(1);
    expect(denied[0]!.attributes["missing_role"]).toBe("superadmin");
  });

  it("permissionChecker with null roles is noop", async () => {
    const checker = permissionChecker(null);
    const ctx = ExecContext.create();

    const result = await checker(ctx, { message: "test" });
    expect(result).toBeUndefined();
  });

  it("usageTracker emits usage event", async () => {
    const tracker = usageTracker();
    const ctx = ExecContext.create();
    ctx.recordTokens(new TokenUsage(100, 50, 150));

    await tracker(ctx, {});

    const usage = ctx.events.filter((e) => e.name === "usage.recorded");
    expect(usage).toHaveLength(1);
    expect(usage[0]!.attributes["prompt_tokens"]).toBe("100");
    expect(usage[0]!.attributes["completion_tokens"]).toBe("50");
    expect(usage[0]!.attributes["total_tokens"]).toBe("150");
  });

  it("usageTracker with zero tokens", async () => {
    const tracker = usageTracker();
    const ctx = ExecContext.create();

    await tracker(ctx, {});

    const usage = ctx.events.filter((e) => e.name === "usage.recorded");
    expect(usage).toHaveLength(1);
    expect(usage[0]!.attributes["total_tokens"]).toBe("0");
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("Middleware edge cases", () => {
  it("middleware returning undefined is passthrough", async () => {
    const orch = makeOrch();
    orch.use("before_route", async () => undefined);

    const resp = await orch.handle("original");
    expect(resp.text).toBe("ok");
  });

  it("middleware can modify payload", async () => {
    const inputs: AgentInput[] = [];
    const runtime: AgentRuntime = {
      invoke: async (handler, input, _ctx) => {
        inputs.push(input);
        return { status: "success", output: "ok", handler, data: {} };
      },
    };

    const orch = new Orchestrator({
      router: makeRouter(),
      runtime,
      responder: makeResponder(),
    });

    orch.use("before_invoke", async (_ctx, _payload) => {
      return { message: "modified" } satisfies AgentInput;
    });

    await orch.handle("original");

    expect(inputs[0]!.message).toBe("modified");
  });

  it("no middleware registered works fine", async () => {
    const orch = makeOrch();
    const resp = await orch.handle("test");
    expect(resp.text).toBe("ok");
  });

  it("10 middleware on same stage all execute in priority order", async () => {
    const order: number[] = [];
    const orch = makeOrch();

    for (let i = 0; i < 10; i++) {
      const priority = (10 - i) * 10;
      const p = priority;
      orch.use("before_route", async () => { order.push(p); return undefined; }, priority);
    }

    await orch.handle("test");

    const sorted = [...order].sort((a, b) => a - b);
    expect(order).toEqual(sorted);
  });
});
