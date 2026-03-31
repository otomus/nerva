/**
 * Composite tool manager — combines multiple ToolManagers into one.
 *
 * Merges tool discovery across managers and routes `call()` to the
 * owning manager. Priority: the first manager to register a tool name wins.
 *
 * @module tools/composite
 */

import type { ExecContext } from "../context.js";
import {
  ToolStatus,
  type ToolSpec,
  type ToolResult,
  type ToolManager,
  createToolResult,
} from "./index.js";

// ---------------------------------------------------------------------------
// CompositeToolManager
// ---------------------------------------------------------------------------

/**
 * Combines multiple ToolManagers into a single unified interface.
 *
 * On `discover()`, merges tools from all managers, deduplicating by name
 * (first manager to claim a name wins). On `call()`, routes to the
 * manager that owns the requested tool.
 *
 * @example
 * ```ts
 * const composite = new CompositeToolManager([mcpManager, functionManager]);
 * const tools = await composite.discover(ctx);
 * const result = await composite.call("calculator", { a: "1", b: "2" }, ctx);
 * ```
 */
export class CompositeToolManager implements ToolManager {
  private readonly _managers: readonly ToolManager[];
  private readonly _toolOwner = new Map<string, ToolManager>();

  /**
   * @param managers - Ordered list of tool managers. Earlier managers have higher priority.
   */
  constructor(managers: readonly ToolManager[]) {
    this._managers = managers;
  }

  /**
   * Discover tools from all managers, deduplicating by name.
   *
   * The first manager to provide a tool with a given name wins.
   * Subsequent managers with the same tool name are skipped.
   *
   * @param ctx - Execution context carrying identity and permission set.
   * @returns Merged and deduplicated list of tool specs.
   */
  async discover(ctx: ExecContext): Promise<ToolSpec[]> {
    const seen = new Set<string>();
    const result: ToolSpec[] = [];

    for (const manager of this._managers) {
      const tools = await manager.discover(ctx);
      for (const tool of tools) {
        if (seen.has(tool.name)) {
          continue;
        }
        seen.add(tool.name);
        result.push(tool);
        this._toolOwner.set(tool.name, manager);
      }
    }

    return result;
  }

  /**
   * Execute a tool call, routing to the owning manager.
   *
   * If the tool has not been seen during `discover()`, attempts to find
   * the tool by calling `discover()` first. Returns NOT_FOUND if no
   * manager owns the requested tool.
   *
   * @param tool - Tool name to invoke.
   * @param args - Arguments matching the tool's parameter schema.
   * @param ctx - Execution context carrying identity and permission set.
   * @returns ToolResult from the owning manager.
   */
  async call(
    tool: string,
    args: Record<string, unknown>,
    ctx: ExecContext,
  ): Promise<ToolResult> {
    let owner = this._toolOwner.get(tool);

    if (owner === undefined) {
      // Tool not yet discovered — try a fresh discovery pass
      await this.discover(ctx);
      owner = this._toolOwner.get(tool);
    }

    if (owner === undefined) {
      return createToolResult(ToolStatus.NOT_FOUND, {
        error: `Tool '${tool}' not found in any manager`,
      });
    }

    return owner.call(tool, args, ctx);
  }
}
