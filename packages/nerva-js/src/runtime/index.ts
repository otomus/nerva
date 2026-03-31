/**
 * Agent runtime — execute handlers with lifecycle management.
 *
 * Exports the core protocol (AgentRuntime), value types (AgentInput, AgentResult),
 * and status enum (AgentStatus) used across the Nerva execution layer.
 *
 * @module runtime
 */

import { ExecContext } from "../context.js";

export { CircuitBreaker, type CircuitBreakerConfig, CircuitState } from "./circuit-breaker.js";

// ---------------------------------------------------------------------------
// AgentStatus
// ---------------------------------------------------------------------------

/**
 * Outcome status of an agent invocation.
 *
 * Each value maps to a distinct failure mode so callers can branch
 * on status without inspecting error messages.
 */
export const AgentStatus = {
  /** Handler completed normally. */
  SUCCESS: "success",
  /** Handler raised an unrecoverable error. */
  ERROR: "error",
  /** Handler exceeded its deadline. */
  TIMEOUT: "timeout",
  /** Router selected the wrong handler for the input. */
  WRONG_HANDLER: "wrong_handler",
  /** Handler requires additional structured data to proceed. */
  NEEDS_DATA: "needs_data",
  /** Handler requires credentials not yet provided. */
  NEEDS_CREDENTIALS: "needs_credentials",
} as const;

export type AgentStatus = (typeof AgentStatus)[keyof typeof AgentStatus];

// ---------------------------------------------------------------------------
// AgentInput
// ---------------------------------------------------------------------------

/**
 * Immutable input passed to an agent handler.
 */
export interface AgentInput {
  /** The user message or piped output from a previous handler. */
  readonly message: string;
  /** Structured arguments extracted by the router. */
  readonly args: Readonly<Record<string, string>>;
  /** Available tool specs for this invocation. */
  readonly tools: ReadonlyArray<Readonly<Record<string, string>>>;
  /** Relevant conversation history entries. */
  readonly history: ReadonlyArray<Readonly<Record<string, string>>>;
}

/**
 * Create an AgentInput with sensible defaults for optional fields.
 *
 * @param message - The user message or piped output.
 * @param options - Optional args, tools, and history overrides.
 * @returns A frozen AgentInput object.
 */
export function createAgentInput(
  message: string,
  options?: {
    args?: Record<string, string>;
    tools?: Array<Record<string, string>>;
    history?: Array<Record<string, string>>;
  },
): AgentInput {
  return {
    message,
    args: options?.args ?? {},
    tools: options?.tools ?? [],
    history: options?.history ?? [],
  };
}

// ---------------------------------------------------------------------------
// AgentResult
// ---------------------------------------------------------------------------

/**
 * Result from an agent handler invocation.
 */
export interface AgentResult {
  /** Outcome status of the invocation. */
  readonly status: AgentStatus;
  /** The agent's response text. */
  readonly output: string;
  /** Structured data returned by the agent. */
  readonly data: Readonly<Record<string, string>>;
  /** Error message when status is ERROR, null otherwise. */
  readonly error: string | null;
  /** Name of the handler that produced this result. */
  readonly handler: string;
}

/**
 * Create an AgentResult with sensible defaults for optional fields.
 *
 * @param status - Outcome status.
 * @param options - Optional output, data, error, and handler overrides.
 * @returns An AgentResult object.
 */
export function createAgentResult(
  status: AgentStatus,
  options?: {
    output?: string;
    data?: Record<string, string>;
    error?: string | null;
    handler?: string;
  },
): AgentResult {
  return {
    status,
    output: options?.output ?? "",
    data: options?.data ?? {},
    error: options?.error ?? null,
    handler: options?.handler ?? "",
  };
}

// ---------------------------------------------------------------------------
// AgentRuntime interface
// ---------------------------------------------------------------------------

/**
 * Execute agent handlers with lifecycle management.
 *
 * Implementations handle timeout enforcement, circuit breaking,
 * structured output parsing, error classification, and streaming.
 */
export interface AgentRuntime {
  /**
   * Run a single handler.
   *
   * @param handler - Handler name (resolved from registry).
   * @param input - Structured input for the handler.
   * @param ctx - Execution context carrying permissions, trace, and config.
   * @returns AgentResult with status and output.
   */
  invoke(handler: string, input: AgentInput, ctx: ExecContext): Promise<AgentResult>;

  /**
   * Run handlers in sequence, piping each output as the next input's message.
   *
   * Stops early if any handler returns a non-SUCCESS status.
   *
   * @param handlers - Ordered list of handler names.
   * @param input - Initial input for the first handler.
   * @param ctx - Execution context shared across the chain.
   * @returns AgentResult from the last successfully executed handler.
   */
  invokeChain(handlers: string[], input: AgentInput, ctx: ExecContext): Promise<AgentResult>;

  /**
   * Invoke a handler from within another handler (agent-to-agent delegation).
   *
   * Creates a child ExecContext with inherited permissions and trace lineage.
   *
   * @param handler - Handler name to delegate to.
   * @param input - Input for the delegated handler.
   * @param parentCtx - Parent's execution context.
   * @returns AgentResult from the delegated handler.
   */
  delegate(handler: string, input: AgentInput, parentCtx: ExecContext): Promise<AgentResult>;
}
