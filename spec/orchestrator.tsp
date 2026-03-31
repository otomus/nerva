import "@typespec/json-schema";
import "./exec_context.tsp";
import "./responder.tsp";
import "./runtime.tsp";
import "./middleware.tsp";

using TypeSpec.JsonSchema;

/**
 * Orchestrator contract — wires all primitives into a single request handler.
 *
 * The orchestrator owns the full request lifecycle:
 * message -> context -> policy -> memory -> router -> runtime -> responder -> response
 */
@jsonSchema
model OrchestratorContract {
  /**
   * Process a message through the full pipeline.
   *
   * Steps: context creation, policy check, memory recall, middleware,
   * routing, handler invocation, memory storage, response formatting.
   */
  handle: {
    input: {
      /** User message. */
      message: string;

      /** Optional pre-built context. Created if omitted. */
      ctx?: ExecContext;

      /** Target channel. Defaults to API channel. */
      channel?: Channel;
    };
    output: Response;
  };

  /**
   * Process a message with streaming output.
   *
   * Same pipeline as handle() but yields chunks as they arrive.
   */
  stream: {
    input: {
      /** User message. */
      message: string;

      /** Optional pre-built context. */
      ctx?: ExecContext;

      /** Target channel. */
      channel?: Channel;
    };

    /** Individual text chunks yielded during processing. */
    output: string;
  };
}
