/**
 * Function-based tools — register JavaScript/TypeScript functions as tools.
 *
 * Provides a `tool()` registration method (decorator-like pattern) that wraps
 * plain functions as discoverable, permission-filtered tools conforming to the
 * `ToolManager` protocol.
 *
 * @example
 * ```ts
 * const mgr = new FunctionToolManager();
 *
 * mgr.tool("add", "Add two numbers", {
 *   parameters: { type: "object", properties: { a: { type: "integer" }, b: { type: "integer" } }, required: ["a", "b"] },
 * }, (args: { a: number; b: number }) => args.a + args.b);
 *
 * const specs = await mgr.discover(ctx);
 * const result = await mgr.call("add", { a: 1, b: 2 }, ctx);
 * ```
 *
 * @module tools/function
 */

import { ExecContext } from "../context.js";
import {
  ToolStatus,
  type ToolSpec,
  type ToolResult,
  type ToolManager,
  createToolSpec,
  createToolResult,
} from "./index.js";

// ---------------------------------------------------------------------------
// ToolFunction type
// ---------------------------------------------------------------------------

/**
 * A function that can be registered as a tool.
 *
 * Accepts a record of arguments and returns a value (sync or async).
 * The return value is stringified as the tool output.
 */
export type ToolFunction = (args: Record<string, unknown>) => unknown | Promise<unknown>;

// ---------------------------------------------------------------------------
// RegisteredTool
// ---------------------------------------------------------------------------

/**
 * Internal record for a function registered as a tool.
 */
interface RegisteredTool {
  /** Unique tool identifier. */
  readonly name: string;
  /** Human-readable description. */
  readonly description: string;
  /** The underlying callable. */
  readonly fn: ToolFunction;
  /** JSON Schema describing the function's parameters. */
  readonly parameters: Record<string, unknown>;
  /** Roles required to use this tool. */
  readonly requiredPermissions: ReadonlySet<string>;
}

// ---------------------------------------------------------------------------
// FunctionToolManager
// ---------------------------------------------------------------------------

/**
 * Tool manager that wraps plain functions as tools.
 *
 * Register functions with `tool()`, then use `discover()` and `call()`
 * to interact with them through the standard `ToolManager` protocol.
 */
export class FunctionToolManager implements ToolManager {
  private readonly _tools = new Map<string, RegisteredTool>();

  // -- Registration ---------------------------------------------------------

  /**
   * Register a function as a tool.
   *
   * @param name - Unique tool identifier.
   * @param description - Human-readable description shown to the LLM.
   * @param options - Optional parameters schema and required permissions.
   * @param fn - The function to execute when the tool is called.
   * @throws {Error} If a tool with the same name is already registered.
   */
  tool(
    name: string,
    description: string,
    options: {
      parameters?: Record<string, unknown>;
      requiredPermissions?: ReadonlySet<string>;
    } | undefined,
    fn: ToolFunction,
  ): void {
    if (this._tools.has(name)) {
      throw new Error(`Tool '${name}' is already registered`);
    }

    const registered: RegisteredTool = {
      name,
      description,
      fn,
      parameters: options?.parameters ?? {},
      requiredPermissions: options?.requiredPermissions ?? new Set<string>(),
    };

    this._tools.set(name, registered);
  }

  // -- Protocol implementation -----------------------------------------------

  /**
   * Return tool specs the caller is permitted to use.
   *
   * Filters registered tools by the context's permission checks
   * and by matching the caller's roles against each tool's
   * `requiredPermissions`.
   *
   * @param ctx - Execution context carrying identity and permission set.
   * @returns List of ToolSpec instances for accessible tools.
   */
  async discover(ctx: ExecContext): Promise<ToolSpec[]> {
    const specs: ToolSpec[] = [];

    for (const registered of this._tools.values()) {
      if (!ctx.permissions.canUseTool(registered.name)) continue;
      if (!hasRequiredRoles(ctx, registered.requiredPermissions)) continue;

      specs.push(
        createToolSpec(registered.name, registered.description, {
          parameters: registered.parameters,
          requiredPermissions: registered.requiredPermissions,
        }),
      );
    }

    return specs;
  }

  /**
   * Execute a registered tool by name.
   *
   * Validates existence and permissions before invoking the function.
   *
   * @param tool - Tool name to invoke.
   * @param args - Arguments forwarded to the underlying function.
   * @param ctx - Execution context carrying identity and permission set.
   * @returns ToolResult with the outcome status, output, and timing.
   */
  async call(
    tool: string,
    args: Record<string, unknown>,
    ctx: ExecContext,
  ): Promise<ToolResult> {
    const registered = this._tools.get(tool);
    if (registered === undefined) {
      return createToolResult(ToolStatus.NOT_FOUND, {
        error: `Tool '${tool}' not found`,
      });
    }

    if (!ctx.permissions.canUseTool(tool)) {
      return createToolResult(ToolStatus.PERMISSION_DENIED, {
        error: `Permission denied for tool '${tool}'`,
      });
    }

    if (!hasRequiredRoles(ctx, registered.requiredPermissions)) {
      return createToolResult(ToolStatus.PERMISSION_DENIED, {
        error: `Missing required role for tool '${tool}'`,
      });
    }

    return executeTool(registered, args);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Check whether the context carries all required roles.
 *
 * An empty required set means the tool is unrestricted.
 *
 * @param ctx - Execution context to inspect.
 * @param required - Role names that must all be present.
 * @returns `true` if every required role is present (or none are required).
 */
function hasRequiredRoles(ctx: ExecContext, required: ReadonlySet<string>): boolean {
  if (required.size === 0) return true;

  for (const role of required) {
    if (!ctx.permissions.hasRole(role)) return false;
  }
  return true;
}

/**
 * Invoke the underlying function and wrap the outcome in a ToolResult.
 *
 * Both sync and async functions are handled -- sync return values are
 * awaited as a no-op, while async functions are awaited normally.
 *
 * @param registered - The registered tool record.
 * @param args - Arguments to forward to the function.
 * @returns ToolResult with status, output, and execution duration.
 */
async function executeTool(
  registered: RegisteredTool,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  const start = performance.now();
  try {
    const rawOutput: unknown = await registered.fn(args);
    const elapsed = performance.now() - start;
    return createToolResult(ToolStatus.SUCCESS, {
      output: String(rawOutput),
      durationMs: elapsed,
    });
  } catch (err: unknown) {
    const elapsed = performance.now() - start;
    const errorMessage = formatError(err);
    return createToolResult(ToolStatus.ERROR, {
      error: errorMessage,
      durationMs: elapsed,
    });
  }
}

/**
 * Format an unknown caught value into a descriptive error string.
 *
 * @param err - The caught value.
 * @returns A string in the format `"ErrorType: message"` or `"Unknown error: <value>"`.
 */
function formatError(err: unknown): string {
  if (err instanceof Error) {
    return `${err.constructor.name}: ${err.message}`;
  }
  return `Unknown error: ${String(err)}`;
}
