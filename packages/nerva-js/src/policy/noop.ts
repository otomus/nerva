/**
 * Noop policy engine — allows everything without recording.
 *
 * Use during development or testing when policy enforcement is not needed.
 *
 * @module policy/noop
 */

import type { ExecContext, PolicyAction, PolicyDecision } from "./index.js";
import { ALLOW } from "./index.js";

// ---------------------------------------------------------------------------
// NoopPolicyEngine
// ---------------------------------------------------------------------------

/**
 * Policy engine that permits every action unconditionally.
 *
 * No state is tracked and `record` is a silent no-op. Satisfies the
 * {@link PolicyEngine} interface so it can be used as a drop-in replacement
 * for any real engine.
 */
export class NoopPolicyEngine {
  /**
   * Always returns {@link ALLOW}.
   *
   * @param _action - The action to evaluate (ignored).
   * @param _ctx - Execution context (ignored).
   * @returns The pre-built ALLOW decision.
   */
  async evaluate(
    _action: PolicyAction,
    _ctx: ExecContext,
  ): Promise<PolicyDecision> {
    return ALLOW;
  }

  /**
   * No-op — nothing to record.
   *
   * @param _action - The evaluated action (ignored).
   * @param _decision - The decision made (ignored).
   * @param _ctx - Execution context (ignored).
   */
  async record(
    _action: PolicyAction,
    _decision: PolicyDecision,
    _ctx: ExecContext,
  ): Promise<void> {
    // Intentionally empty — noop engine does not track decisions.
  }
}
