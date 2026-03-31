/**
 * ExecContext — the connective tissue that flows through every Nerva primitive.
 *
 * Every operation in Nerva receives an ExecContext. It carries identity, permissions,
 * observability (spans/events), token accounting, cancellation, and streaming state.
 * This is primitive #0: the context object that all other primitives depend on.
 *
 * @module context
 */

import { randomUUID } from "node:crypto";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default scope for memory isolation when none is specified. */
const DEFAULT_MEMORY_SCOPE: Scope = "session";

// ---------------------------------------------------------------------------
// Scope (N-201)
// ---------------------------------------------------------------------------

/**
 * Memory isolation boundary for context data.
 *
 * Determines how far stored facts and state are visible:
 * - `user` — persists across sessions for the same user
 * - `session` — scoped to a single conversation session
 * - `agent` — private to the agent handling the request
 * - `global` — visible to all users and agents
 */
export type Scope = "user" | "session" | "agent" | "global";

/** All valid scope values for runtime validation. */
export const SCOPE_VALUES: readonly Scope[] = [
  "user",
  "session",
  "agent",
  "global",
] as const;

// ---------------------------------------------------------------------------
// Permissions (N-201)
// ---------------------------------------------------------------------------

/**
 * Immutable capability set governing what a context is allowed to do.
 *
 * Uses allowlists for tools and agents. A value of `null` means
 * "no restriction" (all allowed). An empty `ReadonlySet` means "none allowed".
 */
export interface Permissions {
  /** Role names assigned to this context (e.g. `"admin"`, `"user"`). */
  readonly roles: ReadonlySet<string>;

  /** Tool names this context may invoke, or `null` for unrestricted. */
  readonly allowedTools: ReadonlySet<string> | null;

  /** Agent names this context may delegate to, or `null` for unrestricted. */
  readonly allowedAgents: ReadonlySet<string> | null;

  /**
   * Check whether the given tool is permitted.
   *
   * @param toolName - Fully-qualified tool name to check.
   * @returns `true` if the tool is allowed (or if no restriction is set).
   */
  canUseTool(toolName: string): boolean;

  /**
   * Check whether delegation to the given agent is permitted.
   *
   * @param agentName - Agent identifier to check.
   * @returns `true` if the agent is allowed (or if no restriction is set).
   */
  canUseAgent(agentName: string): boolean;

  /**
   * Check whether the context carries a specific role.
   *
   * @param role - Role name to look for.
   * @returns `true` if the role is present.
   */
  hasRole(role: string): boolean;
}

/** Options for creating a {@link Permissions} instance. */
export interface PermissionsInit {
  readonly roles?: ReadonlySet<string>;
  readonly allowedTools?: ReadonlySet<string> | null;
  readonly allowedAgents?: ReadonlySet<string> | null;
}

/**
 * Create an immutable {@link Permissions} object.
 *
 * @param init - Optional initial values. Defaults to unrestricted.
 * @returns A frozen `Permissions` instance.
 */
export function createPermissions(init?: PermissionsInit): Permissions {
  const roles: ReadonlySet<string> = init?.roles ?? new Set<string>();
  const allowedTools: ReadonlySet<string> | null =
    init?.allowedTools ?? null;
  const allowedAgents: ReadonlySet<string> | null =
    init?.allowedAgents ?? null;

  return Object.freeze({
    roles,
    allowedTools,
    allowedAgents,

    canUseTool(toolName: string): boolean {
      if (allowedTools === null) return true;
      return allowedTools.has(toolName);
    },

    canUseAgent(agentName: string): boolean {
      if (allowedAgents === null) return true;
      return allowedAgents.has(agentName);
    },

    hasRole(role: string): boolean {
      return roles.has(role);
    },
  });
}

// ---------------------------------------------------------------------------
// TokenUsage (N-201)
// ---------------------------------------------------------------------------

/**
 * Immutable accumulator for LLM token consumption and estimated cost.
 *
 * Instances are never mutated — {@link TokenUsage.add} returns a new instance.
 */
export class TokenUsage {
  /** Number of tokens in the prompt/input. */
  readonly promptTokens: number;

  /** Number of tokens in the completion/output. */
  readonly completionTokens: number;

  /** Combined prompt + completion tokens. */
  readonly totalTokens: number;

  /** Estimated cost in US dollars. */
  readonly costUsd: number;

  /**
   * @param promptTokens - Prompt token count. Defaults to 0.
   * @param completionTokens - Completion token count. Defaults to 0.
   * @param totalTokens - Total token count. Defaults to 0.
   * @param costUsd - Estimated cost in USD. Defaults to 0.
   */
  constructor(
    promptTokens = 0,
    completionTokens = 0,
    totalTokens = 0,
    costUsd = 0,
  ) {
    this.promptTokens = promptTokens;
    this.completionTokens = completionTokens;
    this.totalTokens = totalTokens;
    this.costUsd = costUsd;
  }

  /**
   * Return a new `TokenUsage` that is the sum of this and `other`.
   * Neither operand is mutated.
   *
   * @param other - Token usage to add.
   * @returns A fresh `TokenUsage` with summed fields.
   */
  add(other: TokenUsage): TokenUsage {
    return new TokenUsage(
      this.promptTokens + other.promptTokens,
      this.completionTokens + other.completionTokens,
      this.totalTokens + other.totalTokens,
      this.costUsd + other.costUsd,
    );
  }
}

// ---------------------------------------------------------------------------
// Span & Event (N-201)
// ---------------------------------------------------------------------------

/**
 * A timed segment of work within a request's lifecycle.
 *
 * Spans form a tree: each span may have a `parentId` pointing to the
 * span that created it. A `null` parent means a root span.
 */
export interface Span {
  /** Unique identifier for this span. */
  readonly spanId: string;

  /** Human-readable label (e.g. `"llm.call"`, `"tool.invoke"`). */
  readonly name: string;

  /** Span ID of the parent, or `null` for root spans. */
  readonly parentId: string | null;

  /** Unix timestamp (seconds) when the span started. */
  readonly startedAt: number;

  /** Unix timestamp (seconds) when the span ended, or `null` if still open. */
  readonly endedAt: number | null;

  /** Arbitrary key-value metadata attached to the span. */
  readonly attributes: Readonly<Record<string, string>>;
}

/**
 * A point-in-time occurrence recorded within a context.
 *
 * Events are simpler than spans — they have no duration, just a timestamp
 * and descriptive metadata.
 */
export interface Event {
  /** Unix timestamp (seconds) of the event. */
  readonly timestamp: number;

  /** Human-readable label (e.g. `"policy.denied"`, `"stream.started"`). */
  readonly name: string;

  /** Arbitrary key-value metadata. */
  readonly attributes: Readonly<Record<string, string>>;
}

// ---------------------------------------------------------------------------
// StreamSink (N-202)
// ---------------------------------------------------------------------------

/**
 * Protocol for pushing incremental output chunks to a consumer.
 *
 * Implementations may write to an HTTP response, a WebSocket, a queue,
 * or an in-memory buffer (for testing).
 */
export interface StreamSink {
  /**
   * Send a single chunk of output.
   *
   * @param chunk - Text fragment to push downstream.
   */
  push(chunk: string): Promise<void>;

  /** Signal that no more chunks will be sent. */
  close(): Promise<void>;
}

/**
 * In-memory {@link StreamSink} implementation for testing.
 *
 * Collects all pushed chunks into a list so tests can assert
 * on the full output without I/O.
 */
export class InMemoryStreamSink implements StreamSink {
  /** All chunks pushed so far, in order. */
  readonly chunks: string[] = [];

  /** Whether {@link close} has been called. */
  private _closed = false;

  /** Whether the sink has been closed. */
  get closed(): boolean {
    return this._closed;
  }

  /**
   * Append a chunk to the internal buffer.
   *
   * @param chunk - Text fragment to record.
   * @throws {Error} If the sink has already been closed.
   */
  async push(chunk: string): Promise<void> {
    if (this._closed) {
      throw new Error("Cannot push to a closed StreamSink");
    }
    this.chunks.push(chunk);
  }

  /**
   * Mark the sink as closed. Subsequent pushes will throw.
   *
   * @throws {Error} If the sink has already been closed.
   */
  async close(): Promise<void> {
    if (this._closed) {
      throw new Error("StreamSink is already closed");
    }
    this._closed = true;
  }
}

// ---------------------------------------------------------------------------
// ExecContext (N-202)
// ---------------------------------------------------------------------------

/** Options for {@link ExecContext.create}. */
export interface ExecContextCreateOptions {
  /** Authenticated user identifier, or `null` for anonymous. */
  readonly userId?: string | null;

  /** Conversation/session identifier, or `null`. */
  readonly sessionId?: string | null;

  /** Capability set. Defaults to an unrestricted `Permissions`. */
  readonly permissions?: Permissions;

  /** Memory isolation boundary. Defaults to `"session"`. */
  readonly memoryScope?: Scope;

  /** Seconds from now until the context times out, or `null`. */
  readonly timeoutSeconds?: number | null;

  /** Optional sink for incremental output. */
  readonly stream?: StreamSink | null;
}

/**
 * Execution context that flows through every Nerva primitive.
 *
 * Carries identity, permissions, observability (spans and events),
 * token accounting, cancellation signalling, and an optional stream sink.
 * Contexts are created via the static {@link ExecContext.create} factory
 * and can spawn children for sub-operations via {@link ExecContext.child}.
 */
export class ExecContext {
  /** Unique identifier for this individual request. */
  readonly requestId: string;

  /** Groups related requests into a single trace. */
  readonly traceId: string;

  /** Authenticated user, or `null` for anonymous. */
  readonly userId: string | null;

  /** Conversation/session identifier, or `null`. */
  readonly sessionId: string | null;

  /** Capability set governing tool/agent access. */
  readonly permissions: Permissions;

  /** Isolation boundary for stored state. */
  readonly memoryScope: Scope;

  /** Ordered list of timed work segments. */
  readonly spans: Span[];

  /** Ordered list of point-in-time occurrences. */
  readonly events: Event[];

  /** Accumulated LLM token consumption. */
  tokenUsage: TokenUsage;

  /** Unix timestamp when this context was created. */
  readonly createdAt: number;

  /** Unix timestamp after which the context is timed out, or `null`. */
  readonly timeoutAt: number | null;

  /** Resolves when cancellation is signalled. */
  private readonly _cancelController: AbortController;

  /** Optional sink for incremental output. */
  stream: StreamSink | null;

  /** Arbitrary string tags for policy conditions and routing. */
  readonly metadata: Record<string, string>;

  /** Current delegation depth (0 = root, incremented by `child()`). */
  readonly depth: number;

  private constructor(params: {
    requestId: string;
    traceId: string;
    userId: string | null;
    sessionId: string | null;
    permissions: Permissions;
    memoryScope: Scope;
    spans: Span[];
    events: Event[];
    tokenUsage: TokenUsage;
    createdAt: number;
    timeoutAt: number | null;
    cancelController: AbortController;
    stream: StreamSink | null;
    metadata: Record<string, string>;
    depth: number;
  }) {
    this.requestId = params.requestId;
    this.traceId = params.traceId;
    this.userId = params.userId;
    this.sessionId = params.sessionId;
    this.permissions = params.permissions;
    this.memoryScope = params.memoryScope;
    this.spans = params.spans;
    this.events = params.events;
    this.tokenUsage = params.tokenUsage;
    this.createdAt = params.createdAt;
    this.timeoutAt = params.timeoutAt;
    this._cancelController = params.cancelController;
    this.stream = params.stream;
    this.metadata = params.metadata;
    this.depth = params.depth;
  }

  /**
   * Create a new root execution context.
   *
   * Generates fresh `requestId` and `traceId`, sets the clock,
   * and initialises all accumulators to empty.
   *
   * @param options - Optional creation parameters.
   * @returns A fully initialised `ExecContext` ready for use.
   */
  static create(options?: ExecContextCreateOptions): ExecContext {
    const now = Date.now() / 1000;
    const timeoutSeconds = options?.timeoutSeconds ?? null;
    const timeoutAt =
      timeoutSeconds !== null ? now + timeoutSeconds : null;

    return new ExecContext({
      requestId: randomUUID().replace(/-/g, ""),
      traceId: randomUUID().replace(/-/g, ""),
      userId: options?.userId ?? null,
      sessionId: options?.sessionId ?? null,
      permissions: options?.permissions ?? createPermissions(),
      memoryScope: options?.memoryScope ?? DEFAULT_MEMORY_SCOPE,
      spans: [],
      events: [],
      tokenUsage: new TokenUsage(),
      createdAt: now,
      timeoutAt,
      cancelController: new AbortController(),
      stream: options?.stream ?? null,
      metadata: {},
      depth: 0,
    });
  }

  /**
   * Create a child context for delegation to a sub-handler.
   *
   * The child inherits the parent's trace, permissions, memory scope,
   * timeout, cancellation signal, and stream — but gets a fresh
   * `requestId` and a new root span named after `handlerName`.
   *
   * @param handlerName - Label for the child operation (used as the span name).
   * @returns A new `ExecContext` linked to the same trace as the parent.
   */
  child(handlerName: string): ExecContext {
    const now = Date.now() / 1000;
    const rootSpan: Span = {
      spanId: randomUUID().replace(/-/g, ""),
      name: handlerName,
      parentId: this.requestId,
      startedAt: now,
      endedAt: null,
      attributes: {},
    };

    return new ExecContext({
      requestId: randomUUID().replace(/-/g, ""),
      traceId: this.traceId,
      userId: this.userId,
      sessionId: this.sessionId,
      permissions: this.permissions,
      memoryScope: this.memoryScope,
      spans: [rootSpan],
      events: [],
      tokenUsage: new TokenUsage(),
      createdAt: now,
      timeoutAt: this.timeoutAt,
      cancelController: this._cancelController,
      stream: this.stream,
      metadata: { ...this.metadata },
      depth: this.depth + 1,
    });
  }

  // -- Cancellation -------------------------------------------------------

  /**
   * The `AbortSignal` that fires when this context is cancelled.
   *
   * @returns The signal tied to this context's cancel controller.
   */
  get cancelSignal(): AbortSignal {
    return this._cancelController.signal;
  }

  /**
   * Signal cancellation for this context (and any children sharing
   * the same controller).
   */
  cancel(): void {
    this._cancelController.abort();
  }

  // -- Query helpers ------------------------------------------------------

  /**
   * Check whether the context has exceeded its timeout.
   *
   * @returns `true` if a timeout was set and the current time is past it.
   */
  isTimedOut(): boolean {
    if (this.timeoutAt === null) return false;
    return Date.now() / 1000 > this.timeoutAt;
  }

  /**
   * Check whether cancellation has been signalled.
   *
   * @returns `true` if the cancel controller has been aborted.
   */
  isCancelled(): boolean {
    return this._cancelController.signal.aborted;
  }

  /**
   * Seconds elapsed since this context was created.
   *
   * @returns Wall-clock seconds since `createdAt`.
   */
  elapsedSeconds(): number {
    return Date.now() / 1000 - this.createdAt;
  }

  // -- Mutation helpers (append-only) -------------------------------------

  /**
   * Start a new span and append it to this context's span list.
   *
   * The span is created with `endedAt = null` (still open). Callers
   * are responsible for closing it when the work completes.
   *
   * @param name - Human-readable label for the span.
   * @returns The newly created `Span`.
   */
  addSpan(name: string): Span {
    const span: Span = {
      spanId: randomUUID().replace(/-/g, ""),
      name,
      parentId: this.requestId,
      startedAt: Date.now() / 1000,
      endedAt: null,
      attributes: {},
    };
    this.spans.push(span);
    return span;
  }

  /**
   * Record a point-in-time event in this context.
   *
   * @param name - Human-readable label for the event.
   * @param attributes - Arbitrary string key-value pairs.
   * @returns The newly created `Event`.
   */
  addEvent(name: string, attributes: Record<string, string> = {}): Event {
    const event: Event = {
      timestamp: Date.now() / 1000,
      name,
      attributes: { ...attributes },
    };
    this.events.push(event);
    return event;
  }

  /**
   * Accumulate token usage into this context's running total.
   *
   * @param usage - Token counts and cost to add.
   */
  recordTokens(usage: TokenUsage): void {
    this.tokenUsage = this.tokenUsage.add(usage);
  }
}
