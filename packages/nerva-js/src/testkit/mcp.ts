/**
 * MCP Armor testkit integration.
 *
 * Placeholder module — the full integration depends on the `@otomus/mcparmor`
 * package being available.
 *
 * @module testkit/mcp
 */

/**
 * Wrapper around mcparmor's ArmorTestHarness for Nerva-style assertions.
 *
 * Provides Nerva ToolResult objects and expectation-setting that
 * delegates to Armor's mock server internally.
 */
export class MCPTestHarness {
  /**
   * @param _armorConfig - Path to the armor config file.
   */
  /** Path to the armor config file, used when mcparmor is available. */
  readonly armorConfig: string;

  constructor(armorConfig = "./armor.json") {
    this.armorConfig = armorConfig;
  }

  /**
   * Start the MCP Armor test harness.
   *
   * @throws {Error} If mcparmor is not installed.
   */
  async start(): Promise<void> {
    throw new Error(
      "mcparmor is required for MCP testkit integration. " +
        "Install it with: npm install @otomus/mcparmor",
    );
  }

  /** Stop the MCP Armor test harness. */
  async stop(): Promise<void> {
    // No-op until mcparmor is available
  }
}
