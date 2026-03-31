/**
 * Container runtime — execute handlers in Docker containers.
 *
 * Spawns a Docker container for each handler invocation, passing input
 * as JSON on stdin and collecting output from stdout. Supports configurable
 * images, resource limits, network isolation, timeout via container kill,
 * and per-handler circuit breakers.
 *
 * @module runtime/container
 */

import { spawn } from "node:child_process";
import type { ExecContext } from "../context.js";
import {
  AgentStatus,
  type AgentInput,
  type AgentResult,
  type AgentRuntime,
  createAgentInput,
  createAgentResult,
} from "./index.js";
import { CircuitBreaker, type CircuitBreakerConfig } from "./circuit-breaker.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default timeout for container execution in milliseconds. */
const DEFAULT_TIMEOUT_MS = 60_000;

/** Maximum stdout bytes collected before truncation. */
const MAX_OUTPUT_BYTES = 1_048_576; // 1 MB

/** Default Docker network mode. */
const DEFAULT_NETWORK_MODE = "none";

/** Regex to locate a JSON object in noisy container output. */
const JSON_EXTRACT_PATTERN = /\{[^{}]*\}|\{.*\}/s;

// ---------------------------------------------------------------------------
// ContainerConfig
// ---------------------------------------------------------------------------

/**
 * Configuration for a single container handler.
 */
export interface ContainerHandlerConfig {
  /** Docker image to run (e.g. `"myregistry/handler:latest"`). */
  readonly image: string;
  /** Memory limit (e.g. `"256m"`). Undefined means no limit. */
  readonly memoryLimit?: string;
  /** CPU limit (e.g. `"0.5"`). Undefined means no limit. */
  readonly cpuLimit?: string;
  /** Network mode (e.g. `"none"`, `"bridge"`). Defaults to `"none"`. */
  readonly networkMode?: string;
  /** Extra environment variables passed to the container. */
  readonly env?: Readonly<Record<string, string>>;
}

/**
 * Configuration for the container runtime.
 */
export interface ContainerRuntimeConfig {
  /** Max execution time per handler invocation in milliseconds. */
  readonly timeoutMs: number;
  /** Circuit breaker thresholds applied per handler. */
  readonly circuitBreaker?: Partial<CircuitBreakerConfig> | undefined;
  /** Maximum stdout bytes to collect before truncation. */
  readonly maxOutputBytes: number;
}

// ---------------------------------------------------------------------------
// ContainerRuntime
// ---------------------------------------------------------------------------

/**
 * Execute handlers as Docker containers with lifecycle management.
 *
 * Each handler maps to a container image. On invocation, the runtime
 * spawns `docker run`, writes `AgentInput` JSON to stdin, and collects
 * `AgentResult`-shaped JSON from stdout. Timeout is enforced by killing
 * the container.
 *
 * @example
 * ```ts
 * const runtime = new ContainerRuntime();
 * runtime.register("search", { image: "search-handler:latest" });
 * const result = await runtime.invoke("search", agentInput, ctx);
 * ```
 */
export class ContainerRuntime implements AgentRuntime {
  private readonly _config: ContainerRuntimeConfig;
  private readonly _handlers = new Map<string, ContainerHandlerConfig>();
  private readonly _breakers = new Map<string, CircuitBreaker>();

  /**
   * @param config - Optional runtime configuration overrides.
   */
  constructor(config?: Partial<ContainerRuntimeConfig>) {
    this._config = {
      timeoutMs: config?.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      circuitBreaker: config?.circuitBreaker,
      maxOutputBytes: config?.maxOutputBytes ?? MAX_OUTPUT_BYTES,
    };
  }

  /**
   * Register a handler with its container configuration.
   *
   * @param name - Unique handler identifier.
   * @param handlerConfig - Docker image and resource limits.
   * @throws {Error} If a handler with the same name is already registered.
   */
  register(name: string, handlerConfig: ContainerHandlerConfig): void {
    if (this._handlers.has(name)) {
      throw new Error(`Handler '${name}' is already registered`);
    }
    this._handlers.set(name, handlerConfig);
  }

  /**
   * Run a handler in a Docker container.
   *
   * @param handler - Handler name (must be registered).
   * @param input - Structured input serialised to JSON on stdin.
   * @param ctx - Execution context for tracing and streaming.
   * @returns AgentResult populated from the container's stdout JSON.
   */
  async invoke(handler: string, input: AgentInput, ctx: ExecContext): Promise<AgentResult> {
    const handlerConfig = this._handlers.get(handler);
    if (handlerConfig === undefined) {
      return createAgentResult(AgentStatus.ERROR, {
        handler,
        error: `handler '${handler}' not found`,
      });
    }

    const breaker = this._getBreaker(handler);
    if (!breaker.isAllowed()) {
      return buildCircuitOpenResult(handler);
    }

    ctx.addEvent("container.start", { handler, image: handlerConfig.image });
    const inputJson = JSON.stringify(input);
    const { stdout, timedOut, exitCode } = await this._runContainer(
      handlerConfig,
      inputJson,
      ctx,
    );

    const result = buildResult(handler, stdout, exitCode, timedOut, this._config.timeoutMs);
    recordOnBreaker(breaker, result.status);
    ctx.addEvent("container.end", { handler, status: result.status });
    return result;
  }

  /**
   * Run handlers in sequence, piping each output as the next input's message.
   *
   * @param handlers - Ordered list of handler names.
   * @param input - Initial input for the first handler.
   * @param ctx - Execution context shared across the chain.
   * @returns AgentResult from the last successfully executed handler.
   * @throws {Error} If `handlers` is empty.
   */
  async invokeChain(
    handlers: string[],
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    if (handlers.length === 0) {
      throw new Error("handlers list must not be empty");
    }

    let currentInput = input;
    let result = createAgentResult(AgentStatus.ERROR, { error: "no handlers ran" });

    for (const handlerName of handlers) {
      result = await this.invoke(handlerName, currentInput, ctx);
      if (result.status !== AgentStatus.SUCCESS) {
        return result;
      }
      currentInput = createAgentInput(result.output, {
        args: { ...currentInput.args },
        tools: currentInput.tools.map((t) => ({ ...t })),
        history: currentInput.history.map((h) => ({ ...h })),
      });
    }

    return result;
  }

  /**
   * Invoke a handler from within another handler (agent-to-agent delegation).
   *
   * @param handler - Handler name to delegate to.
   * @param input - Input for the delegated handler.
   * @param parentCtx - Parent's execution context.
   * @returns AgentResult from the delegated handler.
   */
  async delegate(
    handler: string,
    input: AgentInput,
    parentCtx: ExecContext,
  ): Promise<AgentResult> {
    const childCtx = parentCtx.child(handler);
    return this.invoke(handler, input, childCtx);
  }

  // -- Private: container execution -----------------------------------------

  /**
   * Spawn a Docker container, pipe input, and collect output.
   *
   * @param handlerConfig - Container image and resource configuration.
   * @param inputJson - Serialised AgentInput to write to stdin.
   * @param ctx - Execution context for streaming.
   * @returns stdout content, timeout flag, and exit code.
   */
  private _runContainer(
    handlerConfig: ContainerHandlerConfig,
    inputJson: string,
    ctx: ExecContext,
  ): Promise<{ stdout: string; timedOut: boolean; exitCode: number }> {
    return new Promise((resolvePromise) => {
      const args = buildDockerArgs(handlerConfig);
      const child = spawn("docker", args, { stdio: ["pipe", "pipe", "pipe"] });

      const chunks: Buffer[] = [];
      let totalBytes = 0;
      let timedOut = false;

      const timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGKILL");
      }, this._config.timeoutMs);

      if (child.stdout !== null) {
        child.stdout.on("data", (chunk: Buffer) => {
          const remaining = this._config.maxOutputBytes - totalBytes;
          if (remaining <= 0) return;

          const trimmed = remaining < chunk.length ? chunk.subarray(0, remaining) : chunk;
          chunks.push(trimmed);
          totalBytes += trimmed.length;

          if (ctx.stream !== null) {
            ctx.stream.push(trimmed.toString("utf-8")).catch(() => {
              // Best-effort streaming
            });
          }
        });
      }

      child.on("error", () => {
        clearTimeout(timer);
        resolvePromise({ stdout: "", timedOut: false, exitCode: -1 });
      });

      child.on("close", (code) => {
        clearTimeout(timer);
        if (timedOut) {
          resolvePromise({ stdout: "", timedOut: true, exitCode: -1 });
          return;
        }
        const stdout = Buffer.concat(chunks).toString("utf-8");
        resolvePromise({ stdout, timedOut: false, exitCode: code ?? -1 });
      });

      if (child.stdin !== null) {
        child.stdin.write(inputJson, "utf-8");
        child.stdin.end();
      }
    });
  }

  /**
   * Return the circuit breaker for the handler, creating one if needed.
   *
   * @param handler - Handler name used as the breaker key.
   * @returns The CircuitBreaker instance for this handler.
   */
  private _getBreaker(handler: string): CircuitBreaker {
    let breaker = this._breakers.get(handler);
    if (breaker === undefined) {
      breaker = new CircuitBreaker(this._config.circuitBreaker);
      this._breakers.set(handler, breaker);
    }
    return breaker;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build the `docker run` argument list from handler configuration.
 *
 * @param config - Container handler configuration.
 * @returns Array of CLI arguments for `docker run`.
 */
function buildDockerArgs(config: ContainerHandlerConfig): string[] {
  const args = ["run", "--rm", "-i"];

  const networkMode = config.networkMode ?? DEFAULT_NETWORK_MODE;
  args.push("--network", networkMode);

  if (config.memoryLimit !== undefined) {
    args.push("--memory", config.memoryLimit);
  }

  if (config.cpuLimit !== undefined) {
    args.push("--cpus", config.cpuLimit);
  }

  if (config.env !== undefined) {
    for (const [key, value] of Object.entries(config.env)) {
      args.push("-e", `${key}=${value}`);
    }
  }

  args.push(config.image);
  return args;
}

/**
 * Build an AgentResult from container output and execution status.
 *
 * @param handler - Handler name.
 * @param stdout - Raw stdout from the container.
 * @param exitCode - Container exit code.
 * @param timedOut - Whether the container was killed due to timeout.
 * @param timeoutMs - Configured timeout value for error messages.
 * @returns Populated AgentResult.
 */
function buildResult(
  handler: string,
  stdout: string,
  exitCode: number,
  timedOut: boolean,
  timeoutMs: number,
): AgentResult {
  if (timedOut) {
    return createAgentResult(AgentStatus.TIMEOUT, {
      handler,
      error: `container '${handler}' timed out after ${timeoutMs}ms`,
    });
  }

  if (exitCode !== 0) {
    return createAgentResult(AgentStatus.ERROR, {
      handler,
      output: stdout,
      error: `container '${handler}' exited with code ${exitCode}`,
    });
  }

  const output = extractOutputText(stdout);
  return createAgentResult(AgentStatus.SUCCESS, {
    handler,
    output,
  });
}

/**
 * Extract the response text from container stdout.
 *
 * Tries to parse as JSON and pull an "output" or "response" field.
 * Falls back to raw stdout.
 *
 * @param raw - Raw stdout from the container.
 * @returns Extracted output text.
 */
function extractOutputText(raw: string): string {
  const stripped = raw.trim();
  if (stripped === "") return "";

  const parsed = tryParseJson(stripped) ?? regexExtractJson(stripped);
  if (parsed !== null) {
    const output = parsed["output"] ?? parsed["response"];
    if (typeof output === "string") return output;
  }

  return stripped;
}

/**
 * Try parsing the entire text as a JSON object.
 *
 * @param text - Candidate JSON string.
 * @returns Parsed record or null.
 */
function tryParseJson(text: string): Record<string, unknown> | null {
  try {
    const result: unknown = JSON.parse(text);
    if (typeof result === "object" && result !== null && !Array.isArray(result)) {
      return result as Record<string, unknown>;
    }
  } catch {
    // Not valid JSON
  }
  return null;
}

/**
 * Regex-extract the first JSON object from noisy output.
 *
 * @param text - Noisy output that may contain JSON.
 * @returns Parsed record or null.
 */
function regexExtractJson(text: string): Record<string, unknown> | null {
  const match = JSON_EXTRACT_PATTERN.exec(text);
  if (match !== null) {
    return tryParseJson(match[0]);
  }
  return null;
}

/**
 * Build an error result for a handler whose circuit is open.
 *
 * @param handler - The rejected handler name.
 * @returns AgentResult with ERROR status.
 */
function buildCircuitOpenResult(handler: string): AgentResult {
  return createAgentResult(AgentStatus.ERROR, {
    handler,
    error: `circuit open for handler '${handler}'`,
  });
}

/**
 * Record success or failure on the circuit breaker.
 *
 * @param breaker - The handler's circuit breaker.
 * @param status - The outcome status of the invocation.
 */
function recordOnBreaker(breaker: CircuitBreaker, status: AgentStatus): void {
  if (status === AgentStatus.SUCCESS) {
    breaker.recordSuccess();
  } else {
    breaker.recordFailure();
  }
}
