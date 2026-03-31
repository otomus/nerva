import { describe, it, expect } from "vitest";
import { NoopPolicyEngine } from "../src/policy/noop.js";
import {
  YamlPolicyEngine,
  parsePolicyConfig,
} from "../src/policy/yaml-engine.js";
import {
  ALLOW,
  DENY_NO_REASON,
  createPolicyAction,
  createPolicyDecision,
} from "../src/policy/index.js";
import { ExecContext, TokenUsage } from "../src/context.js";

function makeCtx(opts?: { userId?: string; metadata?: Record<string, string> }): ExecContext {
  const ctx = ExecContext.create({ userId: opts?.userId ?? "user-1" });
  if (opts?.metadata) {
    for (const [k, v] of Object.entries(opts.metadata)) {
      ctx.metadata[k] = v;
    }
  }
  return ctx;
}

function makeAction(target: string = "agent-x"): ReturnType<typeof createPolicyAction> {
  return createPolicyAction("invoke_agent", "user-1", target);
}

// ---------------------------------------------------------------------------
// ALLOW / DENY_NO_REASON constants
// ---------------------------------------------------------------------------

describe("Policy constants", () => {
  it("ALLOW is allowed with no reason", () => {
    expect(ALLOW.allowed).toBe(true);
    expect(ALLOW.reason).toBeNull();
    expect(ALLOW.requireApproval).toBe(false);
  });

  it("DENY_NO_REASON is denied with generic reason", () => {
    expect(DENY_NO_REASON.allowed).toBe(false);
    expect(DENY_NO_REASON.reason).toBe("denied by policy");
  });
});

// ---------------------------------------------------------------------------
// createPolicyAction / createPolicyDecision
// ---------------------------------------------------------------------------

describe("createPolicyAction", () => {
  it("creates a frozen action with defaults", () => {
    const a = createPolicyAction("route", "u", "t");
    expect(a.kind).toBe("route");
    expect(a.subject).toBe("u");
    expect(a.target).toBe("t");
    expect(a.metadata).toEqual({});
    expect(Object.isFrozen(a)).toBe(true);
  });
});

describe("createPolicyDecision", () => {
  it("creates decision with defaults", () => {
    const d = createPolicyDecision({ allowed: true });
    expect(d.allowed).toBe(true);
    expect(d.reason).toBeNull();
    expect(d.requireApproval).toBe(false);
    expect(d.approvers).toBeNull();
    expect(d.budgetRemaining).toBeNull();
  });

  it("respects overrides", () => {
    const d = createPolicyDecision({
      allowed: false,
      reason: "over budget",
      budgetRemaining: 0,
    });
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("over budget");
    expect(d.budgetRemaining).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// NoopPolicyEngine
// ---------------------------------------------------------------------------

describe("NoopPolicyEngine", () => {
  it("always returns ALLOW", async () => {
    const engine = new NoopPolicyEngine();
    const decision = await engine.evaluate(makeAction(), makeCtx());
    expect(decision).toBe(ALLOW);
    expect(decision.allowed).toBe(true);
  });

  it("record is a no-op that does not throw", async () => {
    const engine = new NoopPolicyEngine();
    await engine.record(makeAction(), ALLOW, makeCtx());
  });

  it("returns ALLOW regardless of action kind", async () => {
    const engine = new NoopPolicyEngine();
    const d1 = await engine.evaluate(createPolicyAction("route", "u", "t"), makeCtx());
    const d2 = await engine.evaluate(createPolicyAction("call_tool", "u", "t"), makeCtx());
    expect(d1.allowed).toBe(true);
    expect(d2.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// parsePolicyConfig
// ---------------------------------------------------------------------------

describe("parsePolicyConfig", () => {
  it("returns defaults for empty object", () => {
    const cfg = parsePolicyConfig({});
    expect(cfg.rateLimitMaxPerMinute).toBe(0);
    expect(cfg.budgetMaxTokensPerHour).toBe(0);
    expect(cfg.maxDepth).toBe(10);
    expect(cfg.maxToolCalls).toBe(50);
  });

  it("parses a full config dict", () => {
    const cfg = parsePolicyConfig({
      policies: {
        budget: {
          per_agent: {
            max_tokens_per_hour: 5000,
            max_cost_per_day_usd: 1.5,
            on_exceed: "pause",
          },
        },
        rate_limit: {
          per_user: {
            max_requests_per_minute: 10,
            on_exceed: "queue",
          },
        },
        approval: {
          agents: [
            { name: "deploy", requires_approval: true, approvers: ["admin"] },
          ],
        },
        execution: {
          max_depth: 3,
          max_tool_calls_per_invocation: 5,
          timeout_seconds: 15,
        },
      },
    });
    expect(cfg.budgetMaxTokensPerHour).toBe(5000);
    expect(cfg.budgetMaxCostPerDayUsd).toBe(1.5);
    expect(cfg.budgetOnExceed).toBe("pause");
    expect(cfg.rateLimitMaxPerMinute).toBe(10);
    expect(cfg.rateLimitOnExceed).toBe("queue");
    expect(cfg.approvalAgents["deploy"]).toEqual(["admin"]);
    expect(cfg.maxDepth).toBe(3);
    expect(cfg.maxToolCalls).toBe(5);
    expect(cfg.timeoutSeconds).toBe(15);
  });

  it("handles policies key that is not an object", () => {
    const cfg = parsePolicyConfig({ policies: "not an object" });
    expect(cfg.maxDepth).toBe(10); // defaults
  });

  it("handles policies key that is an array", () => {
    const cfg = parsePolicyConfig({ policies: [1, 2, 3] });
    expect(cfg.maxDepth).toBe(10);
  });

  it("ignores approval agents without requires_approval", () => {
    const cfg = parsePolicyConfig({
      policies: {
        approval: {
          agents: [
            { name: "deploy", requires_approval: false, approvers: ["admin"] },
          ],
        },
      },
    });
    expect(cfg.approvalAgents["deploy"]).toBeUndefined();
  });

  it("ignores approval agents without name", () => {
    const cfg = parsePolicyConfig({
      policies: {
        approval: {
          agents: [{ requires_approval: true, approvers: ["admin"] }],
        },
      },
    });
    expect(Object.keys(cfg.approvalAgents)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — constructor
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine constructor", () => {
  it("throws when neither configPath nor configDict is provided", () => {
    expect(() => new YamlPolicyEngine({})).toThrow(
      "Either configPath or configDict must be provided",
    );
  });

  it("throws when configPath does not exist", () => {
    expect(
      () => new YamlPolicyEngine({ configPath: "/nonexistent/path.yaml" }),
    ).toThrow("Policy config not found");
  });

  it("accepts a configDict", () => {
    const engine = new YamlPolicyEngine({ configDict: {} });
    expect(engine.config.maxDepth).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — rate limiting
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine rate limiting", () => {
  it("allows requests under the limit", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          rate_limit: { per_user: { max_requests_per_minute: 5 } },
        },
      },
    });
    const ctx = makeCtx();
    const action = makeAction();

    // Record a few allowed requests
    for (let i = 0; i < 4; i++) {
      const d = await engine.evaluate(action, ctx);
      expect(d.allowed).toBe(true);
      await engine.record(action, d, ctx);
    }
  });

  it("denies requests that exceed the limit", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          rate_limit: {
            per_user: { max_requests_per_minute: 2, on_exceed: "reject" },
          },
        },
      },
    });
    const ctx = makeCtx();
    const action = makeAction();

    // Fill the quota
    for (let i = 0; i < 2; i++) {
      const d = await engine.evaluate(action, ctx);
      await engine.record(action, d, ctx);
    }

    // Third request should be denied
    const d = await engine.evaluate(action, ctx);
    expect(d.allowed).toBe(false);
    expect(d.reason).toContain("rate limit exceeded");
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — budget
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine budget", () => {
  it("allows when token budget is not exceeded", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          budget: {
            per_agent: { max_tokens_per_hour: 10000, on_exceed: "block" },
          },
        },
      },
    });
    const ctx = makeCtx();
    ctx.recordTokens(new TokenUsage(100, 100, 200, 0));
    const d = await engine.evaluate(makeAction(), ctx);
    expect(d.allowed).toBe(true);
  });

  it("denies when token budget is exceeded after recording", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          budget: {
            per_agent: { max_tokens_per_hour: 100, on_exceed: "block" },
          },
        },
      },
    });
    const ctx = makeCtx();
    ctx.recordTokens(new TokenUsage(60, 60, 120, 0));
    const action = makeAction("agent-x");

    // Allow and record first (this records 120 tokens)
    const d1 = await engine.evaluate(action, ctx);
    expect(d1.allowed).toBe(true);
    await engine.record(action, d1, ctx);

    // Second eval should see we already used 120 > 100
    const d2 = await engine.evaluate(action, ctx);
    expect(d2.allowed).toBe(false);
    expect(d2.reason).toContain("token budget exceeded");
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — approval
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine approval", () => {
  it("requires approval for configured agents", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          approval: {
            agents: [
              { name: "deploy", requires_approval: true, approvers: ["admin", "ops"] },
            ],
          },
        },
      },
    });
    const d = await engine.evaluate(makeAction("deploy"), makeCtx());
    expect(d.allowed).toBe(true);
    expect(d.requireApproval).toBe(true);
    expect(d.approvers).toEqual(["admin", "ops"]);
  });

  it("does not require approval for unconfigured agents", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          approval: {
            agents: [
              { name: "deploy", requires_approval: true, approvers: ["admin"] },
            ],
          },
        },
      },
    });
    const d = await engine.evaluate(makeAction("other-agent"), makeCtx());
    expect(d.allowed).toBe(true);
    expect(d.requireApproval).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — execution limits
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine execution limits", () => {
  it("denies when depth exceeds max", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: { execution: { max_depth: 3 } },
      },
    });
    const ctx = makeCtx({ metadata: { depth: "4" } });
    const d = await engine.evaluate(makeAction(), ctx);
    expect(d.allowed).toBe(false);
    expect(d.reason).toContain("execution depth");
  });

  it("allows when depth is within limit", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: { execution: { max_depth: 5 } },
      },
    });
    const ctx = makeCtx({ metadata: { depth: "3" } });
    const d = await engine.evaluate(makeAction(), ctx);
    expect(d.allowed).toBe(true);
  });

  it("denies when tool_call_count exceeds max", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: { execution: { max_tool_calls_per_invocation: 2 } },
      },
    });
    const ctx = makeCtx({ metadata: { tool_call_count: "3" } });
    const d = await engine.evaluate(makeAction(), ctx);
    expect(d.allowed).toBe(false);
    expect(d.reason).toContain("tool call count");
  });

  it("treats non-digit metadata as 0", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: { execution: { max_depth: 1 } },
      },
    });
    const ctx = makeCtx({ metadata: { depth: "abc" } });
    const d = await engine.evaluate(makeAction(), ctx);
    expect(d.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// YamlPolicyEngine — record
// ---------------------------------------------------------------------------

describe("YamlPolicyEngine record", () => {
  it("does not record denied actions (no counter increment)", async () => {
    const engine = new YamlPolicyEngine({
      configDict: {
        policies: {
          rate_limit: { per_user: { max_requests_per_minute: 1 } },
        },
      },
    });
    const ctx = makeCtx();
    const action = makeAction();

    // Fill quota
    const d1 = await engine.evaluate(action, ctx);
    await engine.record(action, d1, ctx);

    // Deny
    const d2 = await engine.evaluate(action, ctx);
    expect(d2.allowed).toBe(false);
    // Record the denial — should NOT increment counters
    await engine.record(action, d2, ctx);

    // Still denied (not double-counted)
    const d3 = await engine.evaluate(action, ctx);
    expect(d3.allowed).toBe(false);
  });
});
