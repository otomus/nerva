/**
 * Boundary stubs — pure fakes for the lowest-level external boundaries.
 *
 * These are not spies. They are lightweight, deterministic implementations
 * used where a real implementation would require external dependencies
 * (LLM API calls, subprocess spawning, etc.).
 *
 * @module testkit/boundaries
 */

import type { ExecContext } from "../context.js";
import type { AgentInput, AgentResult } from "../runtime/index.js";
import type { PolicyAction, PolicyDecision, PolicyEngine } from "../policy/index.js";

// ---------------------------------------------------------------------------
// StubLLMHandler
// ---------------------------------------------------------------------------

/**
 * A handler function that returns canned responses in sequence.
 *
 * Use this as a handler registered in `InProcessRuntime` to simulate
 * LLM responses without hitting a real API.
 */
export class StubLLMHandler {
  private readonly responses: string[];
  private readonly defaultResponse: string;
  private callCounter = 0;

  /**
   * @param responses - Queue of responses to return (FIFO).
   * @param defaultResponse - Fallback when the queue is empty.
   */
  constructor(responses: string[] = [], defaultResponse = "stub response") {
    this.responses = [...responses];
    this.defaultResponse = defaultResponse;
  }

  /** Number of times this handler has been invoked. */
  get callCount(): number {
    return this.callCounter;
  }

  /**
   * Return the next canned response or the default.
   *
   * @param input - Agent input (ignored — responses are pre-configured).
   * @param _ctx - Execution context.
   * @returns AgentResult with "success" status and the canned output.
   */
  async handle(_input: AgentInput, _ctx: ExecContext): Promise<AgentResult> {
    this.callCounter += 1;
    const output =
      this.responses.length > 0
        ? this.responses.shift()!
        : this.defaultResponse;
    return {
      status: "success",
      output,
      data: {},
      error: null,
      handler: "",
    };
  }
}

// ---------------------------------------------------------------------------
// DenyAllPolicy
// ---------------------------------------------------------------------------

/**
 * Policy engine that denies every action.
 *
 * Useful for testing that policy denial is handled correctly.
 */
export class DenyAllPolicy implements PolicyEngine {
  private readonly reason: string;

  /**
   * @param reason - The denial reason returned in every decision.
   */
  constructor(reason = "denied by test policy") {
    this.reason = reason;
  }

  /** @inheritdoc */
  async evaluate(
    _action: PolicyAction,
    _ctx: ExecContext,
  ): Promise<PolicyDecision> {
    return {
      allowed: false,
      reason: this.reason,
      requireApproval: false,
      approvers: null,
      budgetRemaining: null,
    };
  }

  /** @inheritdoc */
  async record(
    _action: PolicyAction,
    _decision: PolicyDecision,
    _ctx: ExecContext,
  ): Promise<void> {}
}

// ---------------------------------------------------------------------------
// AllowAllPolicy
// ---------------------------------------------------------------------------

/**
 * Policy engine that allows every action.
 *
 * Same behavior as `NoopPolicyEngine` but with an explicit test-oriented name.
 */
export class AllowAllPolicy implements PolicyEngine {
  /** @inheritdoc */
  async evaluate(
    _action: PolicyAction,
    _ctx: ExecContext,
  ): Promise<PolicyDecision> {
    return {
      allowed: true,
      reason: null,
      requireApproval: false,
      approvers: null,
      budgetRemaining: null,
    };
  }

  /** @inheritdoc */
  async record(
    _action: PolicyAction,
    _decision: PolicyDecision,
    _ctx: ExecContext,
  ): Promise<void> {}
}
