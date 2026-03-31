/**
 * Built-in middleware — reusable cross-cutting concerns.
 *
 * Provides factory functions that return ready-to-register middleware handlers
 * for common needs: request logging, permission checking, and usage tracking.
 *
 * @module middleware/builtins
 */

import type { ExecContext } from "../context.js";
import type { MiddlewareHandler, AgentInput, AgentResult } from "../orchestrator.js";

// ---------------------------------------------------------------------------
// Request logger
// ---------------------------------------------------------------------------

/** Log function signature — matches `console.log` and custom loggers. */
export type LogFn = (message: string, ...args: unknown[]) => void;

/**
 * Result of calling {@link requestLogger}: a pair of middleware handlers.
 */
export interface RequestLoggerHandlers {
  /** Middleware for the `before_route` stage. */
  readonly beforeRoute: MiddlewareHandler;
  /** Middleware for the `after_invoke` stage. */
  readonly afterInvoke: MiddlewareHandler;
}

/**
 * Create before_route and after_invoke middleware that log request lifecycle.
 *
 * Logs the incoming message at `before_route` and the handler name plus
 * wall-clock duration at `after_invoke`.
 *
 * @param logFn - Logging function. Defaults to `console.log`.
 * @returns A pair of middleware handlers for registration.
 */
export function requestLogger(logFn: LogFn = console.log): RequestLoggerHandlers {
  const startTimes = new Map<string, number>();

  const beforeRoute: MiddlewareHandler = async (ctx: ExecContext, payload: unknown): Promise<unknown> => {
    startTimes.set(ctx.requestId, performance.now());
    const message = typeof payload === "string" ? payload : String(payload);
    const truncated = message.length > 200 ? message.slice(0, 200) + "..." : message;
    logFn(`[${ctx.requestId.slice(0, 8)}] Incoming request: ${truncated}`);
    return undefined;
  };

  const afterInvoke: MiddlewareHandler = async (ctx: ExecContext, payload: unknown): Promise<unknown> => {
    const start = startTimes.get(ctx.requestId);
    startTimes.delete(ctx.requestId);
    const durationMs = start !== undefined ? performance.now() - start : -1;
    const result = payload as AgentResult | undefined;
    const handlerName = result?.handler ?? "unknown";
    const status = result?.status ?? "unknown";
    logFn(
      `[${ctx.requestId.slice(0, 8)}] Handler=${handlerName} status=${status} duration=${durationMs.toFixed(1)}ms`,
    );
    return undefined;
  };

  return { beforeRoute, afterInvoke };
}

// ---------------------------------------------------------------------------
// Permission checker
// ---------------------------------------------------------------------------

/**
 * Create a before_invoke middleware that verifies context permissions.
 *
 * Checks that the execution context carries the required roles before
 * allowing handler invocation. Returns a modified `AgentInput` with an
 * error message when a role is missing.
 *
 * @param requiredRoles - Set of role names that must be present, or `null` for no-op.
 * @returns An async middleware handler for the `before_invoke` stage.
 */
export function permissionChecker(requiredRoles: ReadonlySet<string> | null): MiddlewareHandler {
  return async (ctx: ExecContext, _payload: unknown): Promise<unknown> => {
    if (requiredRoles === null) return undefined;

    for (const role of requiredRoles) {
      if (!ctx.permissions.hasRole(role)) {
        ctx.addEvent("permission.denied", {
          missing_role: role,
          user_id: ctx.userId ?? "anonymous",
        });
        return { message: `Permission denied: missing role '${role}'` } satisfies AgentInput;
      }
    }

    return undefined;
  };
}

// ---------------------------------------------------------------------------
// Usage tracker
// ---------------------------------------------------------------------------

/**
 * Create an after_invoke middleware that records token usage as events.
 *
 * Reads `ctx.tokenUsage` after handler invocation and emits a
 * `usage.recorded` event with prompt, completion, and total token counts.
 *
 * @returns An async middleware handler for the `after_invoke` stage.
 */
export function usageTracker(): MiddlewareHandler {
  return async (ctx: ExecContext, _payload: unknown): Promise<unknown> => {
    const usage = ctx.tokenUsage;
    ctx.addEvent("usage.recorded", {
      prompt_tokens: String(usage.promptTokens),
      completion_tokens: String(usage.completionTokens),
      total_tokens: String(usage.totalTokens),
      cost_usd: usage.costUsd.toFixed(6),
    });
    return undefined;
  };
}
