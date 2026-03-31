/**
 * Policy — layered rules governing execution.
 *
 * Defines the core interface ({@link PolicyEngine}), data types for actions
 * and decisions, and convenience constants used across all engine implementations.
 *
 * @module policy
 */

import type { ExecContext, TokenUsage } from "../context.js";

export type { ExecContext, TokenUsage };

// ---------------------------------------------------------------------------
// PolicyAction
// ---------------------------------------------------------------------------

/**
 * An action to be evaluated by the policy engine.
 *
 * Immutable once created.
 */
export interface PolicyAction {
  /** Action type (invoke_agent, call_tool, delegate, store_memory, route). */
  readonly kind: string;
  /** Who is acting (user_id or agent_name). */
  readonly subject: string;
  /** What they are acting on (agent_name, tool_name). */
  readonly target: string;
  /** Additional context (token_count, cost_estimate, etc.). */
  readonly metadata: Readonly<Record<string, string>>;
}

/**
 * Create a {@link PolicyAction} with sensible defaults.
 *
 * @param kind - Action type.
 * @param subject - Who is acting.
 * @param target - What they are acting on.
 * @param metadata - Additional context.
 * @returns A frozen PolicyAction.
 */
export function createPolicyAction(
  kind: string,
  subject: string,
  target: string,
  metadata: Record<string, string> = {},
): PolicyAction {
  return Object.freeze({ kind, subject, target, metadata });
}

// ---------------------------------------------------------------------------
// PolicyDecision
// ---------------------------------------------------------------------------

/**
 * Result of a policy evaluation.
 *
 * Immutable once created.
 */
export interface PolicyDecision {
  /** Whether the action is permitted. */
  readonly allowed: boolean;
  /** Why denied (null if allowed). */
  readonly reason: string | null;
  /** Whether human approval is needed before proceeding. */
  readonly requireApproval: boolean;
  /** Who can approve (null if no approval needed). */
  readonly approvers: ReadonlyArray<string> | null;
  /** Remaining budget after this action (null if not tracked). */
  readonly budgetRemaining: number | null;
}

/**
 * Create a {@link PolicyDecision} with sensible defaults.
 *
 * @param overrides - Fields to set on the decision.
 * @returns A frozen PolicyDecision.
 */
export function createPolicyDecision(
  overrides: Partial<PolicyDecision> & { allowed: boolean },
): PolicyDecision {
  return Object.freeze({
    allowed: overrides.allowed,
    reason: overrides.reason ?? null,
    requireApproval: overrides.requireApproval ?? false,
    approvers: overrides.approvers ?? null,
    budgetRemaining: overrides.budgetRemaining ?? null,
  });
}

// ---------------------------------------------------------------------------
// Convenience constants
// ---------------------------------------------------------------------------

/** Pre-built decision that permits the action unconditionally. */
export const ALLOW: PolicyDecision = createPolicyDecision({ allowed: true });

/** Pre-built denial with a generic reason string. */
export const DENY_NO_REASON: PolicyDecision = createPolicyDecision({
  allowed: false,
  reason: "denied by policy",
});

// ---------------------------------------------------------------------------
// PolicyEngine interface
// ---------------------------------------------------------------------------

/**
 * Evaluate and record policy decisions at every execution stage.
 *
 * Implementations must provide both `evaluate` (sync gate) and `record`
 * (audit trail). The runtime calls `evaluate` before executing an action
 * and `record` after the decision is made, regardless of outcome.
 */
export interface PolicyEngine {
  /**
   * Evaluate whether an action is allowed under current policies.
   *
   * @param action - The action to evaluate.
   * @param ctx - Execution context carrying identity, usage, and metadata.
   * @returns A PolicyDecision indicating allow, deny, or require_approval.
   */
  evaluate(action: PolicyAction, ctx: ExecContext): Promise<PolicyDecision>;

  /**
   * Record a policy decision for the audit trail.
   *
   * @param action - The evaluated action.
   * @param decision - The decision that was made.
   * @param ctx - Execution context at the time of decision.
   */
  record(
    action: PolicyAction,
    decision: PolicyDecision,
    ctx: ExecContext,
  ): Promise<void>;
}
