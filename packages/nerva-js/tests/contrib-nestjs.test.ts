/**
 * Tests for the NestJS integration bridge.
 */

import { describe, it, expect } from "vitest";
import {
  NervaModule,
  NervaInterceptor,
  extractNervaCtx,
  permissionsFromGuard,
  NERVA_ORCHESTRATOR_TOKEN,
  NERVA_OPTIONS_TOKEN,
  NERVA_CTX_KEY,
  type GuardUser,
} from "../src/contrib/nestjs.js";
import { ExecContext } from "../src/context.js";
import type { Orchestrator } from "../src/orchestrator.js";

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

/** Create a fake NestJS ExecutionContext. */
function fakeNestContext(
  headers: Record<string, string> = {},
  extraProps: Record<string, unknown> = {},
): { switchToHttp(): { getRequest(): Record<string, unknown> } } {
  const lowered: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) {
    lowered[k.toLowerCase()] = v;
  }
  const request: Record<string, unknown> = { headers: lowered, ...extraProps };

  return {
    switchToHttp() {
      return {
        getRequest() {
          return request;
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// NervaModule.register
// ---------------------------------------------------------------------------

describe("NervaModule.register", () => {
  it("creates a dynamic module with orchestrator provider", () => {
    const fakeOrchestrator = {} as Orchestrator;
    const mod = NervaModule.register({
      orchestratorFactory: () => fakeOrchestrator,
    });

    expect(mod.module).toBeDefined();
    expect(mod.exports).toContain(NERVA_ORCHESTRATOR_TOKEN);
    expect(mod.providers.length).toBe(2);

    const optionsProvider = mod.providers.find(
      (p) => p.provide === NERVA_OPTIONS_TOKEN,
    );
    expect(optionsProvider).toBeDefined();
    expect(optionsProvider!.useValue).toBeDefined();

    const orchestratorProvider = mod.providers.find(
      (p) => p.provide === NERVA_ORCHESTRATOR_TOKEN,
    );
    expect(orchestratorProvider).toBeDefined();
    expect(orchestratorProvider!.useFactory).toBeTypeOf("function");
  });

  it("factory resolves to the orchestrator", async () => {
    const fakeOrchestrator = { handle: () => {} } as unknown as Orchestrator;
    const mod = NervaModule.register({
      orchestratorFactory: () => fakeOrchestrator,
    });

    const provider = mod.providers.find(
      (p) => p.provide === NERVA_ORCHESTRATOR_TOKEN,
    );
    const factory = provider!.useFactory as (opts: unknown) => Promise<unknown>;
    const opts = mod.providers.find(
      (p) => p.provide === NERVA_OPTIONS_TOKEN,
    )!.useValue;

    const result = await factory(opts);
    expect(result).toBe(fakeOrchestrator);
  });

  it("factory supports async orchestrator creation", async () => {
    const fakeOrchestrator = { handle: () => {} } as unknown as Orchestrator;
    const mod = NervaModule.register({
      orchestratorFactory: () => Promise.resolve(fakeOrchestrator),
    });

    const provider = mod.providers.find(
      (p) => p.provide === NERVA_ORCHESTRATOR_TOKEN,
    );
    const factory = provider!.useFactory as (opts: unknown) => Promise<unknown>;
    const opts = mod.providers.find(
      (p) => p.provide === NERVA_OPTIONS_TOKEN,
    )!.useValue;

    const result = await factory(opts);
    expect(result).toBe(fakeOrchestrator);
  });
});

// ---------------------------------------------------------------------------
// extractNervaCtx
// ---------------------------------------------------------------------------

describe("extractNervaCtx", () => {
  it("extracts existing context from request", () => {
    const existingCtx = ExecContext.create({ userId: "user-1" });
    const nestCtx = fakeNestContext({}, { [NERVA_CTX_KEY]: existingCtx });

    const result = extractNervaCtx(nestCtx);
    expect(result).toBe(existingCtx);
  });

  it("creates context from headers when none exists", () => {
    const nestCtx = fakeNestContext({
      "X-Request-Id": "req-99",
      Authorization: "Bearer tok-xyz",
    });

    const result = extractNervaCtx(nestCtx);
    expect(result).toBeInstanceOf(ExecContext);
    expect(result.requestId).toBe("req-99");
    expect(result.userId).toBe("tok-xyz");
  });

  it("attaches created context to request for reuse", () => {
    const nestCtx = fakeNestContext({ Authorization: "Bearer tok-1" });
    const req = nestCtx.switchToHttp().getRequest();

    const first = extractNervaCtx(nestCtx);
    expect(req[NERVA_CTX_KEY]).toBe(first);

    // Second call returns the same instance
    const second = extractNervaCtx(nestCtx);
    expect(second).toBe(first);
  });

  it("handles missing headers gracefully", () => {
    const nestCtx = fakeNestContext();

    const result = extractNervaCtx(nestCtx);
    expect(result).toBeInstanceOf(ExecContext);
    expect(result.userId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// NervaInterceptor
// ---------------------------------------------------------------------------

describe("NervaInterceptor", () => {
  it("attaches ExecContext and continues pipeline", () => {
    const interceptor = new NervaInterceptor();
    const nestCtx = fakeNestContext({
      Authorization: "Bearer user-abc",
    });
    const handleResult = { pipe: () => "piped" };
    const next = { handle: () => handleResult };

    const result = interceptor.intercept(nestCtx, next);

    expect(result).toBe(handleResult);
    const req = nestCtx.switchToHttp().getRequest();
    expect(req[NERVA_CTX_KEY]).toBeInstanceOf(ExecContext);
  });
});

// ---------------------------------------------------------------------------
// permissionsFromGuard
// ---------------------------------------------------------------------------

describe("permissionsFromGuard", () => {
  it("maps full user object to Permissions", () => {
    const user: GuardUser = {
      roles: ["admin", "editor"],
      allowedTools: ["tool_a"],
      allowedAgents: ["agent_x"],
    };

    const perms = permissionsFromGuard(user);

    expect(perms.hasRole("admin")).toBe(true);
    expect(perms.hasRole("editor")).toBe(true);
    expect(perms.canUseTool("tool_a")).toBe(true);
    expect(perms.canUseTool("tool_b")).toBe(false);
    expect(perms.canUseAgent("agent_x")).toBe(true);
    expect(perms.canUseAgent("agent_y")).toBe(false);
  });

  it("returns unrestricted permissions for null user", () => {
    const perms = permissionsFromGuard(null);

    expect(perms.roles.size).toBe(0);
    expect(perms.allowedTools).toBeNull();
    expect(perms.allowedAgents).toBeNull();
  });

  it("returns unrestricted permissions for undefined user", () => {
    const perms = permissionsFromGuard(undefined);

    expect(perms.roles.size).toBe(0);
    expect(perms.allowedTools).toBeNull();
  });

  it("handles user with no roles", () => {
    const user: GuardUser = {};
    const perms = permissionsFromGuard(user);

    expect(perms.roles.size).toBe(0);
    expect(perms.allowedTools).toBeNull();
  });

  it("handles user with null allowedTools", () => {
    const user: GuardUser = {
      roles: ["user"],
      allowedTools: null,
    };
    const perms = permissionsFromGuard(user);

    expect(perms.hasRole("user")).toBe(true);
    expect(perms.allowedTools).toBeNull();
  });

  it("handles user with empty arrays", () => {
    const user: GuardUser = {
      roles: [],
      allowedTools: [],
      allowedAgents: [],
    };
    const perms = permissionsFromGuard(user);

    expect(perms.roles.size).toBe(0);
    // Empty arrays become empty sets (not null), meaning "none allowed"
    expect(perms.allowedTools).not.toBeNull();
    expect(perms.canUseTool("anything")).toBe(false);
  });
});
