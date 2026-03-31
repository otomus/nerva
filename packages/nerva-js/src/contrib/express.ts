/**
 * Express integration bridge for Nerva.
 *
 * Provides middleware, JWT-to-Permissions mapping, and SSE streaming handler
 * for Express applications. All Express imports are conditional — a clear
 * error message is thrown if the dependency is missing.
 *
 * @module contrib/express
 */

import {
  ExecContext,
  createPermissions,
  type Permissions,
  type PermissionsInit,
} from "../context.js";
import type { Orchestrator } from "../orchestrator.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** HTTP header used to propagate an external request ID. */
export const REQUEST_ID_HEADER = "x-request-id";

/** HTTP header carrying the Bearer token. */
export const AUTHORIZATION_HEADER = "authorization";

/** Key used to store the ExecContext on the request object. */
export const NERVA_CTX_KEY = "nervaCtx";

/** MIME type for Server-Sent Events responses. */
export const SSE_CONTENT_TYPE = "text/event-stream";

/** Prefix stripped from Authorization header values. */
const BEARER_PREFIX = "Bearer ";

// ---------------------------------------------------------------------------
// Type shims for Express (avoids hard dependency on @types/express)
// ---------------------------------------------------------------------------

/** Minimal Express Request shape needed by the middleware. */
interface ExpressRequest {
  headers: Record<string, string | string[] | undefined>;
  [key: string]: unknown;
}

/** Minimal Express Response shape needed by the SSE handler. */
interface ExpressResponse {
  setHeader(name: string, value: string): void;
  write(chunk: string): boolean;
  end(): void;
  flush?(): void;
  on(event: string, listener: () => void): void;
}

/** Express next-function signature. */
type NextFunction = (err?: unknown) => void;

// ---------------------------------------------------------------------------
// Middleware config
// ---------------------------------------------------------------------------

/** Configuration for {@link nervaMiddleware}. */
export interface NervaMiddlewareConfig {
  /** Default memory scope when none is inferred. Defaults to `"session"`. */
  readonly defaultScope?: "user" | "session" | "agent" | "global";
}

// ---------------------------------------------------------------------------
// nervaMiddleware — Express middleware
// ---------------------------------------------------------------------------

/**
 * Express middleware that creates an `ExecContext` from request headers.
 *
 * Reads `x-request-id` and `authorization` headers to populate the context.
 * The context is stored on `req.nervaCtx` for downstream handlers.
 *
 * @param config - Optional configuration.
 * @returns An Express middleware function.
 */
export function nervaMiddleware(
  config?: NervaMiddlewareConfig,
): (req: ExpressRequest, _res: ExpressResponse, next: NextFunction) => void {
  const defaultScope = config?.defaultScope ?? "session";

  return (req: ExpressRequest, _res: ExpressResponse, next: NextFunction): void => {
    const ctx = buildCtxFromRequest(req, defaultScope);
    req[NERVA_CTX_KEY] = ctx;
    next();
  };
}

// ---------------------------------------------------------------------------
// permissionsFromBearer — JWT-to-Permissions mapper
// ---------------------------------------------------------------------------

/**
 * Map a JWT bearer token to a Nerva `Permissions` object.
 *
 * Delegates decoding to the caller-supplied `decodeFn`.
 * Expects the decoded payload to contain optional `roles`, `allowedTools`,
 * and `allowedAgents` fields.
 *
 * @param token - Raw JWT string (without the `Bearer ` prefix).
 * @param decodeFn - Function that decodes the token and returns a claims object.
 * @returns A `Permissions` instance populated from the JWT claims.
 * @throws {Error} If token is empty or whitespace-only.
 */
export function permissionsFromBearer(
  token: string,
  decodeFn: (token: string) => Record<string, unknown>,
): Permissions {
  if (!token || !token.trim()) {
    throw new Error("Token must be a non-empty string");
  }

  const claims = decodeFn(token);
  const roles = toStringSet(claims["roles"]);
  const allowedTools = toOptionalStringSet(claims["allowedTools"]);
  const allowedAgents = toOptionalStringSet(claims["allowedAgents"]);

  return createPermissions({ roles, allowedTools, allowedAgents });
}

// ---------------------------------------------------------------------------
// sseHandler — Express SSE streaming handler factory
// ---------------------------------------------------------------------------

/** Options for {@link sseHandler}. */
export interface SSEHandlerOptions {
  /** Extract the user message from the request. */
  readonly getMessage: (req: ExpressRequest) => string;

  /** Extract or build the ExecContext from the request. */
  readonly getCtx: (req: ExpressRequest) => ExecContext;
}

/**
 * Express handler factory that streams orchestrator output as SSE events.
 *
 * Each chunk is sent as a `data:` line followed by a blank line.
 * When the stream ends, a `data: [DONE]` sentinel is sent.
 *
 * @param orchestrator - The Nerva orchestrator instance.
 * @param options - Extraction functions for message and context.
 * @returns An Express route handler.
 */
export function sseHandler(
  orchestrator: Orchestrator,
  options: SSEHandlerOptions,
): (req: ExpressRequest, res: ExpressResponse) => Promise<void> {
  return async (req: ExpressRequest, res: ExpressResponse): Promise<void> => {
    const message = options.getMessage(req);
    const ctx = options.getCtx(req);

    res.setHeader("Content-Type", SSE_CONTENT_TYPE);
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");

    let aborted = false;
    res.on("close", () => {
      aborted = true;
      ctx.cancel();
    });

    for await (const chunk of orchestrator.stream(message, ctx)) {
      if (aborted) break;
      res.write(formatSseEvent(chunk));
      if (res.flush) res.flush();
    }

    if (!aborted) {
      res.write(formatSseEvent("[DONE]"));
    }
    res.end();
  };
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Build an ExecContext from Express request headers.
 *
 * @param req - The Express request object.
 * @param defaultScope - Fallback memory scope.
 * @returns A populated ExecContext.
 */
function buildCtxFromRequest(
  req: ExpressRequest,
  defaultScope: "user" | "session" | "agent" | "global",
): ExecContext {
  const requestId = extractHeader(req, REQUEST_ID_HEADER);
  const authHeader = extractHeader(req, AUTHORIZATION_HEADER);
  const userId = extractUserIdFromAuth(authHeader);

  const ctx = ExecContext.create({
    userId,
    memoryScope: defaultScope,
  });

  if (requestId) {
    // Override auto-generated request ID with external one.
    // ExecContext fields are readonly by TS but we need to override here.
    (ctx as { requestId: string }).requestId = requestId;
  }

  return ctx;
}

/**
 * Safely extract a single header value from an Express request.
 *
 * @param req - The Express request.
 * @param name - Lowercase header name.
 * @returns The header value as a string, or null.
 */
function extractHeader(req: ExpressRequest, name: string): string | null {
  const value = req.headers[name];
  if (value === undefined) return null;
  if (Array.isArray(value)) return value[0] ?? null;
  return value;
}

/**
 * Pull a user identifier from the Authorization header value.
 *
 * @param authHeader - Raw Authorization header value, or null.
 * @returns A user identifier string, or null.
 */
function extractUserIdFromAuth(authHeader: string | null): string | null {
  if (!authHeader) return null;
  if (authHeader.startsWith(BEARER_PREFIX)) {
    return authHeader.slice(BEARER_PREFIX.length);
  }
  return authHeader;
}

/**
 * Convert an unknown value to a ReadonlySet of strings.
 *
 * @param value - Expected to be an array of strings, or undefined.
 * @returns A ReadonlySet, defaulting to empty.
 */
function toStringSet(value: unknown): ReadonlySet<string> {
  if (!Array.isArray(value)) return new Set<string>();
  return new Set(value.filter((v): v is string => typeof v === "string"));
}

/**
 * Convert an unknown value to an optional ReadonlySet of strings.
 *
 * @param value - Expected to be an array of strings, undefined, or null.
 * @returns A ReadonlySet, or null if the value is nullish.
 */
function toOptionalStringSet(
  value: unknown,
): ReadonlySet<string> | null {
  if (value === undefined || value === null) return null;
  return toStringSet(value);
}

/**
 * Format a string as an SSE data event.
 *
 * @param data - The event payload.
 * @returns SSE-formatted string with `data:` prefix and trailing newlines.
 */
function formatSseEvent(data: string): string {
  return `data: ${data}\n\n`;
}
