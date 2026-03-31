import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/**
 * Outcome status of a tool call.
 *
 * Each value maps to a distinct failure mode so callers can branch
 * on status without inspecting error messages.
 */
@jsonSchema
enum ToolStatus {
  success: "success",
  error: "error",
  permission_denied: "permission_denied",
  not_found: "not_found",
  timeout: "timeout",
}

/**
 * Specification for a discoverable tool.
 */
@jsonSchema
model ToolSpec {
  /** Unique tool identifier. */
  name: string;

  /** Human-readable description (used by LLM for selection). */
  description: string;

  /** JSON Schema for tool input parameters. */
  parameters?: Record<unknown>;

  /** Roles required to use this tool. */
  required_permissions?: string[];
}

/**
 * Result from executing a tool.
 */
@jsonSchema
model ToolResult {
  /** Outcome status. */
  status: ToolStatus;

  /** Tool output (string or structured). */
  output?: string;

  /** Error message if status is not SUCCESS. */
  error?: string | null;

  /** Execution time in milliseconds. */
  duration_ms?: float64;
}

/**
 * Tool manager interface — discover and execute tools within sandbox constraints.
 *
 * Implementations must filter discovery results by the caller's
 * permissions and enforce sandboxing during execution.
 */
@jsonSchema
model ToolManagerContract {
  /** Return available tools filtered by the context's permissions. */
  discover: {
    input: {
      /** Execution context carrying identity and permission set. */
      ctx: ExecContext;
    };
    output: ToolSpec[];
  };

  /** Execute a tool call within sandbox constraints. */
  call: {
    input: {
      /** Tool name to invoke. */
      tool: string;

      /** Arguments matching the tool's parameter schema. */
      args: Record<unknown>;

      /** Execution context. */
      ctx: ExecContext;
    };
    output: ToolResult;
  };
}
