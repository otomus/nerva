/**
 * In-process runtime — execute handlers as async functions within the same process.
 *
 * Registers async functions (or async generators for streaming) as handlers,
 * invokes them directly, and integrates circuit breakers and timeout enforcement.
 *
 * @module runtime/inprocess
 */

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
// Types
// ---------------------------------------------------------------------------

/**
 * A handler function that processes input and returns a string result.
 */
export type HandlerFn = (input: AgentInput, ctx: ExecContext) => Promise<string>;

/**
 * A streaming handler that yields string chunks via an async generator.
 */
export type StreamingHandlerFn = (
  input: AgentInput,
  ctx: ExecContext,
) => AsyncGenerator<string, void, undefined>;

/** Union type for both handler styles. */
type AnyHandlerFn = HandlerFn | StreamingHandlerFn;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default timeout for handler execution in milliseconds. */
const DEFAULT_TIMEOUT_MS = 30_000;

// ---------------------------------------------------------------------------
// RegisteredHandler
// ---------------------------------------------------------------------------

/** Internal record for a registered handler function. */
interface RegisteredHandler {
  readonly name: string;
  readonly fn: AnyHandlerFn;
  readonly streaming: boolean;
}

// ---------------------------------------------------------------------------
// InProcessConfig
// ---------------------------------------------------------------------------

/**
 * Configuration options for {@link InProcessRuntime}.
 */
export interface InProcessConfig {
  /** Max execution time per handler invocation in milliseconds. */
  readonly timeoutMs: number;
  /** Circuit breaker thresholds applied per handler. */
  readonly circuitBreaker?: Partial<CircuitBreakerConfig> | undefined;
}

// ---------------------------------------------------------------------------
// InProcessRuntime
// ---------------------------------------------------------------------------

/**
 * Runtime that executes handler functions directly in the same Node.js process.
 *
 * Provides per-handler circuit breakers, timeout enforcement via Promise.race,
 * and streaming support for async generator handlers that push chunks to
 * `ctx.stream`.
 *
 * @example
 * ```ts
 * const runtime = new InProcessRuntime();
 * runtime.register("greet", async (input) => `Hello, ${input.message}!`);
 * const result = await runtime.invoke("greet", agentInput, ctx);
 * ```
 */
export class InProcessRuntime implements AgentRuntime {
  private readonly _config: InProcessConfig;
  private readonly _handlers = new Map<string, RegisteredHandler>();
  private readonly _breakers = new Map<string, CircuitBreaker>();

  /**
   * @param config - Optional runtime configuration overrides.
   */
  constructor(config?: Partial<InProcessConfig>) {
    this._config = {
      timeoutMs: config?.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      circuitBreaker: config?.circuitBreaker,
    };
  }

  /**
   * Register a regular async handler function.
   *
   * @param name - Unique handler identifier.
   * @param fn - Async function that processes input and returns a string.
   * @throws {Error} If a handler with the same name is already registered.
   */
  register(name: string, fn: HandlerFn): void {
    if (this._handlers.has(name)) {
      throw new Error(`Handler '${name}' is already registered`);
    }
    this._handlers.set(name, { name, fn, streaming: false });
  }

  /**
   * Register an async generator handler for streaming output.
   *
   * When invoked, each yielded chunk is pushed to `ctx.stream` if available.
   *
   * @param name - Unique handler identifier.
   * @param fn - Async generator that yields string chunks.
   * @throws {Error} If a handler with the same name is already registered.
   */
  registerStreaming(name: string, fn: StreamingHandlerFn): void {
    if (this._handlers.has(name)) {
      throw new Error(`Handler '${name}' is already registered`);
    }
    this._handlers.set(name, { name, fn, streaming: true });
  }

  /**
   * Invoke a registered handler by name.
   *
   * @param handler - Handler name to invoke.
   * @param input - Structured input for the handler.
   * @param ctx - Execution context for tracing, streaming, and cancellation.
   * @returns AgentResult with status and output.
   */
  async invoke(handler: string, input: AgentInput, ctx: ExecContext): Promise<AgentResult> {
    const registered = this._handlers.get(handler);
    if (registered === undefined) {
      return createAgentResult(AgentStatus.ERROR, {
        handler,
        error: `handler '${handler}' not found`,
      });
    }

    const breaker = this._getBreaker(handler);
    if (!breaker.isAllowed()) {
      return buildCircuitOpenResult(handler);
    }

    ctx.addEvent("inprocess.start", { handler });

    const result = registered.streaming
      ? await this._invokeStreaming(registered, input, ctx)
      : await this._invokeRegular(registered, input, ctx);

    recordOnBreaker(breaker, result.status);
    ctx.addEvent("inprocess.end", { handler, status: result.status });
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
   * Creates a child ExecContext with inherited permissions and trace lineage.
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

  // -- Private: handler execution -------------------------------------------

  /**
   * Invoke a regular (non-streaming) handler with timeout.
   *
   * @param registered - The registered handler record.
   * @param input - Agent input.
   * @param ctx - Execution context.
   * @returns AgentResult from the handler.
   */
  private async _invokeRegular(
    registered: RegisteredHandler,
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    const fn = registered.fn as HandlerFn;
    try {
      const output = await Promise.race([
        fn(input, ctx),
        rejectAfterTimeout(this._config.timeoutMs, registered.name),
      ]);
      return createAgentResult(AgentStatus.SUCCESS, {
        output,
        handler: registered.name,
      });
    } catch (err: unknown) {
      return classifyHandlerError(err, registered.name, this._config.timeoutMs);
    }
  }

  /**
   * Invoke a streaming handler, pushing chunks to ctx.stream.
   *
   * @param registered - The registered streaming handler record.
   * @param input - Agent input.
   * @param ctx - Execution context with optional stream sink.
   * @returns AgentResult with concatenated output from all chunks.
   */
  private async _invokeStreaming(
    registered: RegisteredHandler,
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    const fn = registered.fn as StreamingHandlerFn;
    const chunks: string[] = [];

    try {
      const generator = fn(input, ctx);
      const result = await Promise.race([
        collectStreamChunks(generator, chunks, ctx),
        rejectAfterTimeout(this._config.timeoutMs, registered.name),
      ]);
      return createAgentResult(AgentStatus.SUCCESS, {
        output: result,
        handler: registered.name,
      });
    } catch (err: unknown) {
      return classifyHandlerError(err, registered.name, this._config.timeoutMs);
    }
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
 * Collect all chunks from an async generator, pushing to stream if available.
 *
 * @param generator - The async generator yielding string chunks.
 * @param chunks - Accumulator array for all chunks.
 * @param ctx - Execution context with optional stream sink.
 * @returns Concatenated string of all chunks.
 */
async function collectStreamChunks(
  generator: AsyncGenerator<string, void, undefined>,
  chunks: string[],
  ctx: ExecContext,
): Promise<string> {
  for await (const chunk of generator) {
    chunks.push(chunk);
    if (ctx.stream !== null) {
      await ctx.stream.push(chunk);
    }
  }
  return chunks.join("");
}

/**
 * Create a promise that rejects after the given timeout.
 *
 * @param timeoutMs - Milliseconds before rejection.
 * @param handlerName - Handler name for the error message.
 * @returns A promise that always rejects with a TimeoutError.
 */
function rejectAfterTimeout(timeoutMs: number, handlerName: string): Promise<never> {
  return new Promise((_resolve, reject) => {
    setTimeout(() => {
      reject(new TimeoutError(`handler '${handlerName}' timed out after ${timeoutMs}ms`));
    }, timeoutMs);
  });
}

/**
 * Classify a caught handler error into an AgentResult.
 *
 * @param err - The caught error value.
 * @param handlerName - Handler that threw.
 * @param timeoutMs - Configured timeout for error messages.
 * @returns AgentResult with appropriate status.
 */
function classifyHandlerError(
  err: unknown,
  handlerName: string,
  timeoutMs: number,
): AgentResult {
  if (err instanceof TimeoutError) {
    return createAgentResult(AgentStatus.TIMEOUT, {
      handler: handlerName,
      error: `handler '${handlerName}' timed out after ${timeoutMs}ms`,
    });
  }
  const message = err instanceof Error ? err.message : String(err);
  return createAgentResult(AgentStatus.ERROR, {
    handler: handlerName,
    error: message,
  });
}

/**
 * Build an error result for a handler whose circuit is open.
 *
 * @param handler - The handler that was rejected.
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

/**
 * Sentinel error class for timeout detection.
 */
class TimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TimeoutError";
  }
}
