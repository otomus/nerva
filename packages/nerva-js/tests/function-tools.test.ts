import { describe, it, expect } from "vitest";
import { FunctionToolManager } from "../src/tools/function.js";
import { ToolStatus, createToolSpec, createToolResult } from "../src/tools/index.js";
import { ExecContext, createPermissions } from "../src/context.js";

function makeCtx(opts?: {
  allowedTools?: Set<string> | null;
  roles?: Set<string>;
}): ExecContext {
  return ExecContext.create({
    permissions: createPermissions({
      allowedTools: opts?.allowedTools ?? null,
      roles: opts?.roles ?? new Set(),
    }),
  });
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

describe("FunctionToolManager registration", () => {
  it("registers a sync function as a tool", () => {
    const mgr = new FunctionToolManager();
    mgr.tool("add", "Add two numbers", undefined, (args) => {
      return (args["a"] as number) + (args["b"] as number);
    });
    // No throw means success
  });

  it("throws on duplicate registration", () => {
    const mgr = new FunctionToolManager();
    mgr.tool("add", "Add", undefined, () => 0);
    expect(() => mgr.tool("add", "Add again", undefined, () => 0)).toThrow(
      "Tool 'add' is already registered",
    );
  });
});

// ---------------------------------------------------------------------------
// discover()
// ---------------------------------------------------------------------------

describe("FunctionToolManager.discover", () => {
  it("returns all tools when permissions are unrestricted", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("a", "tool a", undefined, () => 1);
    mgr.tool("b", "tool b", undefined, () => 2);
    const ctx = makeCtx();
    const specs = await mgr.discover(ctx);
    expect(specs).toHaveLength(2);
  });

  it("filters by allowedTools", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("a", "tool a", undefined, () => 1);
    mgr.tool("b", "tool b", undefined, () => 2);
    const ctx = makeCtx({ allowedTools: new Set(["a"]) });
    const specs = await mgr.discover(ctx);
    expect(specs).toHaveLength(1);
    expect(specs[0]!.name).toBe("a");
  });

  it("filters by requiredPermissions (roles)", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("admin-tool", "admin only", { requiredPermissions: new Set(["admin"]) }, () => 1);
    mgr.tool("open-tool", "open", undefined, () => 2);

    const noRole = makeCtx({ roles: new Set() });
    const specs1 = await mgr.discover(noRole);
    expect(specs1).toHaveLength(1);
    expect(specs1[0]!.name).toBe("open-tool");

    const withRole = makeCtx({ roles: new Set(["admin"]) });
    const specs2 = await mgr.discover(withRole);
    expect(specs2).toHaveLength(2);
  });

  it("returns empty array when no tools registered", async () => {
    const mgr = new FunctionToolManager();
    const specs = await mgr.discover(makeCtx());
    expect(specs).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// call()
// ---------------------------------------------------------------------------

describe("FunctionToolManager.call", () => {
  it("calls a sync function and returns SUCCESS", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("add", "Add", undefined, (args) => {
      return (args["a"] as number) + (args["b"] as number);
    });
    const result = await mgr.call("add", { a: 3, b: 4 }, makeCtx());
    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(result.output).toBe("7");
    expect(result.error).toBeNull();
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("calls an async function and returns SUCCESS", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("async-add", "Add async", undefined, async (args) => {
      return (args["a"] as number) + (args["b"] as number);
    });
    const result = await mgr.call("async-add", { a: 1, b: 2 }, makeCtx());
    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(result.output).toBe("3");
  });

  it("returns NOT_FOUND for unknown tool", async () => {
    const mgr = new FunctionToolManager();
    const result = await mgr.call("nonexistent", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.NOT_FOUND);
    expect(result.error).toContain("nonexistent");
  });

  it("returns PERMISSION_DENIED when tool not in allowedTools", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("secret", "Secret", undefined, () => "hidden");
    const ctx = makeCtx({ allowedTools: new Set(["other"]) });
    const result = await mgr.call("secret", {}, ctx);
    expect(result.status).toBe(ToolStatus.PERMISSION_DENIED);
  });

  it("returns PERMISSION_DENIED when role requirement not met", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("admin-op", "Admin", { requiredPermissions: new Set(["admin"]) }, () => "ok");
    const ctx = makeCtx({ roles: new Set(["user"]) });
    const result = await mgr.call("admin-op", {}, ctx);
    expect(result.status).toBe(ToolStatus.PERMISSION_DENIED);
    expect(result.error).toContain("Missing required role");
  });

  it("returns ERROR when function throws", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("fail", "Fails", undefined, () => {
      throw new Error("boom");
    });
    const result = await mgr.call("fail", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.ERROR);
    expect(result.error).toContain("boom");
  });

  it("returns ERROR with descriptive message for non-Error throws", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("fail-str", "Fails with string", undefined, () => {
      throw "string error";
    });
    const result = await mgr.call("fail-str", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.ERROR);
    expect(result.error).toContain("string error");
  });

  it("stringifies non-string return values", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("obj", "Returns object", undefined, () => ({ key: "val" }));
    const result = await mgr.call("obj", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(result.output).toBe("[object Object]");
  });

  it("handles null return", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("null-fn", "Returns null", undefined, () => null);
    const result = await mgr.call("null-fn", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(result.output).toBe("null");
  });

  it("handles undefined return", async () => {
    const mgr = new FunctionToolManager();
    mgr.tool("undef-fn", "Returns undefined", undefined, () => undefined);
    const result = await mgr.call("undef-fn", {}, makeCtx());
    expect(result.status).toBe(ToolStatus.SUCCESS);
    expect(result.output).toBe("undefined");
  });
});

// ---------------------------------------------------------------------------
// createToolSpec / createToolResult factory helpers
// ---------------------------------------------------------------------------

describe("createToolSpec", () => {
  it("creates a spec with defaults", () => {
    const spec = createToolSpec("t", "test tool");
    expect(spec.name).toBe("t");
    expect(spec.description).toBe("test tool");
    expect(spec.parameters).toEqual({});
    expect(spec.requiredPermissions.size).toBe(0);
  });
});

describe("createToolResult", () => {
  it("creates a result with defaults", () => {
    const r = createToolResult(ToolStatus.SUCCESS);
    expect(r.status).toBe(ToolStatus.SUCCESS);
    expect(r.output).toBe("");
    expect(r.error).toBeNull();
    expect(r.durationMs).toBe(0);
  });

  it("accepts overrides", () => {
    const r = createToolResult(ToolStatus.ERROR, { error: "fail", durationMs: 42 });
    expect(r.error).toBe("fail");
    expect(r.durationMs).toBe(42);
  });
});
