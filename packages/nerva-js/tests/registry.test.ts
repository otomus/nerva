import { describe, it, expect } from "vitest";
import { InMemoryRegistry } from "../src/registry/inmemory.js";
import {
  ComponentKind,
  HealthStatus,
  InvocationStats,
  createRegistryEntry,
  DURATION_SMOOTHING_FACTOR,
} from "../src/registry/index.js";
import { ExecContext, createPermissions } from "../src/context.js";

function makeCtx(roles: string[] = []): ExecContext {
  return ExecContext.create({
    permissions: createPermissions({ roles: new Set(roles) }),
  });
}

function agentEntry(name: string, overrides?: Parameters<typeof createRegistryEntry>[3]) {
  return createRegistryEntry(name, ComponentKind.AGENT, `Agent ${name}`, overrides);
}

// ---------------------------------------------------------------------------
// InMemoryRegistry — register / resolve
// ---------------------------------------------------------------------------

describe("InMemoryRegistry register/resolve", () => {
  it("registers and resolves an entry", async () => {
    const reg = new InMemoryRegistry();
    const entry = agentEntry("a");
    await reg.register(entry, makeCtx());
    const found = await reg.resolve("a", makeCtx());
    expect(found).toBe(entry);
  });

  it("overwrites existing entry on re-register", async () => {
    const reg = new InMemoryRegistry();
    const entry1 = agentEntry("a");
    const entry2 = agentEntry("a");
    await reg.register(entry1, makeCtx());
    await reg.register(entry2, makeCtx());
    const found = await reg.resolve("a", makeCtx());
    expect(found).toBe(entry2);
  });

  it("resolve returns null for unknown name", async () => {
    const reg = new InMemoryRegistry();
    const found = await reg.resolve("nonexistent", makeCtx());
    expect(found).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// InMemoryRegistry — discover
// ---------------------------------------------------------------------------

describe("InMemoryRegistry discover", () => {
  it("filters by kind", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.register(
      createRegistryEntry("t", ComponentKind.TOOL, "Tool t"),
      makeCtx(),
    );
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents).toHaveLength(1);
    expect(agents[0]!.name).toBe("a");
  });

  it("excludes disabled entries", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a", { enabled: false }), makeCtx());
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents).toHaveLength(0);
  });

  it("excludes UNAVAILABLE entries", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a", { health: HealthStatus.UNAVAILABLE }), makeCtx());
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents).toHaveLength(0);
  });

  it("includes DEGRADED entries", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a", { health: HealthStatus.DEGRADED }), makeCtx());
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents).toHaveLength(1);
  });

  it("filters by permissions — requires matching role", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("admin-agent", { permissions: ["admin"] }), makeCtx());

    const noRole = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(noRole).toHaveLength(0);

    const withRole = await reg.discover(ComponentKind.AGENT, makeCtx(["admin"]));
    expect(withRole).toHaveLength(1);
  });

  it("includes entries with empty permissions for any caller", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("open-agent"), makeCtx());
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents).toHaveLength(1);
  });

  it("returns results sorted by name", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("c"), makeCtx());
    await reg.register(agentEntry("a"), makeCtx());
    await reg.register(agentEntry("b"), makeCtx());
    const agents = await reg.discover(ComponentKind.AGENT, makeCtx());
    expect(agents.map((e) => e.name)).toEqual(["a", "b", "c"]);
  });
});

// ---------------------------------------------------------------------------
// InMemoryRegistry — health
// ---------------------------------------------------------------------------

describe("InMemoryRegistry health", () => {
  it("returns health status of a registered component", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    const h = await reg.health("a");
    expect(h).toBe(HealthStatus.HEALTHY);
  });

  it("throws for unknown component", async () => {
    const reg = new InMemoryRegistry();
    await expect(reg.health("nonexistent")).rejects.toThrow("Component not found");
  });
});

// ---------------------------------------------------------------------------
// InMemoryRegistry — update
// ---------------------------------------------------------------------------

describe("InMemoryRegistry update", () => {
  it("patches description", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.update("a", { description: "new desc" });
    const entry = await reg.resolve("a", makeCtx());
    expect(entry!.description).toBe("new desc");
  });

  it("patches health", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.update("a", { health: HealthStatus.DEGRADED });
    const h = await reg.health("a");
    expect(h).toBe(HealthStatus.DEGRADED);
  });

  it("patches enabled flag", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.update("a", { enabled: false });
    const entry = await reg.resolve("a", makeCtx());
    expect(entry!.enabled).toBe(false);
  });

  it("patches multiple fields at once", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.update("a", {
      description: "updated",
      health: HealthStatus.UNAVAILABLE,
      enabled: false,
    });
    const entry = await reg.resolve("a", makeCtx());
    expect(entry!.description).toBe("updated");
    expect(entry!.health).toBe(HealthStatus.UNAVAILABLE);
    expect(entry!.enabled).toBe(false);
  });

  it("ignores undefined fields in patch", async () => {
    const reg = new InMemoryRegistry();
    await reg.register(agentEntry("a"), makeCtx());
    await reg.update("a", { description: undefined });
    const entry = await reg.resolve("a", makeCtx());
    expect(entry!.description).toBe("Agent a");
  });

  it("throws for unknown component", async () => {
    const reg = new InMemoryRegistry();
    await expect(reg.update("nonexistent", { enabled: false })).rejects.toThrow(
      "Component not found",
    );
  });
});

// ---------------------------------------------------------------------------
// InvocationStats
// ---------------------------------------------------------------------------

describe("InvocationStats", () => {
  it("starts with all-zero counters", () => {
    const stats = new InvocationStats();
    expect(stats.totalCalls).toBe(0);
    expect(stats.successes).toBe(0);
    expect(stats.failures).toBe(0);
    expect(stats.lastInvokedAt).toBeNull();
    expect(stats.avgDurationMs).toBe(0);
  });

  it("recordSuccess increments counters", () => {
    const stats = new InvocationStats();
    stats.recordSuccess(100);
    expect(stats.totalCalls).toBe(1);
    expect(stats.successes).toBe(1);
    expect(stats.failures).toBe(0);
    expect(stats.lastInvokedAt).not.toBeNull();
    expect(stats.avgDurationMs).toBe(100);
  });

  it("recordFailure increments failure counter", () => {
    const stats = new InvocationStats();
    stats.recordFailure(50);
    expect(stats.totalCalls).toBe(1);
    expect(stats.successes).toBe(0);
    expect(stats.failures).toBe(1);
  });

  it("avgDurationMs uses EMA after first call", () => {
    const stats = new InvocationStats();
    stats.recordSuccess(100); // first: avg = 100
    stats.recordSuccess(200); // EMA: 0.2*200 + 0.8*100 = 120
    expect(stats.avgDurationMs).toBeCloseTo(120, 1);
  });
});

// ---------------------------------------------------------------------------
// createRegistryEntry
// ---------------------------------------------------------------------------

describe("createRegistryEntry", () => {
  it("creates entry with defaults", () => {
    const entry = createRegistryEntry("x", ComponentKind.AGENT, "test");
    expect(entry.name).toBe("x");
    expect(entry.kind).toBe(ComponentKind.AGENT);
    expect(entry.description).toBe("test");
    expect(entry.health).toBe(HealthStatus.HEALTHY);
    expect(entry.enabled).toBe(true);
    expect(entry.schema).toBeNull();
    expect(entry.permissions).toEqual([]);
    expect(entry.requirements).toEqual([]);
  });

  it("respects overrides", () => {
    const entry = createRegistryEntry("x", ComponentKind.TOOL, "test", {
      health: HealthStatus.DEGRADED,
      permissions: ["admin"],
    });
    expect(entry.health).toBe(HealthStatus.DEGRADED);
    expect(entry.permissions).toEqual(["admin"]);
  });
});
