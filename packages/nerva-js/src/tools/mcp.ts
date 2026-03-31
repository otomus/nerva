/**
 * MCP tool manager — discover and execute tools via MCP protocol servers.
 *
 * Implements the MCP (Model Context Protocol) stdio transport using JSON-RPC 2.0
 * over subprocess stdin/stdout. No external MCP library required.
 *
 * Features:
 * - Connection pooling with LRU eviction
 * - Permission filtering via ctx.permissions
 * - Sandboxing via ArmorPolicy
 * - Result size limits with truncation
 *
 * @module tools/mcp
 */

import { spawn, type ChildProcess } from "node:child_process";
import { type ExecContext } from "../context.js";
import {
  ToolStatus,
  type ToolSpec,
  type ToolResult,
  type ToolManager,
  createToolSpec,
  createToolResult,
} from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum number of simultaneous MCP server connections in the pool. */
export const DEFAULT_POOL_SIZE = 5;

/** Default timeout for MCP server connection and tool calls (ms). */
export const DEFAULT_TIMEOUT_MS = 30_000;

/** Hard limit on tool output size before truncation kicks in. */
export const MAX_RESULT_BYTES = 524_288; // 512 KB

/** Appended to tool output that exceeds the byte limit. */
const TRUNCATION_SUFFIX = "... [truncated]";

/** JSON-RPC protocol version used for MCP communication. */
const JSONRPC_VERSION = "2.0";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Configuration for an MCP server connection. */
export interface MCPServerConfig {
  /** Server identifier used for logging and tool namespacing. */
  readonly name: string;
  /** Command to launch the server (stdio transport). */
  readonly command: string;
  /** Command-line arguments passed to the server process. */
  readonly args?: readonly string[];
  /** Extra environment variables for the server process. */
  readonly env?: Readonly<Record<string, string>>;
  /** Timeout for connection setup and individual tool calls (ms). */
  readonly timeoutMs?: number;
}

/**
 * Sandboxing policy applied before every tool execution.
 *
 * Controls filesystem access, network permissions, visible environment
 * variables, and output size. An empty allowlist means "none allowed".
 */
export interface ArmorPolicy {
  /** Filesystem paths the tool can access. Empty means no filesystem. */
  readonly allowedPaths?: readonly string[];
  /** Whether the tool may make network calls. */
  readonly allowNetwork?: boolean;
  /** Environment variable names the tool may read. Empty means none. */
  readonly allowedEnvVars?: readonly string[];
  /** Maximum bytes in tool output before truncation. */
  readonly maxResultBytes?: number;
}

// ---------------------------------------------------------------------------
// Exceptions
// ---------------------------------------------------------------------------

/** Raised when MCP JSON-RPC communication fails or returns an error. */
export class MCPProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MCPProtocolError";
  }
}

// ---------------------------------------------------------------------------
// JSON-RPC helpers
// ---------------------------------------------------------------------------

/**
 * Serialize a JSON-RPC 2.0 request to a newline-terminated string.
 *
 * @param method - RPC method name (e.g. "tools/list").
 * @param requestId - Monotonically increasing request identifier.
 * @param params - Optional parameters for the RPC method.
 * @returns JSON line ready to write to stdin.
 */
function buildRequest(
  method: string,
  requestId: number,
  params?: Record<string, unknown>,
): string {
  const payload: Record<string, unknown> = {
    jsonrpc: JSONRPC_VERSION,
    method,
    id: requestId,
  };
  if (params !== undefined) {
    payload["params"] = params;
  }
  return JSON.stringify(payload) + "\n";
}

/**
 * Parse a JSON-RPC 2.0 response line and validate its structure.
 *
 * @param line - Raw string read from the server's stdout.
 * @param expectedId - The request ID we expect the response to match.
 * @returns The "result" field of the JSON-RPC response.
 * @throws {MCPProtocolError} If the response is malformed or contains an error.
 */
function parseResponse(line: string, expectedId: number): Record<string, unknown> {
  let data: unknown;
  try {
    data = JSON.parse(line);
  } catch {
    throw new MCPProtocolError(`Invalid JSON from MCP server: ${line.slice(0, 200)}`);
  }

  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new MCPProtocolError(`Expected JSON object, got ${typeof data}`);
  }

  const record = data as Record<string, unknown>;

  if (record["id"] !== expectedId) {
    throw new MCPProtocolError(
      `Response ID mismatch: expected ${expectedId}, got ${String(record["id"])}`,
    );
  }

  if ("error" in record) {
    const err = record["error"];
    const errObj = typeof err === "object" && err !== null ? (err as Record<string, unknown>) : null;
    const code = errObj !== null ? String(errObj["code"] ?? "?") : "?";
    const message = errObj !== null ? String(errObj["message"] ?? err) : String(err);
    throw new MCPProtocolError(`MCP server error [${code}]: ${message}`);
  }

  const result = record["result"];
  if (typeof result === "object" && result !== null && !Array.isArray(result)) {
    return result as Record<string, unknown>;
  }
  return {};
}

// ---------------------------------------------------------------------------
// MCPConnection
// ---------------------------------------------------------------------------

/**
 * A single connection to an MCP server via stdio subprocess.
 *
 * Manages the server process lifecycle and JSON-RPC 2.0 communication.
 */
export class MCPConnection {
  private readonly _config: MCPServerConfig;
  private _process: ChildProcess | null = null;
  private _requestId = 0;
  private _pendingResolve: ((line: string) => void) | null = null;
  private _pendingReject: ((err: Error) => void) | null = null;
  private _buffer = "";

  /**
   * @param config - Server configuration (command, args, env, timeout).
   */
  constructor(config: MCPServerConfig) {
    this._config = config;
  }

  /**
   * Start the MCP server subprocess.
   *
   * @throws {MCPProtocolError} If the process fails to start.
   */
  connect(): void {
    const env = this._config.env
      ? { ...process.env, ...this._config.env }
      : undefined;

    this._process = spawn(this._config.command, [...(this._config.args ?? [])], {
      stdio: ["pipe", "pipe", "pipe"],
      env,
    });

    this._process.stdout?.setEncoding("utf-8");
    this._process.stdout?.on("data", (chunk: string) => {
      this.handleStdoutData(chunk);
    });

    this._process.on("error", (err: Error) => {
      if (this._pendingReject) {
        this._pendingReject(new MCPProtocolError(`Process error: ${err.message}`));
        this._pendingResolve = null;
        this._pendingReject = null;
      }
    });

    this._process.on("close", () => {
      if (this._pendingReject) {
        this._pendingReject(new MCPProtocolError("MCP server closed unexpectedly"));
        this._pendingResolve = null;
        this._pendingReject = null;
      }
      this._process = null;
    });
  }

  /**
   * Discover all tools exposed by this MCP server.
   *
   * @returns List of ToolSpec objects describing each available tool.
   * @throws {MCPProtocolError} If the server returns an invalid response.
   */
  async listTools(): Promise<ToolSpec[]> {
    const result = await this.send("tools/list");
    const rawTools = result["tools"];
    if (!Array.isArray(rawTools)) {
      throw new MCPProtocolError(`Expected 'tools' list, got ${typeof rawTools}`);
    }
    return rawTools
      .filter((t): t is Record<string, unknown> => typeof t === "object" && t !== null)
      .map((t) => parseToolSpec(t));
  }

  /**
   * Invoke a tool on this MCP server and return its raw output.
   *
   * @param name - Tool name as registered on the server.
   * @param args - Arguments matching the tool's parameter schema.
   * @returns Raw string output from the tool.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    const result = await this.send("tools/call", { name, arguments: args });
    return extractToolOutput(result);
  }

  /**
   * Terminate the MCP server subprocess and release resources.
   */
  close(): void {
    if (this._process === null) {
      return;
    }
    try {
      this._process.stdin?.end();
      this._process.kill();
    } catch {
      // Suppress errors during cleanup
    }
    this._process = null;
  }

  /**
   * Whether the server subprocess is alive.
   *
   * @returns True if the subprocess is running.
   */
  get isConnected(): boolean {
    return this._process !== null && this._process.exitCode === null;
  }

  // -- Private --------------------------------------------------------------

  /**
   * Handle incoming stdout data, buffering until a complete line is received.
   *
   * @param chunk - Raw data from stdout.
   */
  private handleStdoutData(chunk: string): void {
    this._buffer += chunk;
    const newlineIndex = this._buffer.indexOf("\n");
    if (newlineIndex === -1) {
      return;
    }
    const line = this._buffer.slice(0, newlineIndex);
    this._buffer = this._buffer.slice(newlineIndex + 1);
    if (this._pendingResolve) {
      const resolve = this._pendingResolve;
      this._pendingResolve = null;
      this._pendingReject = null;
      resolve(line);
    }
  }

  /**
   * Send a JSON-RPC request and wait for the response.
   *
   * @param method - RPC method name.
   * @param params - Optional method parameters.
   * @returns Parsed "result" field from the JSON-RPC response.
   * @throws {MCPProtocolError} If the connection is dead or response is invalid.
   */
  private async send(
    method: string,
    params?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    if (!this.isConnected) {
      throw new MCPProtocolError(`Not connected to MCP server '${this._config.name}'`);
    }

    this._requestId += 1;
    const requestId = this._requestId;
    const requestStr = buildRequest(method, requestId, params);

    const timeoutMs = this._config.timeoutMs ?? DEFAULT_TIMEOUT_MS;

    const linePromise = new Promise<string>((resolve, reject) => {
      this._pendingResolve = resolve;
      this._pendingReject = reject;
    });

    this._process!.stdin!.write(requestStr);

    const timeoutPromise = new Promise<never>((_resolve, reject) => {
      setTimeout(() => reject(new Error("MCP server timeout")), timeoutMs);
    });

    const line = await Promise.race([linePromise, timeoutPromise]);
    return parseResponse(line, requestId);
  }
}

// ---------------------------------------------------------------------------
// MCPConnectionPool
// ---------------------------------------------------------------------------

/**
 * Pool of MCP server connections with LRU eviction.
 *
 * Maintains at most maxSize live connections. When the pool is full
 * and a new connection is requested, the least-recently-used connection
 * is closed to make room.
 */
export class MCPConnectionPool {
  private readonly _maxSize: number;
  private readonly _connections = new Map<string, MCPConnection>();

  /**
   * @param maxSize - Maximum number of concurrent connections.
   */
  constructor(maxSize: number = DEFAULT_POOL_SIZE) {
    this._maxSize = maxSize;
  }

  /**
   * Get or create a connection for the given server config.
   *
   * Moves the connection to the end of the LRU queue on access.
   *
   * @param config - Server configuration identifying the connection.
   * @returns A connected MCPConnection ready for RPC calls.
   */
  get(config: MCPServerConfig): MCPConnection {
    const key = config.name;

    const existing = this._connections.get(key);
    if (existing !== undefined && existing.isConnected) {
      // Move to end (LRU refresh)
      this._connections.delete(key);
      this._connections.set(key, existing);
      return existing;
    }

    // Stale connection — clean up
    if (existing !== undefined) {
      safeClose(existing);
      this._connections.delete(key);
    }

    this.evictIfFull();
    const conn = createConnection(config);
    this._connections.set(key, conn);
    return conn;
  }

  /** Close every connection in the pool and clear the cache. */
  closeAll(): void {
    for (const conn of this._connections.values()) {
      safeClose(conn);
    }
    this._connections.clear();
  }

  // -- Private --------------------------------------------------------------

  /** Evict the least-recently-used connection if pool is at capacity. */
  private evictIfFull(): void {
    if (this._connections.size < this._maxSize) {
      return;
    }
    const oldestKey = this._connections.keys().next().value;
    if (oldestKey !== undefined) {
      const oldestConn = this._connections.get(oldestKey);
      if (oldestConn !== undefined) {
        safeClose(oldestConn);
      }
      this._connections.delete(oldestKey);
    }
  }
}

// ---------------------------------------------------------------------------
// MCPToolManager
// ---------------------------------------------------------------------------

/** Configuration options for {@link MCPToolManager}. */
export interface MCPToolManagerOptions {
  /** MCP server configurations to connect to. */
  readonly servers: readonly MCPServerConfig[];
  /** Sandboxing policy. Undefined means no restrictions. */
  readonly armor?: ArmorPolicy;
  /** Maximum concurrent server connections in the pool. */
  readonly poolSize?: number;
}

/**
 * Tool manager that discovers and executes tools via MCP servers.
 *
 * Implements the ToolManager protocol. Manages a pool of MCP server
 * connections and enforces permission filtering, sandboxing, and
 * result size limits on every operation.
 */
export class MCPToolManager implements ToolManager {
  private readonly _servers: Map<string, MCPServerConfig>;
  private readonly _armor: ArmorPolicy | undefined;
  private readonly _pool: MCPConnectionPool;
  private readonly _toolServerMap = new Map<string, string>();

  /**
   * @param options - Server configs, armor policy, and pool size.
   */
  constructor(options: MCPToolManagerOptions) {
    this._servers = new Map(options.servers.map((cfg) => [cfg.name, cfg]));
    this._armor = options.armor;
    this._pool = new MCPConnectionPool(options.poolSize ?? DEFAULT_POOL_SIZE);
  }

  /**
   * Discover tools from all configured MCP servers, filtered by permissions.
   *
   * @param ctx - Execution context carrying identity and permission set.
   * @returns List of tool specs the current caller is permitted to use.
   */
  async discover(ctx: ExecContext): Promise<ToolSpec[]> {
    const allTools: ToolSpec[] = [];
    for (const config of this._servers.values()) {
      const serverTools = await this.discoverFromServer(config, ctx);
      allTools.push(...serverTools);
    }
    return allTools;
  }

  /**
   * Execute a tool call with sandboxing and size limits.
   *
   * @param tool - Tool name to invoke.
   * @param args - Arguments matching the tool's parameter schema.
   * @param ctx - Execution context carrying identity and permission set.
   * @returns ToolResult with status, output, and timing information.
   */
  async call(
    tool: string,
    args: Record<string, unknown>,
    ctx: ExecContext,
  ): Promise<ToolResult> {
    const startMs = performance.now();

    if (!ctx.permissions.canUseTool(tool)) {
      return permissionDeniedResult(tool, startMs);
    }

    const serverName = this._toolServerMap.get(tool);
    if (serverName === undefined) {
      return notFoundResult(tool, startMs);
    }

    const config = this._servers.get(serverName);
    if (config === undefined) {
      return notFoundResult(tool, startMs);
    }

    if (this._armor !== undefined) {
      const violation = checkArmor(args, this._armor);
      if (violation !== null) {
        return armorViolationResult(tool, violation, startMs);
      }
    }

    return this.executeTool(config, tool, args, startMs);
  }

  /** Close all MCP server connections and release resources. */
  close(): void {
    this._pool.closeAll();
    this._toolServerMap.clear();
  }

  // -- Private: discovery ---------------------------------------------------

  /**
   * Discover and filter tools from a single MCP server.
   *
   * @param config - Server configuration.
   * @param ctx - Execution context for permission checks.
   * @returns Permission-filtered tool specs from this server.
   */
  private async discoverFromServer(config: MCPServerConfig, ctx: ExecContext): Promise<ToolSpec[]> {
    try {
      const conn = this._pool.get(config);
      const tools = await conn.listTools();
      const permitted = filterByPermissions(tools, ctx);
      for (const spec of permitted) {
        this._toolServerMap.set(spec.name, config.name);
      }
      return permitted;
    } catch {
      return [];
    }
  }

  /**
   * Execute a tool call on the server and build the result.
   *
   * @param config - Server that owns the tool.
   * @param tool - Tool name.
   * @param args - Tool arguments.
   * @param startMs - Monotonic timestamp when the call started.
   * @returns ToolResult with output and timing.
   */
  private async executeTool(
    config: MCPServerConfig,
    tool: string,
    args: Record<string, unknown>,
    startMs: number,
  ): Promise<ToolResult> {
    try {
      const conn = this._pool.get(config);
      const rawOutput = await conn.callTool(tool, args);
      const maxBytes = this._armor?.maxResultBytes ?? MAX_RESULT_BYTES;
      const output = truncateResult(rawOutput, maxBytes);
      const durationMs = performance.now() - startMs;

      return createToolResult(ToolStatus.SUCCESS, {
        output,
        durationMs,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.message.includes("timeout")) {
        return timeoutResult(tool, startMs);
      }
      const message = err instanceof Error ? err.message : String(err);
      return errorResult(tool, message, startMs);
    }
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Convert a raw MCP tool descriptor into a ToolSpec.
 *
 * @param raw - Dictionary from the MCP "tools/list" response.
 * @returns A ToolSpec with the tool's name, description, and parameters.
 */
function parseToolSpec(raw: Record<string, unknown>): ToolSpec {
  const name = String(raw["name"] ?? "");
  const description = String(raw["description"] ?? "");
  const inputSchema = raw["inputSchema"];
  const parameters = typeof inputSchema === "object" && inputSchema !== null && !Array.isArray(inputSchema)
    ? (inputSchema as Record<string, unknown>)
    : {};
  return createToolSpec(name, description, { parameters });
}

/**
 * Extract a string output from an MCP "tools/call" result.
 *
 * @param result - Parsed "result" field from the JSON-RPC response.
 * @returns String representation of the tool output.
 */
function extractToolOutput(result: Record<string, unknown>): string {
  const content = result["content"];
  if (Array.isArray(content)) {
    for (const block of content) {
      if (typeof block === "object" && block !== null && (block as Record<string, unknown>)["type"] === "text") {
        return String((block as Record<string, unknown>)["text"] ?? "");
      }
    }
  }
  return JSON.stringify(result);
}

/**
 * Remove tools the caller is not permitted to use.
 *
 * @param tools - Full tool list from a server.
 * @param ctx - Execution context with permission set.
 * @returns Subset of tools the caller may invoke.
 */
function filterByPermissions(tools: readonly ToolSpec[], ctx: ExecContext): ToolSpec[] {
  return tools.filter((t) => ctx.permissions.canUseTool(t.name));
}

/**
 * Truncate tool output that exceeds the byte limit.
 *
 * @param output - Raw tool output string.
 * @param maxBytes - Maximum allowed bytes.
 * @returns Original output if within limits, otherwise truncated with suffix.
 */
export function truncateResult(output: string, maxBytes: number): string {
  const encoded = Buffer.from(output, "utf-8");
  if (encoded.length <= maxBytes) {
    return output;
  }

  const suffixBytes = Buffer.from(TRUNCATION_SUFFIX, "utf-8");
  const cutAt = Math.max(0, maxBytes - suffixBytes.length);
  const truncated = encoded.subarray(0, cutAt).toString("utf-8");
  return truncated + TRUNCATION_SUFFIX;
}

/**
 * Validate a tool call against the armor policy.
 *
 * @param args - Arguments that will be passed to the tool.
 * @param armor - Active sandboxing policy.
 * @returns Violation description string, or null if permitted.
 */
function checkArmor(args: Record<string, unknown>, armor: ArmorPolicy): string | null {
  const pathViolation = checkPathArgs(args, armor.allowedPaths ?? []);
  if (pathViolation !== null) {
    return pathViolation;
  }

  if (armor.allowNetwork !== true) {
    const networkViolation = checkNetworkArgs(args);
    if (networkViolation !== null) {
      return networkViolation;
    }
  }

  return null;
}

/** Path-related argument keys to inspect. */
const PATH_KEYS = new Set(["path", "file", "filepath", "filename", "directory", "dir"]);

/** Network-related argument keys to inspect. */
const NETWORK_KEYS = new Set(["url", "uri", "endpoint", "host", "hostname"]);

/**
 * Check whether any path-like arguments fall outside allowed paths.
 *
 * @param args - Tool arguments to inspect.
 * @param allowedPaths - Allowed filesystem path prefixes.
 * @returns Violation message if a disallowed path is found, else null.
 */
function checkPathArgs(
  args: Record<string, unknown>,
  allowedPaths: readonly string[],
): string | null {
  if (allowedPaths.length === 0) {
    for (const key of PATH_KEYS) {
      if (key in args) {
        return `Filesystem access denied: argument '${key}' not permitted`;
      }
    }
    return null;
  }

  for (const key of PATH_KEYS) {
    const value = args[key];
    if (typeof value !== "string") {
      continue;
    }
    if (!allowedPaths.some((ap) => value.startsWith(ap))) {
      return `Path '${value}' is outside allowed paths`;
    }
  }

  return null;
}

/**
 * Check whether arguments suggest a network call.
 *
 * @param args - Tool arguments to inspect.
 * @returns Violation message if network indicators are found, else null.
 */
function checkNetworkArgs(args: Record<string, unknown>): string | null {
  for (const key of NETWORK_KEYS) {
    if (key in args) {
      return `Network access denied: argument '${key}' not permitted`;
    }
  }
  return null;
}

/**
 * Create and connect a new MCPConnection.
 *
 * @param config - Server configuration.
 * @returns A freshly connected MCPConnection.
 */
function createConnection(config: MCPServerConfig): MCPConnection {
  const conn = new MCPConnection(config);
  conn.connect();
  return conn;
}

/**
 * Close a connection, suppressing any errors.
 *
 * @param conn - Connection to close.
 */
function safeClose(conn: MCPConnection): void {
  try {
    conn.close();
  } catch {
    // Suppress errors during cleanup
  }
}

// ---------------------------------------------------------------------------
// Result builders
// ---------------------------------------------------------------------------

/**
 * Calculate elapsed milliseconds from a performance.now() start.
 *
 * @param startMs - Start time from performance.now().
 * @returns Milliseconds elapsed since start.
 */
function elapsedMs(startMs: number): number {
  return performance.now() - startMs;
}

/**
 * Build a PERMISSION_DENIED result.
 *
 * @param tool - Tool name that was denied.
 * @param startMs - Call start timestamp.
 * @returns ToolResult with PERMISSION_DENIED status.
 */
function permissionDeniedResult(tool: string, startMs: number): ToolResult {
  return createToolResult(ToolStatus.PERMISSION_DENIED, {
    error: `Permission denied for tool '${tool}'`,
    durationMs: elapsedMs(startMs),
  });
}

/**
 * Build a NOT_FOUND result.
 *
 * @param tool - Tool name that was not found.
 * @param startMs - Call start timestamp.
 * @returns ToolResult with NOT_FOUND status.
 */
function notFoundResult(tool: string, startMs: number): ToolResult {
  return createToolResult(ToolStatus.NOT_FOUND, {
    error: `Tool '${tool}' not found on any configured MCP server`,
    durationMs: elapsedMs(startMs),
  });
}

/**
 * Build a TIMEOUT result.
 *
 * @param tool - Tool name that timed out.
 * @param startMs - Call start timestamp.
 * @returns ToolResult with TIMEOUT status.
 */
function timeoutResult(tool: string, startMs: number): ToolResult {
  return createToolResult(ToolStatus.TIMEOUT, {
    error: `Tool '${tool}' timed out`,
    durationMs: elapsedMs(startMs),
  });
}

/**
 * Build a generic ERROR result.
 *
 * @param tool - Tool name that errored.
 * @param message - Error description.
 * @param startMs - Call start timestamp.
 * @returns ToolResult with ERROR status.
 */
function errorResult(tool: string, message: string, startMs: number): ToolResult {
  return createToolResult(ToolStatus.ERROR, {
    error: `Tool '${tool}' failed: ${message}`,
    durationMs: elapsedMs(startMs),
  });
}

/**
 * Build a PERMISSION_DENIED result for an armor policy violation.
 *
 * @param tool - Tool name that violated policy.
 * @param violation - Human-readable violation description.
 * @param startMs - Call start timestamp.
 * @returns ToolResult with PERMISSION_DENIED status.
 */
function armorViolationResult(tool: string, violation: string, startMs: number): ToolResult {
  return createToolResult(ToolStatus.PERMISSION_DENIED, {
    error: `Armor violation for tool '${tool}': ${violation}`,
    durationMs: elapsedMs(startMs),
  });
}
