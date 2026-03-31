/**
 * Tool layer — discover, sandbox, and execute tools.
 *
 * Exports the core protocol (ToolManager), value types (ToolSpec, ToolResult),
 * and status enum (ToolStatus) used across the Nerva tool execution layer.
 *
 * @module tools
 */

import { ExecContext } from "../context.js";

// ---------------------------------------------------------------------------
// ToolStatus
// ---------------------------------------------------------------------------

/**
 * Outcome status of a tool call.
 *
 * Each value maps to a distinct failure mode so callers can branch
 * on status without inspecting error messages.
 */
export const ToolStatus = {
  /** Tool executed successfully. */
  SUCCESS: "success",
  /** Tool execution failed. */
  ERROR: "error",
  /** Caller lacks permission to use the tool. */
  PERMISSION_DENIED: "permission_denied",
  /** Requested tool does not exist. */
  NOT_FOUND: "not_found",
  /** Tool execution exceeded its deadline. */
  TIMEOUT: "timeout",
} as const;

export type ToolStatus = (typeof ToolStatus)[keyof typeof ToolStatus];

// ---------------------------------------------------------------------------
// ToolSpec
// ---------------------------------------------------------------------------

/**
 * Specification for a discoverable tool.
 */
export interface ToolSpec {
  /** Unique tool identifier. */
  readonly name: string;
  /** Human-readable description (used by LLM for selection). */
  readonly description: string;
  /** JSON Schema for tool input parameters. */
  readonly parameters: Readonly<Record<string, unknown>>;
  /** Roles required to use this tool. Empty set means unrestricted. */
  readonly requiredPermissions: ReadonlySet<string>;
}

/**
 * Create a ToolSpec with sensible defaults for optional fields.
 *
 * @param name - Unique tool identifier.
 * @param description - Human-readable description.
 * @param options - Optional parameters schema and required permissions.
 * @returns A ToolSpec instance.
 */
export function createToolSpec(
  name: string,
  description: string,
  options?: {
    parameters?: Record<string, unknown>;
    requiredPermissions?: ReadonlySet<string>;
  },
): ToolSpec {
  return {
    name,
    description,
    parameters: options?.parameters ?? {},
    requiredPermissions: options?.requiredPermissions ?? new Set<string>(),
  };
}

// ---------------------------------------------------------------------------
// ToolResult
// ---------------------------------------------------------------------------

/**
 * Result from executing a tool.
 */
export interface ToolResult {
  /** Outcome status. */
  readonly status: ToolStatus;
  /** Tool output (string or structured). */
  readonly output: string;
  /** Error message if status is not SUCCESS. */
  readonly error: string | null;
  /** Execution time in milliseconds. */
  readonly durationMs: number;
}

/**
 * Create a ToolResult with sensible defaults for optional fields.
 *
 * @param status - Outcome status.
 * @param options - Optional output, error, and durationMs overrides.
 * @returns A ToolResult instance.
 */
export function createToolResult(
  status: ToolStatus,
  options?: {
    output?: string;
    error?: string | null;
    durationMs?: number;
  },
): ToolResult {
  return {
    status,
    output: options?.output ?? "",
    error: options?.error ?? null,
    durationMs: options?.durationMs ?? 0,
  };
}

// ---------------------------------------------------------------------------
// ToolManager interface
// ---------------------------------------------------------------------------

/**
 * Discover and execute tools within sandbox constraints.
 *
 * Implementations must filter discovery results by the caller's
 * permissions and enforce sandboxing during execution.
 */
export interface ToolManager {
  /**
   * Return available tools filtered by the context's permissions.
   *
   * @param ctx - Execution context carrying identity and permission set.
   * @returns List of tool specs the current user/agent is allowed to access.
   */
  discover(ctx: ExecContext): Promise<ToolSpec[]>;

  /**
   * Execute a tool call within sandbox constraints.
   *
   * @param tool - Tool name to invoke.
   * @param args - Arguments matching the tool's parameter schema.
   * @param ctx - Execution context carrying identity and permission set.
   * @returns ToolResult with status and output.
   */
  call(tool: string, args: Record<string, unknown>, ctx: ExecContext): Promise<ToolResult>;
}
