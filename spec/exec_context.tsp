import "@typespec/json-schema";

using TypeSpec.JsonSchema;

/**
 * Memory isolation boundary for context data.
 *
 * Determines how far stored facts and state are visible:
 * - USER: persists across sessions for the same user
 * - SESSION: scoped to a single conversation session
 * - AGENT: private to the agent handling the request
 * - GLOBAL: visible to all users and agents
 */
@jsonSchema
enum Scope {
  user: "user",
  session: "session",
  agent: "agent",
  global: "global",
}

/**
 * Immutable capability set governing what a context is allowed to do.
 *
 * Uses allowlists for tools and agents. A null value means
 * "no restriction" (all allowed). An empty array means "none allowed".
 */
@jsonSchema
model Permissions {
  /** Role names assigned to this context (e.g. "admin", "user"). */
  roles: string[];

  /** Tool names this context may invoke, or null for unrestricted. */
  allowed_tools?: string[] | null;

  /** Agent names this context may delegate to, or null for unrestricted. */
  allowed_agents?: string[] | null;
}

/**
 * Accumulator for LLM token consumption and estimated cost.
 */
@jsonSchema
model TokenUsage {
  /** Number of tokens in the prompt/input. */
  prompt_tokens: int32;

  /** Number of tokens in the completion/output. */
  completion_tokens: int32;

  /** Combined prompt + completion tokens. */
  total_tokens: int32;

  /** Estimated cost in US dollars. */
  cost_usd: float64;
}

/**
 * A timed segment of work within a request's lifecycle.
 *
 * Spans form a tree: each span may have a parent_id pointing to
 * the span that created it. A null parent means a root span.
 */
@jsonSchema
model Span {
  /** Unique identifier for this span. */
  span_id: string;

  /** Human-readable label (e.g. "llm.call", "tool.invoke"). */
  name: string;

  /** Span ID of the parent, or null for root spans. */
  parent_id: string | null;

  /** Unix timestamp when the span started. */
  started_at: float64;

  /** Unix timestamp when the span ended, or null if still open. */
  ended_at?: float64 | null;

  /** Arbitrary key-value metadata attached to the span. */
  attributes: Record<string>;
}

/**
 * A point-in-time occurrence recorded within a context.
 *
 * Events have no duration — just a timestamp and descriptive metadata.
 */
@jsonSchema
model Event {
  /** Unix timestamp of the event. */
  timestamp: float64;

  /** Human-readable label (e.g. "policy.denied", "stream.started"). */
  name: string;

  /** Arbitrary key-value metadata. */
  attributes: Record<string>;
}

/**
 * Execution context that flows through every Nerva primitive.
 *
 * Carries identity, permissions, observability (spans and events),
 * token accounting, cancellation state, and streaming configuration.
 */
@jsonSchema
model ExecContext {
  /** Unique identifier for this individual request. */
  request_id: string;

  /** Groups related requests into a single trace. */
  trace_id: string;

  /** Authenticated user, or null for anonymous. */
  user_id: string | null;

  /** Conversation/session identifier, or null. */
  session_id: string | null;

  /** Capability set governing tool/agent access. */
  permissions: Permissions;

  /** Isolation boundary for stored state. */
  memory_scope: Scope;

  /** Ordered list of timed work segments. */
  spans: Span[];

  /** Ordered list of point-in-time occurrences. */
  events: Event[];

  /** Accumulated LLM token consumption. */
  token_usage: TokenUsage;

  /** Unix timestamp when this context was created. */
  created_at: float64;

  /** Unix timestamp after which the context is timed out, or null. */
  timeout_at?: float64 | null;

  /** Whether cancellation has been signalled. */
  cancelled: boolean;

  /** Arbitrary string tags for policy conditions and routing. */
  metadata: Record<string>;
}
