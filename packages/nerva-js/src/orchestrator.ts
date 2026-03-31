/**
 * Orchestrator — wires all Nerva primitives into a single request handler.
 *
 * The orchestrator owns the full request lifecycle:
 * message -> context -> policy -> memory -> router -> runtime -> responder -> response
 *
 * All primitives are injected — none are created internally. Optional primitives
 * (tools, memory, registry, policy) gracefully degrade when absent.
 *
 * @module orchestrator
 */

import {
  ExecContext,
  InMemoryStreamSink,
} from "./context.js";
import type { IntentResult, IntentRouter } from "./router/index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Policy action kind for routing a user message. */
export const POLICY_ACTION_ROUTE = "route";

/** Policy action kind for invoking a handler. */
export const POLICY_ACTION_INVOKE = "invoke_agent";

/** Sentinel handler name used when the router returns no candidates. */
export const FALLBACK_HANDLER = "__fallback__";

/** Interval (ms) between stream sink polls in the `stream()` generator. */
const STREAM_POLL_INTERVAL_MS = 10;

/** Maximum delegation depth before returning an error result. */
export const DEFAULT_MAX_DELEGATION_DEPTH = 5;

/** Error message template when delegation depth is exceeded. */
export const DELEGATION_DEPTH_EXCEEDED_TEMPLATE = "Delegation depth limit exceeded (max: {n})";

// ---------------------------------------------------------------------------
// Lightweight protocol interfaces (N-203)
//
// These mirror the Python protocols that the orchestrator depends on.
// Full implementations live in separate modules; these are the
// minimal shapes the orchestrator needs.
// ---------------------------------------------------------------------------

/** Action to be evaluated by the policy engine. */
export interface PolicyAction {
  readonly kind: string;
  readonly subject: string;
  readonly target: string;
  readonly metadata?: Readonly<Record<string, string>>;
}

/** Result of a policy evaluation. */
export interface PolicyDecision {
  readonly allowed: boolean;
  readonly reason: string | null;
}

/** Policy enforcement engine. */
export interface PolicyEngine {
  /**
   * Evaluate an action against the policy.
   *
   * @param action - The action to evaluate.
   * @param ctx - Current execution context.
   * @returns Whether the action is allowed and why.
   */
  evaluate(action: PolicyAction, ctx: ExecContext): Promise<PolicyDecision>;

  /**
   * Record an action and its decision for audit.
   *
   * @param action - The evaluated action.
   * @param decision - The policy decision.
   * @param ctx - Current execution context.
   */
  record(
    action: PolicyAction,
    decision: PolicyDecision,
    ctx: ExecContext,
  ): Promise<void>;
}

/** Outcome status of an agent invocation. */
export type AgentStatus =
  | "success"
  | "error"
  | "timeout"
  | "wrong_handler"
  | "needs_data"
  | "needs_credentials";

/** Immutable input passed to an agent handler. */
export interface AgentInput {
  readonly message: string;
  readonly args?: Readonly<Record<string, string>>;
  readonly tools?: readonly Readonly<Record<string, string>>[];
  readonly history?: readonly Readonly<Record<string, string>>[];
}

/** Result from an agent handler invocation. */
export interface AgentResult {
  readonly status: AgentStatus;
  readonly output: string;
  readonly data?: Readonly<Record<string, string>>;
  readonly error?: string;
  readonly handler: string;
}

/** Agent execution engine. */
export interface AgentRuntime {
  /**
   * Execute a handler with the given input.
   *
   * @param handler - Handler name to invoke.
   * @param input - Structured input for the handler.
   * @param ctx - Current execution context.
   * @returns The agent result.
   */
  invoke(
    handler: string,
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult>;
}

/** Target channel for a response. */
export interface Channel {
  readonly name: string;
  readonly supportsMarkdown: boolean;
  readonly supportsMedia: boolean;
  readonly maxLength: number;
}

/** Default API channel used when none is specified. */
export const API_CHANNEL: Channel = Object.freeze({
  name: "api",
  supportsMarkdown: true,
  supportsMedia: false,
  maxLength: 0,
});

/** Formatted response ready for delivery. */
export interface Response {
  readonly text: string;
  readonly channel: Channel;
  readonly media?: readonly string[];
  readonly metadata?: Readonly<Record<string, string>>;
}

/** Output formatter for target channels. */
export interface Responder {
  /**
   * Format an agent result for the target channel.
   *
   * @param result - Agent result to format.
   * @param channel - Target delivery channel.
   * @param ctx - Current execution context.
   * @returns Formatted response.
   */
  format(
    result: AgentResult,
    channel: Channel,
    ctx: ExecContext,
  ): Promise<Response>;
}

/** Tiered context storage. */
export interface Memory {
  /**
   * Recall relevant context for the given message.
   *
   * @param message - User message to use as the recall query.
   * @param ctx - Current execution context.
   * @returns Recalled conversation history entries.
   */
  recall(
    message: string,
    ctx: ExecContext,
  ): Promise<{ conversation: readonly Readonly<Record<string, string>>[] }>;

  /**
   * Store an event in memory.
   *
   * @param event - Memory event to store.
   * @param ctx - Current execution context.
   */
  store(event: MemoryEvent, ctx: ExecContext): Promise<void>;
}

/** An event to be stored in memory. */
export interface MemoryEvent {
  readonly content: string;
  readonly tier: string;
  readonly source: string;
}

/** Component catalog. */
export interface Registry {
  /**
   * Look up a component by name.
   *
   * @param name - Component name to look up.
   * @returns The component, or `null` if not found.
   */
  lookup(name: string): Promise<unknown>;
}

/** Optional tool discovery and execution layer. */
export interface ToolManager {
  /**
   * List available tool specs.
   *
   * @param ctx - Current execution context.
   * @returns List of tool specifications.
   */
  list(ctx: ExecContext): Promise<readonly Readonly<Record<string, string>>[]>;
}

// ---------------------------------------------------------------------------
// Exceptions (N-204)
// ---------------------------------------------------------------------------

/**
 * Raised when policy blocks a request.
 */
export class PolicyDeniedError extends Error {
  /** The denial decision from the policy engine. */
  readonly decision: PolicyDecision;

  /**
   * @param decision - The policy decision that caused the denial.
   */
  constructor(decision: PolicyDecision) {
    super(decision.reason ?? "denied by policy");
    this.name = "PolicyDeniedError";
    this.decision = decision;
  }
}

// ---------------------------------------------------------------------------
// Middleware (N-204)
// ---------------------------------------------------------------------------

/**
 * Pipeline stages where middleware can be inserted.
 *
 * - `before_route` — after context creation, before intent classification.
 * - `before_invoke` — after routing, before handler execution.
 * - `after_invoke` — after handler execution, before response formatting.
 * - `before_respond` — after formatting, before returning the response.
 */
export type MiddlewareStage =
  | "before_route"
  | "before_invoke"
  | "after_invoke"
  | "before_respond";

/** All valid middleware stage values. */
export const MIDDLEWARE_STAGES: readonly MiddlewareStage[] = [
  "before_route",
  "before_invoke",
  "after_invoke",
  "before_respond",
] as const;

/**
 * Middleware handler signature.
 *
 * If the handler returns a non-null/non-undefined value, that value
 * replaces the payload for subsequent middleware and the next stage.
 *
 * @param ctx - Current execution context.
 * @param payload - Current payload entering this stage.
 * @returns Replacement payload, or `null`/`undefined` to keep the original.
 */
export type MiddlewareHandler = (
  ctx: ExecContext,
  payload: unknown,
) => Promise<unknown>;

/**
 * Error handler signature for middleware failures.
 *
 * Called when a middleware handler throws. Receives the error, the stage
 * where it occurred, and the current execution context.
 *
 * @param error - The error thrown by the middleware.
 * @param stage - The pipeline stage where the error occurred.
 * @param ctx - Current execution context.
 */
export type MiddlewareErrorHandler = (
  error: unknown,
  stage: MiddlewareStage,
  ctx: ExecContext,
) => Promise<void>;

/** Default priority for middleware registration (lower runs first). */
export const DEFAULT_MIDDLEWARE_PRIORITY = 100;

/** Internal entry pairing a handler with its execution priority. */
interface MiddlewareEntry {
  readonly handler: MiddlewareHandler;
  readonly priority: number;
  readonly insertionOrder: number;
}

// ---------------------------------------------------------------------------
// Orchestrator options
// ---------------------------------------------------------------------------

/** Required and optional dependencies for the {@link Orchestrator}. */
export interface OrchestratorOptions {
  /** Intent classifier that selects a handler. */
  readonly router: IntentRouter;

  /** Agent execution engine. */
  readonly runtime: AgentRuntime;

  /** Output formatter for target channels. */
  readonly responder: Responder;

  /** Optional tool discovery and execution layer. */
  readonly tools?: ToolManager | null;

  /** Optional tiered context storage. */
  readonly memory?: Memory | null;

  /** Optional component catalog. */
  readonly registry?: Registry | null;

  /** Optional policy enforcement engine. */
  readonly policy?: PolicyEngine | null;

  /** Maximum delegation depth (default: 5). */
  readonly maxDelegationDepth?: number;
}

// ---------------------------------------------------------------------------
// Orchestrator (N-203, N-204)
// ---------------------------------------------------------------------------

/**
 * Wires all primitives into a single request handler.
 *
 * The orchestrator owns the request lifecycle:
 * message -> context -> policy -> router -> runtime -> responder -> response
 *
 * All primitives are injected via the constructor — none are created internally.
 */
export class Orchestrator {
  private readonly _router: IntentRouter;
  private readonly _runtime: AgentRuntime;
  private readonly _responder: Responder;
  private readonly _memory: Memory | null;
  private readonly _policy: PolicyEngine | null;
  private readonly _middleware: Map<MiddlewareStage, MiddlewareEntry[]>;
  private readonly _maxDelegationDepth: number;
  private _insertionCounter = 0;
  private readonly _errorHandlers: MiddlewareErrorHandler[] = [];

  /** Optional tool discovery and execution layer, accessible to middleware. */
  readonly tools: ToolManager | null;

  /** Optional component catalog, accessible to middleware. */
  readonly registry: Registry | null;

  /**
   * @param options - Injected dependencies. `router`, `runtime`, and
   *   `responder` are required; all others are optional.
   */
  constructor(options: OrchestratorOptions) {
    this._router = options.router;
    this._runtime = options.runtime;
    this._responder = options.responder;
    this.tools = options.tools ?? null;
    this._memory = options.memory ?? null;
    this.registry = options.registry ?? null;
    this._policy = options.policy ?? null;
    this._maxDelegationDepth = options.maxDelegationDepth ?? DEFAULT_MAX_DELEGATION_DEPTH;
    this._middleware = new Map();
  }

  // -- Public API ---------------------------------------------------------

  /**
   * Process a message through the full pipeline.
   *
   * Steps:
   *   1. Create `ExecContext` if not provided.
   *   2. Policy: rate limit and budget check on the route action.
   *   3. Memory: recall relevant context for prompt enrichment.
   *   4. Middleware: `before_route`.
   *   5. Router: classify intent, select handler.
   *   6. Middleware: `before_invoke`.
   *   7. Policy: check invoke permission for the selected handler.
   *   8. Runtime: execute handler.
   *   9. Middleware: `after_invoke`.
   *   10. Memory: store the result.
   *   11. Responder: format output.
   *   12. Middleware: `before_respond`.
   *
   * @param message - User message.
   * @param ctx - Optional pre-built context. Created if not provided.
   * @param channel - Target channel. Defaults to `API_CHANNEL`.
   * @returns Formatted `Response`.
   * @throws {PolicyDeniedError} If policy blocks the request.
   */
  async handle(
    message: string,
    ctx?: ExecContext | null,
    channel?: Channel | null,
  ): Promise<Response> {
    const resolvedCtx = createOrValidateCtx(ctx ?? null);
    const targetChannel = channel ?? API_CHANNEL;

    await this._checkPolicy(POLICY_ACTION_ROUTE, message, resolvedCtx);
    const memoryCtx = await this._recallMemory(message, resolvedCtx);
    let routePayload = await this._runMiddleware(
      "before_route",
      resolvedCtx,
      message,
    ) as string;

    const intent = await this._route(routePayload, resolvedCtx);
    const handlerName = pickHandler(intent);

    let agentInput: AgentInput = buildAgentInput(routePayload, memoryCtx);
    agentInput = (await this._runMiddleware(
      "before_invoke",
      resolvedCtx,
      agentInput,
    )) as AgentInput;

    await this._checkPolicy(POLICY_ACTION_INVOKE, handlerName, resolvedCtx);
    let result = await this._invoke(handlerName, agentInput, resolvedCtx);
    result = (await this._runMiddleware(
      "after_invoke",
      resolvedCtx,
      result,
    )) as AgentResult;

    await this._storeMemory(result, resolvedCtx);
    let response = await this._formatResponse(result, targetChannel, resolvedCtx);
    response = (await this._runMiddleware(
      "before_respond",
      resolvedCtx,
      response,
    )) as Response;

    return response;
  }

  /**
   * Process a message with streaming output.
   *
   * Same pipeline as {@link handle} but attaches an `InMemoryStreamSink`
   * to the context and yields chunks as they arrive from the runtime.
   *
   * @param message - User message.
   * @param ctx - Optional pre-built context. Created if not provided.
   * @param channel - Target channel. Defaults to `API_CHANNEL`.
   * @returns An async generator yielding string chunks.
   * @throws {PolicyDeniedError} If policy blocks the request.
   */
  async *stream(
    message: string,
    ctx?: ExecContext | null,
    channel?: Channel | null,
  ): AsyncGenerator<string, void, undefined> {
    const sink = new InMemoryStreamSink();
    const resolvedCtx = createOrValidateCtx(ctx ?? null);
    resolvedCtx.stream = sink;

    const handlePromise = this.handle(message, resolvedCtx, channel);

    // Track completion and error state
    let done = false;
    let pipelineError: unknown = null;

    handlePromise
      .then(() => {
        done = true;
      })
      .catch((err: unknown) => {
        pipelineError = err;
        done = true;
      });

    let readIndex = 0;

    while (!done) {
      if (readIndex < sink.chunks.length) {
        const chunk = sink.chunks[readIndex];
        if (chunk !== undefined) {
          yield chunk;
        }
        readIndex++;
      } else {
        await sleep(STREAM_POLL_INTERVAL_MS);
      }
    }

    // Drain remaining chunks after the pipeline completes
    while (readIndex < sink.chunks.length) {
      const chunk = sink.chunks[readIndex];
      if (chunk !== undefined) {
        yield chunk;
      }
      readIndex++;
    }

    // Re-raise pipeline errors
    if (pipelineError !== null) {
      throw pipelineError;
    }
  }

  /**
   * Register middleware for a pipeline stage.
   *
   * Middleware runs in priority order (lower = earlier). Handlers with
   * equal priority preserve registration order. Each handler receives the
   * current context and payload. Returning a non-null value replaces
   * the payload for subsequent handlers and the next pipeline stage.
   *
   * @param stage - Pipeline stage to hook into.
   * @param handler - Async callable `(ctx, payload) => payload | null`.
   * @param priority - Execution order (lower runs first). Defaults to 100.
   */
  use(stage: MiddlewareStage, handler: MiddlewareHandler, priority: number = DEFAULT_MIDDLEWARE_PRIORITY): void {
    const entry: MiddlewareEntry = {
      handler,
      priority,
      insertionOrder: this._insertionCounter++,
    };
    const entries = this._middleware.get(stage);
    if (entries !== undefined) {
      entries.push(entry);
      entries.sort(sortEntries);
    } else {
      this._middleware.set(stage, [entry]);
    }
  }

  /**
   * Register an error handler for middleware failures.
   *
   * When a middleware handler throws, all registered error handlers are
   * called before the pipeline continues to the next stage.
   *
   * @param handler - Async callable `(error, stage, ctx) => void`.
   */
  onError(handler: MiddlewareErrorHandler): void {
    this._errorHandlers.push(handler);
  }

  /**
   * Register a `before_route` middleware handler.
   *
   * @param handler - Middleware handler.
   * @param priority - Execution order (lower runs first). Defaults to 100.
   */
  beforeRoute(handler: MiddlewareHandler, priority: number = DEFAULT_MIDDLEWARE_PRIORITY): void {
    this.use("before_route", handler, priority);
  }

  /**
   * Register a `before_invoke` middleware handler.
   *
   * @param handler - Middleware handler.
   * @param priority - Execution order (lower runs first). Defaults to 100.
   */
  beforeInvoke(handler: MiddlewareHandler, priority: number = DEFAULT_MIDDLEWARE_PRIORITY): void {
    this.use("before_invoke", handler, priority);
  }

  /**
   * Register an `after_invoke` middleware handler.
   *
   * @param handler - Middleware handler.
   * @param priority - Execution order (lower runs first). Defaults to 100.
   */
  afterInvoke(handler: MiddlewareHandler, priority: number = DEFAULT_MIDDLEWARE_PRIORITY): void {
    this.use("after_invoke", handler, priority);
  }

  /**
   * Register a `before_respond` middleware handler.
   *
   * @param handler - Middleware handler.
   * @param priority - Execution order (lower runs first). Defaults to 100.
   */
  beforeRespond(handler: MiddlewareHandler, priority: number = DEFAULT_MIDDLEWARE_PRIORITY): void {
    this.use("before_respond", handler, priority);
  }

  /**
   * Delegate execution to another handler with a child context.
   *
   * Creates a child `ExecContext` from `ctx`, checks agent permissions,
   * enforces the delegation depth limit, invokes the handler through the
   * runtime, and accumulates the child's token usage back to the parent.
   *
   * @param handlerName - Name of the handler to delegate to.
   * @param message - Message to pass as the delegated handler's input.
   * @param ctx - Parent execution context.
   * @returns AgentResult from the delegated handler, or an error result
   *          if the depth limit is exceeded or permissions deny the delegation.
   */
  async delegate(
    handlerName: string,
    message: string,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    if (!handlerName) {
      return buildDelegationError("handler_name must not be empty");
    }

    if (!ctx.permissions.canUseAgent(handlerName)) {
      ctx.addEvent("delegation.denied", {
        handler: handlerName,
        reason: "permission_denied",
      });
      return buildDelegationError(
        `permission denied: cannot delegate to '${handlerName}'`,
      );
    }

    const childCtx = ctx.child(handlerName);

    if (childCtx.depth > this._maxDelegationDepth) {
      ctx.addEvent("delegation.depth_exceeded", {
        handler: handlerName,
        depth: String(childCtx.depth),
        max_depth: String(this._maxDelegationDepth),
      });
      return buildDelegationError(
        DELEGATION_DEPTH_EXCEEDED_TEMPLATE.replace("{n}", String(this._maxDelegationDepth)),
      );
    }

    const agentInput: AgentInput = { message, args: {}, tools: [], history: [] };
    const result = await this._runtime.invoke(handlerName, agentInput, childCtx);

    ctx.recordTokens(childCtx.tokenUsage);
    return result;
  }

  // -- Private helpers ----------------------------------------------------

  /**
   * Evaluate a policy action and raise on denial.
   * No-ops when the policy engine is not configured.
   */
  private async _checkPolicy(
    actionKind: string,
    target: string,
    ctx: ExecContext,
  ): Promise<void> {
    if (this._policy === null) return;

    const subject = ctx.userId ?? "anonymous";
    const action: PolicyAction = { kind: actionKind, subject, target };
    const decision = await this._policy.evaluate(action, ctx);
    await this._policy.record(action, decision, ctx);

    if (!decision.allowed) {
      ctx.addEvent("policy.denied", {
        action_kind: actionKind,
        target,
      });
      throw new PolicyDeniedError(decision);
    }
  }

  /**
   * Recall relevant conversation history from memory.
   * Returns an empty list when memory is not configured.
   */
  private async _recallMemory(
    message: string,
    ctx: ExecContext,
  ): Promise<readonly Readonly<Record<string, string>>[]> {
    if (this._memory === null) return [];
    const result = await this._memory.recall(message, ctx);
    return result.conversation;
  }

  /**
   * Classify the message intent and return the routing result.
   */
  private async _route(
    message: string,
    ctx: ExecContext,
  ): Promise<IntentResult> {
    return this._router.classify(message, ctx);
  }

  /**
   * Execute the selected handler through the runtime.
   */
  private async _invoke(
    handler: string,
    agentInput: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    return this._runtime.invoke(handler, agentInput, ctx);
  }

  /**
   * Persist a successful agent result to memory.
   * Skips when memory is not configured or the result is non-success.
   */
  private async _storeMemory(
    result: AgentResult,
    ctx: ExecContext,
  ): Promise<void> {
    if (this._memory === null) return;
    if (result.status !== "success") return;

    const event: MemoryEvent = {
      content: result.output,
      tier: "hot",
      source: result.handler,
    };
    await this._memory.store(event, ctx);
  }

  /**
   * Format an agent result for the target channel.
   */
  private async _formatResponse(
    result: AgentResult,
    channel: Channel,
    ctx: ExecContext,
  ): Promise<Response> {
    return this._responder.format(result, channel, ctx);
  }

  /**
   * Run all middleware for a stage in priority order.
   *
   * If a handler throws, remaining handlers in this stage are skipped,
   * error handlers are notified, and the pipeline continues with the
   * last good payload.
   */
  private async _runMiddleware(
    stage: MiddlewareStage,
    ctx: ExecContext,
    payload: unknown,
  ): Promise<unknown> {
    const entries = this._middleware.get(stage);
    if (entries === undefined) return payload;

    let current = payload;
    for (const entry of entries) {
      try {
        const result = await entry.handler(ctx, current);
        if (result !== null && result !== undefined) {
          current = result;
        }
      } catch (err: unknown) {
        await this._emitMiddlewareError(err, stage, ctx);
        break;
      }
    }
    return current;
  }

  /**
   * Notify error handlers and emit a trace event for a middleware failure.
   */
  private async _emitMiddlewareError(
    error: unknown,
    stage: MiddlewareStage,
    ctx: ExecContext,
  ): Promise<void> {
    const errorMessage = error instanceof Error ? error.message : String(error);
    const errorType = error instanceof Error ? error.constructor.name : "Error";

    ctx.addEvent("middleware.error", {
      stage,
      error: errorMessage,
      error_type: errorType,
    });

    for (const handler of this._errorHandlers) {
      try {
        await handler(error, stage, ctx);
      } catch {
        // Error handlers must not break the pipeline.
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build an ERROR AgentResult for delegation failures.
 *
 * @param errorMessage - Human-readable description of what went wrong.
 * @returns AgentResult with ERROR status and the given error message.
 */
function buildDelegationError(errorMessage: string): AgentResult {
  return {
    status: "error",
    output: "",
    data: {},
    error: errorMessage,
    handler: "",
  };
}

/**
 * Return the provided context or create a fresh one.
 *
 * @param ctx - Caller-supplied context, or `null`.
 * @returns A valid `ExecContext` ready for the pipeline.
 */
function createOrValidateCtx(ctx: ExecContext | null): ExecContext {
  if (ctx !== null) return ctx;
  return ExecContext.create();
}

/**
 * Extract the best handler name from an intent result.
 * Falls back to `FALLBACK_HANDLER` when no candidates exist.
 *
 * @param intent - Routing result with ranked handler candidates.
 * @returns Handler name to invoke.
 */
function pickHandler(intent: IntentResult): string {
  const best = intent.bestHandler;
  if (best === null) return FALLBACK_HANDLER;
  return best.name;
}

/**
 * Construct an `AgentInput` from the message and memory context.
 *
 * @param message - User message.
 * @param history - Conversation history from memory recall.
 * @returns Structured `AgentInput` for the runtime.
 */
function buildAgentInput(
  message: string,
  history: readonly Readonly<Record<string, string>>[],
): AgentInput {
  return { message, history: [...history] };
}

/**
 * Sort comparator for middleware entries — lower priority first,
 * then by insertion order for stability.
 *
 * @param a - First entry.
 * @param b - Second entry.
 * @returns Negative if `a` should run before `b`.
 */
function sortEntries(a: MiddlewareEntry, b: MiddlewareEntry): number {
  if (a.priority !== b.priority) return a.priority - b.priority;
  return a.insertionOrder - b.insertionOrder;
}

/**
 * Sleep for a given number of milliseconds.
 *
 * @param ms - Duration in milliseconds.
 * @returns A promise that resolves after `ms` milliseconds.
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
