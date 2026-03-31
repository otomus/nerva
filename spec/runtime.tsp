import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/**
 * Outcome status of an agent invocation.
 */
@jsonSchema
enum AgentStatus {
  /** Handler completed normally. */
  success: "success",

  /** Handler raised an unrecoverable error. */
  error: "error",

  /** Handler exceeded its deadline. */
  timeout: "timeout",

  /** Router selected the wrong handler for the input. */
  wrong_handler: "wrong_handler",

  /** Handler requires additional structured data to proceed. */
  needs_data: "needs_data",

  /** Handler requires credentials not yet provided. */
  needs_credentials: "needs_credentials",
}

/**
 * Immutable input passed to an agent handler.
 */
@jsonSchema
model AgentInput {
  /** The user message or piped output from a previous handler. */
  message: string;

  /** Structured arguments extracted by the router. */
  args?: Record<string>;

  /** Available tool specs for this invocation. */
  tools?: Record<string>[];

  /** Relevant conversation history entries. */
  history?: Record<string>[];
}

/**
 * Result from an agent handler invocation.
 */
@jsonSchema
model AgentResult {
  /** Outcome status of the invocation. */
  status: AgentStatus;

  /** The agent's response text. */
  output?: string;

  /** Structured data returned by the agent. */
  data?: Record<string>;

  /** Error message when status is ERROR, null otherwise. */
  error?: string | null;

  /** Name of the handler that produced this result. */
  handler?: string;
}

/**
 * Agent runtime interface — execute handlers with lifecycle management.
 *
 * Implementations handle timeout enforcement, circuit breaking,
 * structured output parsing, error classification, and streaming.
 */
@jsonSchema
model AgentRuntimeContract {
  /** Invoke a single handler. */
  invoke: {
    input: {
      /** Handler name (resolved from registry). */
      handler: string;

      /** Structured input for the handler. */
      input: AgentInput;

      /** Execution context. */
      ctx: ExecContext;
    };
    output: AgentResult;
  };

  /** Run handlers in sequence, piping output as the next input's message. */
  invoke_chain: {
    input: {
      /** Ordered list of handler names. */
      handlers: string[];

      /** Initial input for the first handler. */
      input: AgentInput;

      /** Execution context shared across the chain. */
      ctx: ExecContext;
    };
    output: AgentResult;
  };

  /** Invoke a handler from within another handler (agent-to-agent delegation). */
  delegate: {
    input: {
      /** Handler name to delegate to. */
      handler: string;

      /** Input for the delegated handler. */
      input: AgentInput;

      /** Parent's execution context. */
      parent_ctx: ExecContext;
    };
    output: AgentResult;
  };
}
