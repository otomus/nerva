/**
 * Spy wrappers — record calls and support expectations over real implementations.
 *
 * Each spy wraps a real Nerva primitive, delegating all method calls to the inner
 * implementation while recording every invocation. When expectations are set via
 * `expect*()` methods, the spy returns the configured value instead of calling
 * the real implementation (FIFO queue, falls back to passthrough when exhausted).
 *
 * @module testkit/spies
 */

import type { ExecContext } from "../context.js";
import type { IntentResult, IntentRouter } from "../router/index.js";
import { createHandlerCandidate, createIntentResult } from "../router/index.js";
import type {
  AgentInput,
  AgentResult,
  AgentRuntime,
} from "../runtime/index.js";
import type {
  AgentResult as ResponderAgentResult,
  Channel,
  Responder,
  Response,
} from "../responder/index.js";
import type { Memory, MemoryContext, MemoryEvent } from "../memory/index.js";
import type {
  PolicyAction,
  PolicyDecision,
  PolicyEngine,
} from "../policy/index.js";
import type { ToolManager, ToolResult, ToolSpec } from "../tools/index.js";

// ---------------------------------------------------------------------------
// Call records
// ---------------------------------------------------------------------------

/** Record of a single `IntentRouter.classify()` invocation. */
export interface ClassifyCall {
  readonly message: string;
  readonly ctx: ExecContext;
  readonly result: IntentResult;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `AgentRuntime.invoke()` invocation. */
export interface InvokeCall {
  readonly handler: string;
  readonly input: AgentInput;
  readonly ctx: ExecContext;
  readonly result: AgentResult;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `AgentRuntime.delegate()` invocation. */
export interface DelegateCall {
  readonly handler: string;
  readonly input: AgentInput;
  readonly parentCtx: ExecContext;
  readonly result: AgentResult;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `Responder.format()` invocation. */
export interface FormatCall {
  readonly output: ResponderAgentResult;
  readonly channel: Channel;
  readonly ctx: ExecContext;
  readonly result: Response;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `Memory.recall()` invocation. */
export interface RecallCall {
  readonly query: string;
  readonly ctx: ExecContext;
  readonly result: MemoryContext;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `Memory.store()` invocation. */
export interface StoreCall {
  readonly event: MemoryEvent;
  readonly ctx: ExecContext;
  readonly timestamp: number;
}

/** Record of a single `PolicyEngine.evaluate()` invocation. */
export interface EvaluateCall {
  readonly action: PolicyAction;
  readonly ctx: ExecContext;
  readonly result: PolicyDecision;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `PolicyEngine.record()` invocation. */
export interface PolicyRecordCall {
  readonly action: PolicyAction;
  readonly decision: PolicyDecision;
  readonly ctx: ExecContext;
  readonly timestamp: number;
}

/** Record of a single `ToolManager.discover()` invocation. */
export interface DiscoverToolsCall {
  readonly ctx: ExecContext;
  readonly result: ToolSpec[];
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

/** Record of a single `ToolManager.call()` invocation. */
export interface ToolCallRecord {
  readonly toolName: string;
  readonly args: Record<string, unknown>;
  readonly ctx: ExecContext;
  readonly result: ToolResult;
  readonly timestamp: number;
  readonly wasExpected: boolean;
}

// ---------------------------------------------------------------------------
// SpyRouter
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around an {@link IntentRouter} implementation.
 *
 * Records every `classify()` call. Supports expectation-setting via
 * `expectHandler()` and `expectIntent()`.
 */
export class SpyRouter implements IntentRouter {
  /** Ordered list of recorded classify invocations. */
  readonly classifyCalls: ClassifyCall[] = [];

  private readonly expectations: IntentResult[] = [];

  constructor(
    /** The wrapped IntentRouter implementation. */
    readonly inner: IntentRouter,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    return this.expectations.length;
  }

  /**
   * Queue an expectation that classify() will return the given handler.
   *
   * @param handlerName - Handler name to return.
   * @param confidence - Confidence score for the result.
   */
  expectHandler(handlerName: string, confidence = 0.95): void {
    const candidate = createHandlerCandidate(handlerName, confidence, "expected");
    const result = createIntentResult(handlerName, confidence, [candidate]);
    this.expectations.push(result);
  }

  /**
   * Queue a full IntentResult expectation.
   *
   * @param result - The IntentResult to return on the next classify() call.
   */
  expectIntent(result: IntentResult): void {
    this.expectations.push(result);
  }

  /** @inheritdoc */
  async classify(message: string, ctx: ExecContext): Promise<IntentResult> {
    const wasExpected = this.expectations.length > 0;
    const result = wasExpected
      ? this.expectations.shift()!
      : await this.inner.classify(message, ctx);

    this.classifyCalls.push({
      message,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.classifyCalls.length = 0;
    this.expectations.length = 0;
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyRouter has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// SpyRuntime
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around an {@link AgentRuntime} implementation.
 *
 * Records `invoke()`, `invokeChain()`, and `delegate()` calls.
 */
export class SpyRuntime implements AgentRuntime {
  /** Ordered list of recorded invoke invocations. */
  readonly invokeCalls: InvokeCall[] = [];
  /** Ordered list of recorded delegate invocations. */
  readonly delegateCalls: DelegateCall[] = [];

  private readonly invokeExpectations: AgentResult[] = [];
  private readonly delegateExpectations: AgentResult[] = [];

  constructor(
    /** The wrapped AgentRuntime implementation. */
    readonly inner: AgentRuntime,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    return this.invokeExpectations.length + this.delegateExpectations.length;
  }

  /**
   * Queue an expectation for the next invoke() call.
   *
   * @param result - The AgentResult to return.
   */
  expectResult(result: AgentResult): void {
    this.invokeExpectations.push(result);
  }

  /**
   * Queue a successful invoke() expectation with the given output.
   *
   * @param output - The output text to return.
   */
  expectLlmResponse(output: string): void {
    this.invokeExpectations.push({
      status: "success",
      output,
      data: {},
      error: null,
      handler: "",
    });
  }

  /** @inheritdoc */
  async invoke(
    handler: string,
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    const wasExpected = this.invokeExpectations.length > 0;
    const result = wasExpected
      ? this.invokeExpectations.shift()!
      : await this.inner.invoke(handler, input, ctx);

    this.invokeCalls.push({
      handler,
      input,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** @inheritdoc */
  async invokeChain(
    handlers: string[],
    input: AgentInput,
    ctx: ExecContext,
  ): Promise<AgentResult> {
    return this.inner.invokeChain(handlers, input, ctx);
  }

  /** @inheritdoc */
  async delegate(
    handler: string,
    input: AgentInput,
    parentCtx: ExecContext,
  ): Promise<AgentResult> {
    const wasExpected = this.delegateExpectations.length > 0;
    const result = wasExpected
      ? this.delegateExpectations.shift()!
      : await this.inner.delegate(handler, input, parentCtx);

    this.delegateCalls.push({
      handler,
      input,
      parentCtx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.invokeCalls.length = 0;
    this.delegateCalls.length = 0;
    this.invokeExpectations.length = 0;
    this.delegateExpectations.length = 0;
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyRuntime has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// SpyResponder
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around a {@link Responder} implementation.
 *
 * Records every `format()` call.
 */
export class SpyResponder implements Responder {
  /** Ordered list of recorded format invocations. */
  readonly formatCalls: FormatCall[] = [];

  private readonly expectations: Response[] = [];

  constructor(
    /** The wrapped Responder implementation. */
    readonly inner: Responder,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    return this.expectations.length;
  }

  /**
   * Queue an expectation for the next format() call.
   *
   * @param response - The Response to return.
   */
  expectResponse(response: Response): void {
    this.expectations.push(response);
  }

  /** @inheritdoc */
  async format(
    output: ResponderAgentResult,
    channel: Channel,
    ctx: ExecContext,
  ): Promise<Response> {
    const wasExpected = this.expectations.length > 0;
    const result = wasExpected
      ? this.expectations.shift()!
      : await this.inner.format(output, channel, ctx);

    this.formatCalls.push({
      output,
      channel,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.formatCalls.length = 0;
    this.expectations.length = 0;
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyResponder has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// SpyMemory
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around a {@link Memory} implementation.
 *
 * Records `recall()`, `store()`, and `consolidate()` calls.
 */
export class SpyMemory implements Memory {
  /** Ordered list of recorded recall invocations. */
  readonly recallCalls: RecallCall[] = [];
  /** Ordered list of recorded store invocations. */
  readonly storeCalls: StoreCall[] = [];

  private readonly recallExpectations: MemoryContext[] = [];

  constructor(
    /** The wrapped Memory implementation. */
    readonly inner: Memory,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    return this.recallExpectations.length;
  }

  /**
   * Queue an expectation for the next recall() call.
   *
   * @param memoryCtx - The MemoryContext to return.
   */
  expectRecall(memoryCtx: MemoryContext): void {
    this.recallExpectations.push(memoryCtx);
  }

  /** @inheritdoc */
  async recall(query: string, ctx: ExecContext): Promise<MemoryContext> {
    const wasExpected = this.recallExpectations.length > 0;
    const result = wasExpected
      ? this.recallExpectations.shift()!
      : await this.inner.recall(query, ctx);

    this.recallCalls.push({
      query,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** @inheritdoc */
  async store(event: MemoryEvent, ctx: ExecContext): Promise<void> {
    this.storeCalls.push({ event, ctx, timestamp: Date.now() / 1000 });
    await this.inner.store(event, ctx);
  }

  /** @inheritdoc */
  async consolidate(ctx: ExecContext): Promise<void> {
    await this.inner.consolidate(ctx);
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.recallCalls.length = 0;
    this.storeCalls.length = 0;
    this.recallExpectations.length = 0;
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyMemory has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// SpyPolicy
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around a {@link PolicyEngine} implementation.
 *
 * Records `evaluate()` and `record()` calls.
 */
export class SpyPolicy implements PolicyEngine {
  /** Ordered list of recorded evaluate invocations. */
  readonly evaluateCalls: EvaluateCall[] = [];
  /** Ordered list of recorded record invocations. */
  readonly recordCalls: PolicyRecordCall[] = [];

  private readonly expectations: PolicyDecision[] = [];

  constructor(
    /** The wrapped PolicyEngine implementation. */
    readonly inner: PolicyEngine,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    return this.expectations.length;
  }

  /** Queue an expectation that the next evaluate() will allow. */
  expectAllow(): void {
    this.expectations.push({
      allowed: true,
      reason: null,
      requireApproval: false,
      approvers: null,
      budgetRemaining: null,
    });
  }

  /**
   * Queue an expectation that the next evaluate() will deny.
   *
   * @param reason - Reason string for the denial.
   */
  expectDeny(reason = "denied by test"): void {
    this.expectations.push({
      allowed: false,
      reason,
      requireApproval: false,
      approvers: null,
      budgetRemaining: null,
    });
  }

  /** @inheritdoc */
  async evaluate(
    action: PolicyAction,
    ctx: ExecContext,
  ): Promise<PolicyDecision> {
    const wasExpected = this.expectations.length > 0;
    const result = wasExpected
      ? this.expectations.shift()!
      : await this.inner.evaluate(action, ctx);

    this.evaluateCalls.push({
      action,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** @inheritdoc */
  async record(
    action: PolicyAction,
    decision: PolicyDecision,
    ctx: ExecContext,
  ): Promise<void> {
    this.recordCalls.push({
      action,
      decision,
      ctx,
      timestamp: Date.now() / 1000,
    });
    await this.inner.record(action, decision, ctx);
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.evaluateCalls.length = 0;
    this.recordCalls.length = 0;
    this.expectations.length = 0;
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyPolicy has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// SpyToolManager
// ---------------------------------------------------------------------------

/**
 * Spy wrapper around a {@link ToolManager} implementation.
 *
 * Records `discover()` and `call()` invocations.
 */
export class SpyToolManager implements ToolManager {
  /** Ordered list of recorded discover invocations. */
  readonly discoverCalls: DiscoverToolsCall[] = [];
  /** Ordered list of recorded call invocations. */
  readonly callCalls: ToolCallRecord[] = [];

  private readonly discoverExpectations: ToolSpec[][] = [];
  private readonly callExpectations = new Map<string, ToolResult[]>();

  constructor(
    /** The wrapped ToolManager implementation. */
    readonly inner: ToolManager,
  ) {}

  /** Number of unconsumed expectations remaining. */
  get pendingExpectations(): number {
    let toolCount = 0;
    for (const queue of this.callExpectations.values()) {
      toolCount += queue.length;
    }
    return this.discoverExpectations.length + toolCount;
  }

  /**
   * Queue an expectation for the next discover() call.
   *
   * @param tools - The list of ToolSpecs to return.
   */
  expectTools(tools: ToolSpec[]): void {
    this.discoverExpectations.push(tools);
  }

  /**
   * Queue an expectation for the next call() to a specific tool.
   *
   * @param toolName - Name of the tool this expectation applies to.
   * @param result - The ToolResult to return.
   */
  expectToolResult(toolName: string, result: ToolResult): void {
    const queue = this.callExpectations.get(toolName) ?? [];
    queue.push(result);
    this.callExpectations.set(toolName, queue);
  }

  /** @inheritdoc */
  async discover(ctx: ExecContext): Promise<ToolSpec[]> {
    const wasExpected = this.discoverExpectations.length > 0;
    const result = wasExpected
      ? this.discoverExpectations.shift()!
      : await this.inner.discover(ctx);

    this.discoverCalls.push({
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** @inheritdoc */
  async call(
    tool: string,
    args: Record<string, unknown>,
    ctx: ExecContext,
  ): Promise<ToolResult> {
    const queue = this.callExpectations.get(tool);
    const wasExpected = queue !== undefined && queue.length > 0;
    let result: ToolResult;

    if (wasExpected) {
      result = queue!.shift()!;
      if (queue!.length === 0) {
        this.callExpectations.delete(tool);
      }
    } else {
      result = await this.inner.call(tool, args, ctx);
    }

    this.callCalls.push({
      toolName: tool,
      args,
      ctx,
      result,
      timestamp: Date.now() / 1000,
      wasExpected,
    });
    return result;
  }

  /** Clear all recorded calls and pending expectations. */
  reset(): void {
    this.discoverCalls.length = 0;
    this.callCalls.length = 0;
    this.discoverExpectations.length = 0;
    this.callExpectations.clear();
  }

  /** Assert that all expectations have been consumed. */
  verifyExpectationsConsumed(): void {
    if (this.pendingExpectations > 0) {
      throw new Error(
        `SpyToolManager has ${this.pendingExpectations} unconsumed expectation(s)`,
      );
    }
  }
}
