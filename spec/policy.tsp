import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/**
 * An action to be evaluated by the policy engine.
 */
@jsonSchema
model PolicyAction {
  /** Action type (invoke_agent, call_tool, delegate, store_memory, route). */
  kind: string;

  /** Who is acting (user_id or agent_name). */
  subject: string;

  /** What they are acting on (agent_name, tool_name). */
  target: string;

  /** Additional context (token_count, cost_estimate, etc.). */
  metadata?: Record<string>;
}

/**
 * Result of a policy evaluation.
 */
@jsonSchema
model PolicyDecision {
  /** Whether the action is permitted. */
  allowed: boolean;

  /** Why denied (null if allowed). */
  reason?: string | null;

  /** Whether human approval is needed before proceeding. */
  require_approval?: boolean;

  /** Who can approve (null if no approval needed). */
  approvers?: string[] | null;

  /** Remaining budget after this action (null if not tracked). */
  budget_remaining?: float64 | null;
}

/**
 * Policy engine interface — evaluate and record policy decisions at every stage.
 *
 * Implementations must provide both evaluate (sync gate) and record
 * (audit trail). The runtime calls evaluate before executing an action
 * and record after the decision is made.
 */
@jsonSchema
model PolicyEngineContract {
  /** Evaluate whether an action is allowed under current policies. */
  evaluate: {
    input: {
      /** The action to evaluate. */
      action: PolicyAction;

      /** Execution context carrying identity, usage, and metadata. */
      ctx: ExecContext;
    };
    output: PolicyDecision;
  };

  /** Record a policy decision for the audit trail. */
  record: {
    input: {
      /** The evaluated action. */
      action: PolicyAction;

      /** The decision that was made. */
      decision: PolicyDecision;

      /** Execution context at the time of decision. */
      ctx: ExecContext;
    };
  };
}
