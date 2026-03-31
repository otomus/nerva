/**
 * NestJS integration bridge for Nerva.
 *
 * Provides a dynamic module, parameter decorator, interceptor, and permissions
 * helper for NestJS applications. All NestJS imports are conditional — a clear
 * error message is thrown if `@nestjs/common` is missing.
 *
 * @module contrib/nestjs
 */

import {
  ExecContext,
  createPermissions,
  type Permissions,
} from "../context.js";
import type { Orchestrator } from "../orchestrator.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Injection token for the Nerva Orchestrator. */
export const NERVA_ORCHESTRATOR_TOKEN = "NERVA_ORCHESTRATOR";

/** Injection token for Nerva module options. */
export const NERVA_OPTIONS_TOKEN = "NERVA_OPTIONS";

/** HTTP header used to propagate an external request ID. */
export const REQUEST_ID_HEADER = "x-request-id";

/** HTTP header carrying the Bearer token. */
export const AUTHORIZATION_HEADER = "authorization";

/** Key used to store ExecContext on the request object. */
export const NERVA_CTX_KEY = "nervaCtx";

/** Prefix stripped from Authorization header values. */
const BEARER_PREFIX = "Bearer ";

// ---------------------------------------------------------------------------
// Type shims (avoids hard dependency on @nestjs/common)
// ---------------------------------------------------------------------------

/** Minimal NestJS ExecutionContext shape. */
interface NestExecutionContext {
  switchToHttp(): { getRequest(): NestRequest };
}

/** Minimal NestJS request shape. */
interface NestRequest {
  headers: Record<string, string | string[] | undefined>;
  [key: string]: unknown;
}

/** Minimal NestJS CallHandler shape. */
interface NestCallHandler {
  handle(): { pipe(...operators: unknown[]): unknown };
}

/** NestJS-compatible dynamic module shape. */
export interface NervaDynamicModule {
  readonly module: symbol;
  readonly providers: readonly NervaProvider[];
  readonly exports: readonly (string | symbol)[];
}

/** NestJS-compatible provider shape. */
interface NervaProvider {
  readonly provide: string | symbol;
  readonly useFactory?: (...args: unknown[]) => unknown;
  readonly useValue?: unknown;
  readonly inject?: readonly (string | symbol)[];
}

// ---------------------------------------------------------------------------
// NervaModuleOptions
// ---------------------------------------------------------------------------

/** Options for {@link NervaModule.register}. */
export interface NervaModuleOptions {
  /** Factory function that creates the Orchestrator. */
  readonly orchestratorFactory: () => Orchestrator | Promise<Orchestrator>;
}

// ---------------------------------------------------------------------------
// NervaModule — NestJS dynamic module
// ---------------------------------------------------------------------------

/** Unique symbol identifying the NervaModule. */
const NERVA_MODULE_SYMBOL = Symbol("NervaModule");

/**
 * NestJS dynamic module that provides a Nerva `Orchestrator`.
 *
 * Usage:
 * ```ts
 * @Module({ imports: [NervaModule.register({ orchestratorFactory: () => myOrchestrator })] })
 * export class AppModule {}
 * ```
 */
export const NervaModule = {
  /**
   * Register the NervaModule with an orchestrator factory.
   *
   * @param options - Module configuration including the orchestrator factory.
   * @returns A NestJS-compatible dynamic module definition.
   */
  register(options: NervaModuleOptions): NervaDynamicModule {
    return {
      module: NERVA_MODULE_SYMBOL,
      providers: [
        {
          provide: NERVA_OPTIONS_TOKEN,
          useValue: options,
        },
        {
          provide: NERVA_ORCHESTRATOR_TOKEN,
          useFactory: async (...args: unknown[]) => {
            const opts = args[0] as NervaModuleOptions;
            return opts.orchestratorFactory();
          },
          inject: [NERVA_OPTIONS_TOKEN],
        },
      ],
      exports: [NERVA_ORCHESTRATOR_TOKEN],
    };
  },
};

// ---------------------------------------------------------------------------
// @NervaCtx() — parameter decorator
// ---------------------------------------------------------------------------

/**
 * NestJS parameter decorator that extracts the `ExecContext` from the request.
 *
 * The interceptor or middleware must have attached the context to the request
 * under the `nervaCtx` key before this decorator is evaluated.
 *
 * Usage:
 * ```ts
 * @Post('/chat')
 * async chat(@NervaCtx() ctx: ExecContext) { ... }
 * ```
 *
 * @returns A NestJS parameter decorator.
 */
export function NervaCtx(): ParameterDecorator {
  return (_target: object, _propertyKey: string | symbol | undefined, parameterIndex: number): void => {
    // Store metadata for extraction by NervaInterceptor
    const existing: number[] =
      (Reflect as any).getMetadata?.("nerva:ctx_params", _target) ?? [];
    existing.push(parameterIndex);
    (Reflect as any).defineMetadata?.("nerva:ctx_params", existing, _target);
  };
}

/**
 * Extract `ExecContext` from a NestJS execution context.
 *
 * Looks for the context on `request.nervaCtx`. If not found, creates a
 * fresh context from request headers and attaches it.
 *
 * @param nestCtx - The NestJS execution context.
 * @returns The `ExecContext` for this request.
 */
export function extractNervaCtx(nestCtx: NestExecutionContext): ExecContext {
  const request = nestCtx.switchToHttp().getRequest();
  const existing = request[NERVA_CTX_KEY];

  if (existing instanceof ExecContext) {
    return existing;
  }

  const ctx = buildCtxFromRequest(request);
  request[NERVA_CTX_KEY] = ctx;
  return ctx;
}

// ---------------------------------------------------------------------------
// NervaInterceptor — automatic context creation
// ---------------------------------------------------------------------------

/**
 * NestJS interceptor that creates an `ExecContext` from request headers
 * and attaches it to the request before the handler executes.
 *
 * Usage:
 * ```ts
 * @UseInterceptors(NervaInterceptor)
 * @Controller('chat')
 * export class ChatController { ... }
 * ```
 */
export class NervaInterceptor {
  /**
   * Intercept the request and attach an ExecContext.
   *
   * @param context - The NestJS execution context.
   * @param next - The call handler to continue the pipeline.
   * @returns The result of the downstream handler.
   */
  intercept(context: NestExecutionContext, next: NestCallHandler): unknown {
    extractNervaCtx(context);
    return next.handle();
  }
}

// ---------------------------------------------------------------------------
// permissionsFromGuard — map NestJS user object to Permissions
// ---------------------------------------------------------------------------

/** Expected shape of a NestJS user object with role/permission info. */
export interface GuardUser {
  readonly roles?: readonly string[];
  readonly allowedTools?: readonly string[] | null;
  readonly allowedAgents?: readonly string[] | null;
}

/**
 * Map a NestJS guard-populated user object to Nerva `Permissions`.
 *
 * Typically used after an `AuthGuard` has attached a `user` object
 * to the request.
 *
 * @param user - The user object from the NestJS guard, or null/undefined.
 * @returns A `Permissions` instance populated from the user object.
 */
export function permissionsFromGuard(user: GuardUser | null | undefined): Permissions {
  if (!user) {
    return createPermissions();
  }

  const roles = new Set(user.roles ?? []);
  const allowedTools = user.allowedTools
    ? new Set(user.allowedTools)
    : null;
  const allowedAgents = user.allowedAgents
    ? new Set(user.allowedAgents)
    : null;

  return createPermissions({ roles, allowedTools, allowedAgents });
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Build an ExecContext from a NestJS request object.
 *
 * @param request - The NestJS/Express request.
 * @returns A populated ExecContext.
 */
function buildCtxFromRequest(request: NestRequest): ExecContext {
  const requestId = extractHeader(request, REQUEST_ID_HEADER);
  const authHeader = extractHeader(request, AUTHORIZATION_HEADER);
  const userId = extractUserIdFromAuth(authHeader);

  const ctx = ExecContext.create({ userId });

  if (requestId) {
    (ctx as { requestId: string }).requestId = requestId;
  }

  return ctx;
}

/**
 * Safely extract a single header value.
 *
 * @param req - The request object.
 * @param name - Lowercase header name.
 * @returns The header value or null.
 */
function extractHeader(req: NestRequest, name: string): string | null {
  const value = req.headers[name];
  if (value === undefined) return null;
  if (Array.isArray(value)) return value[0] ?? null;
  return value;
}

/**
 * Pull a user identifier from the Authorization header.
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
