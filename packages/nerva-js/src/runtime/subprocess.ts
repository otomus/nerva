/**
 * Subprocess runtime — execute agent handlers as child processes.
 *
 * Each handler is a script or binary that:
 * 1. Receives `AgentInput` as JSON on stdin
 * 2. Writes `AgentResult`-shaped JSON to stdout
 * 3. Exits with 0 on success, non-zero on failure
 *
 * Tasks: N-221 (spawn + collect), N-222 (circuit breaker), N-223 (JSON extraction),
 * N-224 (error classification), N-225 (streaming).
 *
 * @module runtime/subprocess
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { ExecContext } from "../context.js";
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
// Named constants
// ---------------------------------------------------------------------------

/** Maximum wall-clock milliseconds a handler process may run. */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Maximum stdout bytes collected from a handler before truncation. */
const MAX_OUTPUT_BYTES = 1_048_576; // 1 MB

/** Exit code that signals the handler cannot handle this input. */
const WRONG_HANDLER_EXIT_CODE = 2;

/** Regex to locate a JSON object in noisy handler output. */
const JSON_EXTRACT_PATTERN = /\{[^{}]*\}|\{.*\}/s;

// ---------------------------------------------------------------------------
// ErrorKind (N-224)
// ---------------------------------------------------------------------------

/**
 * Classification of handler errors.
 */
export const ErrorKind = {
  /** Transient failure -- safe to retry (e.g. timeout). */
  RETRYABLE: "retryable",
  /** Permanent failure -- retrying will not help. */
  FATAL: "fatal",
  /** Handler cannot handle the given input. */
  WRONG_HANDLER: "wrong_handler",
} as const;

export type ErrorKind = (typeof ErrorKind)[keyof typeof ErrorKind];

// ---------------------------------------------------------------------------
// SubprocessConfig
// ---------------------------------------------------------------------------

/**
 * Configuration for the subprocess runtime.
 */
export interface SubprocessConfig {
  /** Max execution time per handler invocation in milliseconds. */
  readonly timeoutMs: number;
  /** Circuit breaker thresholds applied per handler, or undefined for defaults. */
  readonly circuitBreaker?: Partial<CircuitBreakerConfig> | undefined;
  /** Max stdout bytes to collect before truncation. */
  readonly maxOutputBytes: number;
  /** Base directory for resolving handler scripts. */
  readonly handlerDir: string;
}

/**
 * Create a SubprocessConfig with defaults for any omitted fields.
 *
 * @param overrides - Partial config to merge with defaults.
 * @returns A complete SubprocessConfig.
 */
export function createSubprocessConfig(
  overrides?: Partial<SubprocessConfig>,
): SubprocessConfig {
  return {
    timeoutMs: overrides?.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    circuitBreaker: overrides?.circuitBreaker,
    maxOutputBytes: overrides?.maxOutputBytes ?? MAX_OUTPUT_BYTES,
    handlerDir: overrides?.handlerDir ?? ".",
  };
}

// ---------------------------------------------------------------------------
// SubprocessRuntime (N-221)
// ---------------------------------------------------------------------------

/**
 * Execute handlers as child processes with lifecycle management.
 *
 * Provides per-handler circuit breakers (N-222), timeout enforcement,
 * structured JSON extraction from output (N-223), error classification
 * (N-224), and incremental streaming to `ctx.stream` (N-225).
 *
 * @example
 * ```ts
 * const runtime = new SubprocessRuntime({ handlerDir: "/opt/handlers" });
 * const result = await runtime.invoke("search", agentInput, ctx);
 * ```
 */
export class SubprocessRuntime implements AgentRuntime {
  private readonly _config: SubprocessConfig;
  private readonly _breakers = new Map<string, CircuitBreaker>();

  /**
   * Create a new subprocess runtime.
   *
   * @param config - Runtime configuration. Uses defaults when omitted.
   */
  constructor(config?: Partial<SubprocessConfig>) {
    this._config = createSubprocessConfig(config);
  }

  // -- Public API -----------------------------------------------------------

  /**
   * Run a single handler as a subprocess.
   *
   * @param handler - Handler name, resolved relative to `handlerDir`.
   * @param input - Structured input serialised to JSON on stdin.
   * @param ctx - Execution context for tracing, streaming, and cancellation.
   * @returns AgentResult populated from the handler's stdout JSON,
   *          or an error result if the handler fails or times out.
   */
  async invoke(handler: string, input: AgentInput, ctx: ExecContext): Promise<AgentResult> {
    const breaker = this._getBreaker(handler);
    if (!breaker.isAllowed()) {
      return buildCircuitOpenResult(handler);
    }

    ctx.addEvent("subprocess.start", { handler });
    const startedAt = performance.now();

    const inputJson = JSON.stringify(input);
    const { stdout, exitCode } = await this._spawnProcess(handler, inputJson, ctx);

    const elapsedMs = performance.now() - startedAt;
    ctx.addEvent("subprocess.end", {
      handler,
      exitCode: String(exitCode),
      elapsedMs: elapsedMs.toFixed(3),
    });

    const errorKind = classifyError(exitCode, stdout);
    const result = buildResult(handler, stdout, exitCode, errorKind, this._config.timeoutMs);

    recordOnBreaker(breaker, result.status);
    return result;
  }

  /**
   * Run handlers in sequence, piping each output as the next input's message.
   *
   * Stops early if any handler returns a non-SUCCESS status.
   *
   * @param handlers - Ordered list of handler names.
   * @param input - Initial input for the first handler.
   * @param ctx - Execution context shared across the chain.
   * @returns AgentResult from the last successfully executed handler,
   *          or the first non-SUCCESS result.
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
    let result = createAgentResult(AgentStatus.ERROR, {
      error: "no handlers ran",
    });

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
   * Creates a child ExecContext inheriting the parent's trace, permissions,
   * and stream, then runs the handler in that child context.
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

  // -- Circuit breaker helpers -----------------------------------------------

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

  // -- Process spawning (N-221 + N-225) -------------------------------------

  /**
   * Spawn a subprocess, collect stdout, and enforce timeout.
   *
   * When `ctx.stream` is set, pushes stdout chunks incrementally (N-225).
   *
   * @param handler - Handler name resolved to a filesystem path.
   * @param inputJson - Serialised AgentInput written to stdin.
   * @param ctx - Execution context carrying stream sink and cancellation.
   * @returns A `{ stdout, exitCode }` tuple. `exitCode` is `-1` on timeout.
   */
  private _spawnProcess(
    handler: string,
    inputJson: string,
    ctx: ExecContext,
  ): Promise<{ stdout: string; exitCode: number }> {
    return new Promise((resolvePromise) => {
      const commandPath = resolveHandlerPath(this._config.handlerDir, handler);

      const child = spawn(commandPath, [], {
        stdio: ["pipe", "pipe", "pipe"],
      });

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

          // Stream chunks to the context sink if available (N-225)
          if (ctx.stream !== null) {
            ctx.stream.push(trimmed.toString("utf-8")).catch(() => {
              // Best-effort streaming; swallowing push errors is intentional
              // because a broken stream should not abort the handler.
            });
          }
        });
      }

      child.on("error", () => {
        clearTimeout(timer);
        resolvePromise({ stdout: "", exitCode: -1 });
      });

      child.on("close", (code) => {
        clearTimeout(timer);
        if (timedOut) {
          ctx.addEvent("subprocess.timeout", { handler });
          resolvePromise({ stdout: "", exitCode: -1 });
          return;
        }
        const stdout = Buffer.concat(chunks).toString("utf-8");
        resolvePromise({ stdout, exitCode: code ?? -1 });
      });

      // Write input to stdin then close it
      if (child.stdin !== null) {
        child.stdin.write(inputJson, "utf-8");
        child.stdin.end();
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Pure helpers (no instance state)
// ---------------------------------------------------------------------------

/**
 * Resolve a handler name to an executable filesystem path.
 *
 * Checks the configured `handlerDir` first. If no file exists there,
 * returns the bare handler name for PATH-based resolution.
 *
 * @param handlerDir - Base directory for handler scripts.
 * @param handler - Handler name (e.g. `"search"`).
 * @returns Absolute path if found in `handlerDir`, otherwise the bare name.
 */
function resolveHandlerPath(handlerDir: string, handler: string): string {
  const candidate = resolve(handlerDir, handler);
  if (existsSync(candidate)) {
    return candidate;
  }
  return handler;
}

/**
 * Build an error result for a handler whose circuit is open.
 *
 * @param handler - The handler that was rejected.
 * @returns AgentResult with ERROR status and descriptive error message.
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

/**
 * Classify a handler's exit status into an error kind.
 *
 * @param exitCode - Process exit code (`-1` for timeout).
 * @param _output - Collected stdout (reserved for future heuristics).
 * @returns `null` if the handler succeeded, otherwise an ErrorKind.
 */
function classifyError(exitCode: number, _output: string): ErrorKind | null {
  if (exitCode === 0) return null;
  if (exitCode === -1) return ErrorKind.RETRYABLE;
  if (exitCode === WRONG_HANDLER_EXIT_CODE) return ErrorKind.WRONG_HANDLER;
  return ErrorKind.FATAL;
}

/**
 * Construct an AgentResult from handler output and exit status.
 *
 * @param handler - Name of the handler that ran.
 * @param output - Raw stdout collected from the process.
 * @param exitCode - Process exit code.
 * @param errorKind - Classified error, or `null` on success.
 * @param timeoutMs - Configured timeout for error messages.
 * @returns Fully populated AgentResult.
 */
function buildResult(
  handler: string,
  output: string,
  exitCode: number,
  errorKind: ErrorKind | null,
  timeoutMs: number,
): AgentResult {
  if (errorKind === null) return buildSuccessResult(handler, output);
  if (errorKind === ErrorKind.RETRYABLE) return buildTimeoutResult(handler, timeoutMs);
  if (errorKind === ErrorKind.WRONG_HANDLER) return buildWrongHandlerResult(handler, output);
  return buildFatalResult(handler, output, exitCode);
}

/**
 * Build a SUCCESS result, extracting structured data from output.
 *
 * @param handler - Handler name.
 * @param output - Raw stdout from the handler.
 * @returns AgentResult with SUCCESS status and extracted data.
 */
function buildSuccessResult(handler: string, output: string): AgentResult {
  const data = extractJson(output);
  const responseText =
    popStringField(data, "output") || popStringField(data, "response") || output;
  return createAgentResult(AgentStatus.SUCCESS, {
    output: responseText,
    data,
    handler,
  });
}

/**
 * Build a TIMEOUT result.
 *
 * @param handler - Handler name.
 * @param timeoutMs - The timeout that was exceeded, in milliseconds.
 * @returns AgentResult with TIMEOUT status.
 */
function buildTimeoutResult(handler: string, timeoutMs: number): AgentResult {
  return createAgentResult(AgentStatus.TIMEOUT, {
    handler,
    error: `handler '${handler}' timed out after ${timeoutMs}ms`,
  });
}

/**
 * Build a WRONG_HANDLER result.
 *
 * @param handler - Handler name.
 * @param output - Raw stdout (may contain a reason).
 * @returns AgentResult with WRONG_HANDLER status.
 */
function buildWrongHandlerResult(handler: string, output: string): AgentResult {
  const data = extractJson(output);
  const reason =
    (typeof data["error"] === "string" ? data["error"] : undefined) ||
    output.trim() ||
    "handler declined the input";
  return createAgentResult(AgentStatus.WRONG_HANDLER, {
    handler,
    error: reason,
    data,
  });
}

/**
 * Build an ERROR result for a fatal (non-retryable) failure.
 *
 * @param handler - Handler name.
 * @param output - Raw stdout from the handler.
 * @param exitCode - Non-zero exit code.
 * @returns AgentResult with ERROR status.
 */
function buildFatalResult(handler: string, output: string, exitCode: number): AgentResult {
  const data = extractJson(output);
  const errorMsg =
    (typeof data["error"] === "string" ? data["error"] : undefined) ||
    `handler '${handler}' exited with code ${exitCode}`;
  return createAgentResult(AgentStatus.ERROR, {
    output,
    data,
    handler,
    error: errorMsg,
  });
}

// ---------------------------------------------------------------------------
// JSON extraction (N-223)
// ---------------------------------------------------------------------------

/**
 * Extract a JSON object from potentially noisy handler output.
 *
 * Tries parsing the entire output first. Falls back to regex extraction
 * of the first JSON-like object.
 *
 * @param rawOutput - Raw stdout from the handler process.
 * @returns Parsed record if JSON was found, empty record otherwise.
 */
function extractJson(rawOutput: string): Record<string, string> {
  const stripped = rawOutput.trim();
  if (stripped === "") return {};

  const fullParse = tryParseJson(stripped);
  if (fullParse !== null) return fullParse;

  return regexExtractJson(stripped);
}

/**
 * Attempt to parse the entire text as a JSON object.
 *
 * @param text - Candidate JSON string.
 * @returns Parsed record if successful, `null` otherwise.
 */
function tryParseJson(text: string): Record<string, string> | null {
  try {
    const result: unknown = JSON.parse(text);
    if (typeof result === "object" && result !== null && !Array.isArray(result)) {
      return result as Record<string, string>;
    }
  } catch {
    // Not valid JSON — fall through
  }
  return null;
}

/**
 * Use regex to find and parse the first JSON object in the text.
 *
 * @param text - Noisy output that may contain a JSON object.
 * @returns Parsed record from the first valid JSON match, or empty record.
 */
function regexExtractJson(text: string): Record<string, string> {
  const match = JSON_EXTRACT_PATTERN.exec(text);
  if (match !== null) {
    const parsed = tryParseJson(match[0]);
    if (parsed !== null) return parsed;
  }
  return {};
}

/**
 * Remove a string field from a mutable record and return its value.
 *
 * @param data - The record to mutate.
 * @param key - The key to pop.
 * @returns The string value if present, empty string otherwise.
 */
function popStringField(data: Record<string, string>, key: string): string {
  const value = data[key];
  if (typeof value === "string") {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete data[key];
    return value;
  }
  return "";
}
