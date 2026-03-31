import "@typespec/json-schema";
import "./exec_context.tsp";
import "./runtime.tsp";

using TypeSpec.JsonSchema;

/**
 * Target channel for a response.
 *
 * Describes the capabilities and constraints of the delivery channel
 * so the responder can adapt output formatting accordingly.
 */
@jsonSchema
model Channel {
  /** Channel identifier (e.g. "slack", "api", "websocket"). */
  name: string;

  /** Whether the channel renders markdown. */
  supports_markdown?: boolean;

  /** Whether the channel can display images/files. */
  supports_media?: boolean;

  /** Maximum response length in characters (0 = unlimited). */
  max_length?: int32;
}

/**
 * Formatted response ready for delivery.
 */
@jsonSchema
model Response {
  /** The formatted response text. */
  text: string;

  /** Target channel this response was formatted for. */
  channel: Channel;

  /** Optional media attachments (URLs or base64 strings). */
  media?: string[];

  /** Extra key-value metadata for the channel. */
  metadata?: Record<string>;
}

/**
 * Responder interface — format agent output for a target channel.
 *
 * Implementations adapt the raw AgentResult into a Response
 * appropriate for the delivery channel (truncation, markdown stripping,
 * media attachment, etc.).
 */
@jsonSchema
model ResponderContract {
  /** Format agent output for the target channel. */
  format: {
    input: {
      /** Raw agent result from the runtime. */
      output: AgentResult;

      /** Target delivery channel. */
      channel: Channel;

      /** Execution context carrying identity and permissions. */
      ctx: ExecContext;
    };
    output: Response;
  };
}
