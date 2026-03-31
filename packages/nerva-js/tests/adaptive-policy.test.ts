import { describe, it, expect, beforeEach } from "vitest";
import {
  AdaptivePolicyEngine,
  createAdaptivePolicyConfig,
  COST_DISABLED,
  REASON_BUDGET_EXCEEDED,
  REASON_THROTTLED,
} from "../src/policy/adaptive.js";
import type { AdaptivePolicyConfig } from "../src/policy/adaptive.js";
import {
  agentPolicy,
  getAgentPolicy,
  resolvePolicy,
  clearRegistry,
} from "../src/policy/decorator.js";
import type { AgentPolicyConfig } from "../src/policy/decorator.js";
import {
  createPolicyAction,
  createPolicyDecision,
  ALLOW,
  DENY_NO_REASON,
} from "../src/policy/index.js";
import type {
  PolicyEngine,
  PolicyAction,
  PolicyDecision,
  ExecContext,
} from "../src/policy/index.js";
import { TokenUsage } from "../src/context.js";
import { ExecContext as ExecContextClass } from "../src/context.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCtx(opts?: {
  costUsd?: number;
  metadata?: Record<string, string>;
}): ExecContext {
  const ctx = ExecContextClass.create({ userId: "test-user" });
  if (opts?.costUsd !== undefined) {
    ctx.tokenUsage = new TokenUsage(0, 0, 0, opts.costUsd);
  }
  if (opts?.metadata) {
    for (const [k, v] of Object.entries(opts.metadata)) {
      ctx.metadata[k] = v;
    }
  }
  return ctx;
}

function makeAction(target: string = "agent-x"): PolicyAction {
  return createPolicyAction("invoke_agent", "user-1", target);
}

/**
 * Stub policy engine that always allows.
 */
class AllowEngine implements PolicyEngine {
  readonly recorded: Array<{
    action: PolicyAction;
    decision: PolicyDecision;
  }> = [];

  async evaluate(
    _action: PolicyAction,
    _ctx: ExecContext,
  ): Promise<PolicyDecision> {
    return ALLOW;
  }

  async record(
    action: PolicyAction,
    decision: PolicyDecision,
    _ctx: ExecContext,
  ): Promise<void> {
    this.recorded.push({ action, decision });
  }
}

/**
 * Stub policy engine that always denies.
 */
class DenyEngine implements PolicyEngine {
  async evaluate(
    _action: PolicyAction,
    _ctx: ExecContext,
  ): Promise<PolicyDecision> {
    return DENY_NO_REASON;
  }

  async record(
    _action: PolicyAction,
    _decision: PolicyDecision,
    _ctx: ExecContext,
  ): Promise<void> {}
}

// ---------------------------------------------------------------------------
// createAdaptivePolicyConfig
// ---------------------------------------------------------------------------

describe("createAdaptivePolicyConfig", () => {
  it("creates config with sensible defaults", () => {
    const config = createAdaptivePolicyConfig();
    expect(config.baseTimeoutSeconds).toBe(30.0);
    expect(config.extendTimeoutOn.size).toBe(0);
    expect(config.timeoutExtensionFactor).toBe(2.0);
    expect(config.throttleAfterCost).toBe(COST_DISABLED);
    expect(config.pauseAfterCost).toBe(COST_DISABLED);
    expect(config.throttleModelDowngrade).toBe("");
  });

  it("applies overrides selectively", () => {
    const config = createAdaptivePolicyConfig({
      baseTimeoutSeconds: 60,
      throttleAfterCost: 5.0,
    });
    expect(config.baseTimeoutSeconds).toBe(60);
    expect(config.throttleAfterCost).toBe(5.0);
    // Defaults for unspecified fields
    expect(config.timeoutExtensionFactor).toBe(2.0);
    expect(config.pauseAfterCost).toBe(COST_DISABLED);
  });
});

// ---------------------------------------------------------------------------
// AdaptivePolicyEngine — wrapping base engine
// ---------------------------------------------------------------------------

describe("AdaptivePolicyEngine", () => {
  it("wraps a base engine and exposes it via .base", () => {
    const base = new AllowEngine();
    const config = createAdaptivePolicyConfig();
    const engine = new AdaptivePolicyEngine(base, config);

    expect(engine.base).toBe(base);
    expect(engine.config).toBe(config);
  });

  it("passes through base ALLOW when no adaptive conditions fire", async () => {
    const base = new AllowEngine();
    const config = createAdaptivePolicyConfig();
    const engine = new AdaptivePolicyEngine(base, config);

    const decision = await engine.evaluate(makeAction(), makeCtx());
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBeNull();
  });

  it("never overrides a base DENY", async () => {
    const base = new DenyEngine();
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 100,
      pauseAfterCost: 200,
    });
    const engine = new AdaptivePolicyEngine(base, config);

    // Even with zero cost (well under thresholds), base denial is returned
    const decision = await engine.evaluate(
      makeAction(),
      makeCtx({ costUsd: 0 }),
    );
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe("denied by policy");
  });

  it("delegates record() to base engine", async () => {
    const base = new AllowEngine();
    const config = createAdaptivePolicyConfig();
    const engine = new AdaptivePolicyEngine(base, config);

    const action = makeAction();
    const decision = ALLOW;
    const ctx = makeCtx();

    await engine.record(action, decision, ctx);
    expect(base.recorded).toHaveLength(1);
    expect(base.recorded[0].action).toBe(action);
  });
});

// ---------------------------------------------------------------------------
// Timeout extension
// ---------------------------------------------------------------------------

describe("AdaptivePolicyEngine — timeout extension", () => {
  it("extends timeout when context has matching tag", () => {
    const config = createAdaptivePolicyConfig({
      baseTimeoutSeconds: 30,
      extendTimeoutOn: new Set(["long_running"]),
      timeoutExtensionFactor: 3.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ metadata: { long_running: "true" } });
    expect(engine.shouldExtendTimeout(ctx)).toBe(true);
    expect(engine.getExtendedTimeout()).toBe(90.0);
  });

  it("does not extend timeout when no matching tag", () => {
    const config = createAdaptivePolicyConfig({
      extendTimeoutOn: new Set(["long_running"]),
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ metadata: { short_task: "yes" } });
    expect(engine.shouldExtendTimeout(ctx)).toBe(false);
  });

  it("does not extend when extendTimeoutOn is empty", () => {
    const config = createAdaptivePolicyConfig({
      extendTimeoutOn: new Set(),
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ metadata: { anything: "at all" } });
    expect(engine.shouldExtendTimeout(ctx)).toBe(false);
  });

  it("extends when any one of multiple tags matches", () => {
    const config = createAdaptivePolicyConfig({
      extendTimeoutOn: new Set(["batch", "export", "training"]),
      baseTimeoutSeconds: 10,
      timeoutExtensionFactor: 5.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ metadata: { export: "csv" } });
    expect(engine.shouldExtendTimeout(ctx)).toBe(true);
    expect(engine.getExtendedTimeout()).toBe(50.0);
  });

  it("returns base * factor even if factor is 1.0 (no actual extension)", () => {
    const config = createAdaptivePolicyConfig({
      baseTimeoutSeconds: 20,
      timeoutExtensionFactor: 1.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);
    expect(engine.getExtendedTimeout()).toBe(20.0);
  });
});

// ---------------------------------------------------------------------------
// Cost-based throttling
// ---------------------------------------------------------------------------

describe("AdaptivePolicyEngine — throttling", () => {
  it("throttles when cost exceeds throttleAfterCost", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 5.0,
      pauseAfterCost: 0, // no pause
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 5.5 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe(REASON_THROTTLED);
  });

  it("includes budgetRemaining when both throttle and pause set", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 5.0,
      pauseAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 6.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe(REASON_THROTTLED);
    expect(decision.budgetRemaining).toBeCloseTo(4.0);
  });

  it("budgetRemaining is null when no pause threshold set", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 1.0,
      pauseAfterCost: 0, // disabled
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 2.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
    expect(decision.budgetRemaining).toBeNull();
  });

  it("does not throttle when cost is below threshold", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 3.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBeNull(); // passthrough from base ALLOW
  });

  it("does not throttle when throttleAfterCost is disabled (0)", () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: COST_DISABLED,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 999.0 });
    expect(engine.shouldThrottle(ctx)).toBe(false);
  });

  it("cost exactly at threshold triggers throttle", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 5.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 5.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBe(REASON_THROTTLED);
  });
});

// ---------------------------------------------------------------------------
// Cost-based pausing (hard stop)
// ---------------------------------------------------------------------------

describe("AdaptivePolicyEngine — pausing (budget limit)", () => {
  it("denies when cost exceeds pauseAfterCost", async () => {
    const config = createAdaptivePolicyConfig({
      pauseAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 12.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe(REASON_BUDGET_EXCEEDED);
    expect(decision.budgetRemaining).toBe(0.0);
  });

  it("cost exactly at pause threshold triggers pause", async () => {
    const config = createAdaptivePolicyConfig({
      pauseAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 10.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe(REASON_BUDGET_EXCEEDED);
  });

  it("does not pause when cost below threshold", async () => {
    const config = createAdaptivePolicyConfig({
      pauseAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 9.99 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(true);
  });

  it("does not pause when pauseAfterCost is disabled (0)", () => {
    const config = createAdaptivePolicyConfig({
      pauseAfterCost: COST_DISABLED,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 999.0 });
    expect(engine.shouldPause(ctx)).toBe(false);
  });

  it("pause takes priority over throttle", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 5.0,
      pauseAfterCost: 10.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    // Cost exceeds both thresholds — pause wins
    const ctx = makeCtx({ costUsd: 15.0 });
    const decision = await engine.evaluate(makeAction(), ctx);

    expect(decision.allowed).toBe(false);
    expect(decision.reason).toBe(REASON_BUDGET_EXCEEDED);
  });

  it("zero budget means disabled, not immediate pause", async () => {
    const config = createAdaptivePolicyConfig({
      pauseAfterCost: 0.0, // COST_DISABLED
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 0 });
    const decision = await engine.evaluate(makeAction(), ctx);
    expect(decision.allowed).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("AdaptivePolicyEngine — edge cases", () => {
  it("negative cost does not trigger throttle or pause", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: 1.0,
      pauseAfterCost: 5.0,
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: -1.0 });
    const decision = await engine.evaluate(makeAction(), ctx);
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBeNull();
  });

  it("no adaptive behavior when all thresholds disabled", async () => {
    const config = createAdaptivePolicyConfig({
      throttleAfterCost: COST_DISABLED,
      pauseAfterCost: COST_DISABLED,
      extendTimeoutOn: new Set(),
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);

    const ctx = makeCtx({ costUsd: 9999, metadata: { anything: "val" } });
    const decision = await engine.evaluate(makeAction(), ctx);
    expect(decision.allowed).toBe(true);
    expect(decision.reason).toBeNull();
    expect(engine.shouldExtendTimeout(ctx)).toBe(false);
    expect(engine.shouldThrottle(ctx)).toBe(false);
    expect(engine.shouldPause(ctx)).toBe(false);
  });

  it("context with empty metadata — no timeout extension", () => {
    const config = createAdaptivePolicyConfig({
      extendTimeoutOn: new Set(["tag"]),
    });
    const engine = new AdaptivePolicyEngine(new AllowEngine(), config);
    const ctx = makeCtx();
    expect(engine.shouldExtendTimeout(ctx)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Decorator — agentPolicy / getAgentPolicy / resolvePolicy / clearRegistry
// ---------------------------------------------------------------------------

describe("agentPolicy decorator", () => {
  beforeEach(() => {
    clearRegistry();
  });

  it("registers config and returns the target unchanged", () => {
    class MyAgent {}
    const decorated = agentPolicy("my-agent", {
      requiresApproval: true,
      timeoutSeconds: 120,
    })(MyAgent);

    expect(decorated).toBe(MyAgent);
  });

  it("getAgentPolicy retrieves registered config", () => {
    agentPolicy("deploy", { requiresApproval: true, maxToolCalls: 5 })(
      class {},
    );
    const config = getAgentPolicy("deploy");
    expect(config).not.toBeNull();
    expect(config?.requiresApproval).toBe(true);
    expect(config?.maxToolCalls).toBe(5);
  });

  it("getAgentPolicy returns null for unregistered agent", () => {
    expect(getAgentPolicy("nonexistent")).toBeNull();
  });

  it("throws on empty agent name", () => {
    expect(() => agentPolicy("", { timeoutSeconds: 10 })).toThrow(
      "agent name must be a non-empty string",
    );
  });

  it("clearRegistry removes all entries", () => {
    agentPolicy("a", { timeoutSeconds: 10 })(class {});
    agentPolicy("b", { maxCostUsd: 5 })(class {});

    expect(getAgentPolicy("a")).not.toBeNull();
    clearRegistry();
    expect(getAgentPolicy("a")).toBeNull();
    expect(getAgentPolicy("b")).toBeNull();
  });

  it("duplicate registration overwrites previous config", () => {
    agentPolicy("agent", { timeoutSeconds: 10 })(class {});
    agentPolicy("agent", { timeoutSeconds: 99 })(class {});

    const config = getAgentPolicy("agent");
    expect(config?.timeoutSeconds).toBe(99);
  });

  it("empty config object registers without error", () => {
    agentPolicy("empty-config", {})(class {});
    const config = getAgentPolicy("empty-config");
    expect(config).not.toBeNull();
  });

  it("default config (no second arg) registers empty overrides", () => {
    agentPolicy("default-config")(class {});
    const config = getAgentPolicy("default-config");
    expect(config).not.toBeNull();
  });

  it("ignores unknown keys in config", () => {
    const rawConfig = {
      requiresApproval: true,
      unknownField: "should be ignored",
    } as AgentPolicyConfig;

    agentPolicy("strict", rawConfig)(class {});
    const config = getAgentPolicy("strict");
    expect(config?.requiresApproval).toBe(true);
    expect((config as Record<string, unknown>)?.["unknownField"]).toBeUndefined();
  });

  it("preserves approvers array", () => {
    agentPolicy("with-approvers", {
      approvers: ["alice", "bob"],
    })(class {});

    const config = getAgentPolicy("with-approvers");
    expect(config?.approvers).toEqual(["alice", "bob"]);
  });
});

// ---------------------------------------------------------------------------
// resolvePolicy — merge YAML defaults with decorator overrides
// ---------------------------------------------------------------------------

describe("resolvePolicy", () => {
  beforeEach(() => {
    clearRegistry();
  });

  it("returns YAML config as-is when no decorator registered", () => {
    const yaml = { timeoutSeconds: 30, maxToolCalls: 10 };
    const result = resolvePolicy(yaml, "no-decorator");

    expect(result).toEqual(yaml);
    // Should be a new object, not the same reference
    expect(result).not.toBe(yaml);
  });

  it("decorator overrides win over YAML defaults", () => {
    agentPolicy("agent", { timeoutSeconds: 120, maxToolCalls: 3 })(class {});

    const yaml = { timeoutSeconds: 30, maxToolCalls: 10, maxCostUsd: 5.0 };
    const result = resolvePolicy(yaml, "agent");

    expect(result["timeoutSeconds"]).toBe(120);
    expect(result["maxToolCalls"]).toBe(3);
    // Non-overridden YAML value preserved
    expect(result["maxCostUsd"]).toBe(5.0);
  });

  it("undefined override fields do not clobber YAML values", () => {
    agentPolicy("partial", { requiresApproval: true })(class {});

    const yaml = { timeoutSeconds: 30, maxToolCalls: 10 };
    const result = resolvePolicy(yaml, "partial");

    expect(result["requiresApproval"]).toBe(true);
    expect(result["timeoutSeconds"]).toBe(30);
    expect(result["maxToolCalls"]).toBe(10);
  });

  it("works with empty YAML config", () => {
    agentPolicy("agent", { timeoutSeconds: 60 })(class {});
    const result = resolvePolicy({}, "agent");

    expect(result["timeoutSeconds"]).toBe(60);
  });

  it("works with empty decorator config", () => {
    agentPolicy("agent", {})(class {});
    const yaml = { timeoutSeconds: 30, maxToolCalls: 10 };
    const result = resolvePolicy(yaml, "agent");

    // Nothing overridden
    expect(result).toEqual(yaml);
  });

  it("handles YAML config with extra keys not in override fields", () => {
    agentPolicy("agent", { timeoutSeconds: 60 })(class {});

    const yaml = { timeoutSeconds: 30, customField: "preserved" };
    const result = resolvePolicy(yaml, "agent");

    expect(result["timeoutSeconds"]).toBe(60);
    expect(result["customField"]).toBe("preserved");
  });

  it("boolean override false correctly overrides true", () => {
    agentPolicy("agent", { requiresApproval: false })(class {});

    const yaml = { requiresApproval: true };
    const result = resolvePolicy(yaml, "agent");

    expect(result["requiresApproval"]).toBe(false);
  });

  it("zero numeric override is applied (not treated as undefined)", () => {
    agentPolicy("agent", { maxCostUsd: 0 })(class {});

    const yaml = { maxCostUsd: 100 };
    const result = resolvePolicy(yaml, "agent");

    expect(result["maxCostUsd"]).toBe(0);
  });
});
