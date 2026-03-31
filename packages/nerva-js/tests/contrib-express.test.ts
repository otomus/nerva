/**
 * Tests for the Express integration bridge.
 */

import { describe, it, expect, vi } from "vitest";
import {
  nervaMiddleware,
  permissionsFromBearer,
  sseHandler,
  REQUEST_ID_HEADER,
  NERVA_CTX_KEY,
  SSE_CONTENT_TYPE,
} from "../src/contrib/express.js";
import { ExecContext } from "../src/context.js";
import type { Orchestrator } from "../src/orchestrator.js";

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

/** Create a minimal Express-like request. */
function fakeReq(headers: Record<string, string> = {}): Record<string, unknown> {
  const lowered: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) {
    lowered[k.toLowerCase()] = v;
  }
  return { headers: lowered } as Record<string, unknown>;
}

/** Create a minimal Express-like response that captures writes. */
function fakeRes(): {
  written: string[];
  ended: boolean;
  headers: Record<string, string>;
  setHeader(name: string, value: string): void;
  write(chunk: string): boolean;
  end(): void;
  flush(): void;
  on(event: string, listener: () => void): void;
  _listeners: Record<string, (() => void)[]>;
} {
  const res = {
    written: [] as string[],
    ended: false,
    headers: {} as Record<string, string>,
    _listeners: {} as Record<string, (() => void)[]>,
    setHeader(name: string, value: string) {
      res.headers[name] = value;
    },
    write(chunk: string): boolean {
      res.written.push(chunk);
      return true;
    },
    end() {
      res.ended = true;
    },
    flush() {
      // no-op
    },
    on(event: string, listener: () => void) {
      if (!res._listeners[event]) res._listeners[event] = [];
      res._listeners[event]!.push(listener);
    },
  };
  return res;
}

// ---------------------------------------------------------------------------
// nervaMiddleware
// ---------------------------------------------------------------------------

describe("nervaMiddleware", () => {
  it("creates ExecContext from request headers", () => {
    const mw = nervaMiddleware();
    const req = fakeReq({
      "X-Request-Id": "req-42",
      Authorization: "Bearer tok-abc",
    });
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx).toBeInstanceOf(ExecContext);
    expect(ctx.requestId).toBe("req-42");
    expect(ctx.userId).toBe("tok-abc");
    expect(next).toHaveBeenCalledOnce();
  });

  it("handles missing headers with anonymous context", () => {
    const mw = nervaMiddleware();
    const req = fakeReq();
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx).toBeInstanceOf(ExecContext);
    expect(ctx.userId).toBeNull();
    expect(ctx.requestId).toBeTruthy();
  });

  it("applies custom default scope", () => {
    const mw = nervaMiddleware({ defaultScope: "user" });
    const req = fakeReq();
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx.memoryScope).toBe("user");
  });

  it("uses non-Bearer auth header as user_id directly", () => {
    const mw = nervaMiddleware();
    const req = fakeReq({ Authorization: "api-key-xyz" });
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx.userId).toBe("api-key-xyz");
  });

  it("empty Authorization header yields null user_id", () => {
    const mw = nervaMiddleware();
    const req = fakeReq({ Authorization: "" });
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx.userId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// permissionsFromBearer
// ---------------------------------------------------------------------------

describe("permissionsFromBearer", () => {
  it("maps full JWT claims to Permissions", () => {
    const claims = {
      roles: ["admin", "user"],
      allowedTools: ["tool_a", "tool_b"],
      allowedAgents: ["agent_x"],
    };
    const perms = permissionsFromBearer("valid-token", () => claims);

    expect(perms.hasRole("admin")).toBe(true);
    expect(perms.hasRole("user")).toBe(true);
    expect(perms.canUseTool("tool_a")).toBe(true);
    expect(perms.canUseTool("tool_c")).toBe(false);
    expect(perms.canUseAgent("agent_x")).toBe(true);
    expect(perms.canUseAgent("agent_y")).toBe(false);
  });

  it("defaults to unrestricted when claims are empty", () => {
    const perms = permissionsFromBearer("tok", () => ({}));

    expect(perms.roles.size).toBe(0);
    expect(perms.allowedTools).toBeNull();
    expect(perms.allowedAgents).toBeNull();
  });

  it("throws on empty token", () => {
    expect(() => permissionsFromBearer("", () => ({}))).toThrow(
      "non-empty",
    );
  });

  it("throws on whitespace-only token", () => {
    expect(() => permissionsFromBearer("   ", () => ({}))).toThrow(
      "non-empty",
    );
  });

  it("propagates decode function errors", () => {
    expect(() =>
      permissionsFromBearer("tok", () => {
        throw new Error("invalid signature");
      }),
    ).toThrow("invalid signature");
  });

  it("filters non-string values from roles", () => {
    const claims = { roles: [123, "admin", null, "user"] };
    const perms = permissionsFromBearer("tok", () => claims);

    expect(perms.hasRole("admin")).toBe(true);
    expect(perms.hasRole("user")).toBe(true);
    // non-strings are filtered out
    expect(perms.roles.size).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// sseHandler
// ---------------------------------------------------------------------------

describe("sseHandler", () => {
  it("streams chunks as SSE data events", async () => {
    const chunks = ["Hello", " world"];
    const fakeOrchestrator = {
      async *stream(_message: string, _ctx: ExecContext) {
        for (const chunk of chunks) {
          yield chunk;
        }
      },
    } as unknown as Orchestrator;

    const handler = sseHandler(fakeOrchestrator, {
      getMessage: (req) => req["body"] as string,
      getCtx: (req) => req[NERVA_CTX_KEY] as ExecContext,
    });

    const ctx = ExecContext.create();
    const req = { body: "hi", [NERVA_CTX_KEY]: ctx, headers: {} };
    const res = fakeRes();

    await handler(req as never, res as never);

    expect(res.headers["Content-Type"]).toBe(SSE_CONTENT_TYPE);
    expect(res.headers["Cache-Control"]).toBe("no-cache");
    expect(res.written).toEqual([
      "data: Hello\n\n",
      "data:  world\n\n",
      "data: [DONE]\n\n",
    ]);
    expect(res.ended).toBe(true);
  });

  it("sends only [DONE] for empty stream", async () => {
    const fakeOrchestrator = {
      async *stream() {
        // empty stream
      },
    } as unknown as Orchestrator;

    const handler = sseHandler(fakeOrchestrator, {
      getMessage: () => "hi",
      getCtx: () => ExecContext.create(),
    });

    const req = { headers: {} };
    const res = fakeRes();

    await handler(req as never, res as never);

    expect(res.written).toEqual(["data: [DONE]\n\n"]);
  });

  it("cancels context on client disconnect", async () => {
    let yieldCount = 0;
    const fakeOrchestrator = {
      async *stream(_message: string, ctx: ExecContext) {
        while (!ctx.isCancelled()) {
          yield `chunk-${yieldCount}`;
          yieldCount++;
          if (yieldCount > 10) break; // safety limit
        }
      },
    } as unknown as Orchestrator;

    const handler = sseHandler(fakeOrchestrator, {
      getMessage: () => "hi",
      getCtx: (req) => req[NERVA_CTX_KEY] as ExecContext,
    });

    const ctx = ExecContext.create();
    const req = { [NERVA_CTX_KEY]: ctx, headers: {} };
    const res = fakeRes();

    // Simulate client disconnect after first write
    const origWrite = res.write.bind(res);
    res.write = (chunk: string) => {
      origWrite(chunk);
      // Trigger close listeners after first chunk
      if (res.written.length === 1 && res._listeners["close"]) {
        for (const listener of res._listeners["close"]) listener();
      }
      return true;
    };

    await handler(req as never, res as never);

    // Should have stopped early due to abort
    expect(yieldCount).toBeLessThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

describe("edge cases", () => {
  it("Bearer prefix is case-sensitive", () => {
    const mw = nervaMiddleware();
    const req = fakeReq({ Authorization: "bearer lowercase-tok" });
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx.userId).toBe("bearer lowercase-tok");
  });

  it("handles array header values (first element used)", () => {
    const mw = nervaMiddleware();
    const req = {
      headers: { [REQUEST_ID_HEADER]: ["first-id", "second-id"] },
    } as unknown as Record<string, unknown>;
    const res = fakeRes();
    const next = vi.fn();

    mw(req as never, res as never, next);

    const ctx = req[NERVA_CTX_KEY] as ExecContext;
    expect(ctx.requestId).toBe("first-id");
  });
});
