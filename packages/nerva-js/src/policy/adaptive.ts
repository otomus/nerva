/**
 * Adaptive policy engine — runtime condition monitoring with dynamic adjustments.
 *
 * Layers on top of any base {@link PolicyEngine} to add:
 *
 * - **Timeout extension** — when specific tags appear in `ctx.metadata`.
 * - **Cost-based throttling** — emits an advisory when cumulative cost
 *   exceeds a threshold.
 * - **Cost-based pausing** — halts execution when cumulative cost exceeds
 *   a hard budget ceiling.
 *
 * A base denial is **never** overridden. Adaptive logic only adds
 * restrictions (pause/throttle) or extensions (timeout).
 *
 * @module policy/adaptive
 */

import type {
  ExecContext,
  PolicyAction,
  PolicyDecision,
  PolicyEngine,
} from "./index.js";
import { createPolicyDecision } from "./index.js";

// ---------------------------------------------------------------------------
// Named constants
// ---------------------------------------------------------------------------

/** Threshold value that means "this cost gate is turned off". */
export const COST_DISABLED = 0.0;

/** Denial reason when cumulative cost exceeds the pause threshold. */
export const REASON_BUDGET_EXCEEDED = "budget_exceeded_adaptive";

/** Advisory reason attached to allow-decisions when throttling. */
export const REASON_THROTTLED = "cost_throttle_advisory";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/**
 * Configuration for adaptive runtime policy.
 *
 * Zero values for cost thresholds mean "disabled" (no enforcement).
 */
export interface AdaptivePolicyConfig {
  /** Starting timeout before adaptation. */
  readonly baseTimeoutSeconds: number;
  /** Tags in `ctx.metadata` that trigger timeout extension. */
  readonly extendTimeoutOn: ReadonlySet<string>;
  /** Multiplier when extending timeout (e.g. 2.0 = double). */
  readonly timeoutExtensionFactor: number;
  /** Cost threshold (USD) that triggers a throttle advisory. */
  readonly throttleAfterCost: number;
  /** Cost threshold (USD) that halts execution. */
  readonly pauseAfterCost: number;
  /** Suggested cheaper model when throttling. */
  readonly throttleModelDowngrade: string;
}

/**
 * Create an {@link AdaptivePolicyConfig} with sensible defaults.
 *
 * @param overrides - Fields to override from defaults.
 * @returns A fully populated AdaptivePolicyConfig.
 */
export function createAdaptivePolicyConfig(
  overrides?: Partial<AdaptivePolicyConfig>,
): AdaptivePolicyConfig {
  return {
    baseTimeoutSeconds: overrides?.baseTimeoutSeconds ?? 30.0,
    extendTimeoutOn: overrides?.extendTimeoutOn ?? new Set<string>(),
    timeoutExtensionFactor: overrides?.timeoutExtensionFactor ?? 2.0,
    throttleAfterCost: overrides?.throttleAfterCost ?? COST_DISABLED,
    pauseAfterCost: overrides?.pauseAfterCost ?? COST_DISABLED,
    throttleModelDowngrade: overrides?.throttleModelDowngrade ?? "",
  };
}

// ---------------------------------------------------------------------------
// AdaptivePolicyEngine
// ---------------------------------------------------------------------------

/**
 * Policy engine with runtime adaptation based on execution conditions.
 *
 * Wraps any base {@link PolicyEngine} and applies adaptive conditions
 * after the base evaluation. Resolution order: base engine -> adaptive
 * conditions.
 *
 * A base `DENY` is never overridden. Adaptive logic only adds restrictions
 * (pause/throttle) or extensions (timeout).
 */
export class AdaptivePolicyEngine implements PolicyEngine {
  private readonly _base: PolicyEngine;
  private readonly _config: AdaptivePolicyConfig;

  /**
   * @param base - Underlying policy engine (e.g. `YamlPolicyEngine`).
   * @param config - Adaptive policy configuration.
   */
  constructor(base: PolicyEngine, config: AdaptivePolicyConfig) {
    this._base = base;
    this._config = config;
  }

  /**
   * The adaptive policy configuration.
   *
   * @returns The immutable AdaptivePolicyConfig provided at init time.
   */
  get config(): AdaptivePolicyConfig {
    return this._config;
  }

  /**
   * The underlying base policy engine.
   *
   * @returns The PolicyEngine that this adaptive engine wraps.
   */
  get base(): PolicyEngine {
    return this._base;
  }

  // -- Public protocol ----------------------------------------------------

  /**
   * Evaluate base policy, then apply adaptive conditions.
   *
   * Evaluation order:
   * 1. Check base engine — if denied, return that denial immediately.
   * 2. Check pause threshold — if exceeded, deny with budget reason.
   * 3. Check throttle threshold — if exceeded, return allow with advisory.
   * 4. Otherwise return the base decision (possibly with budget remaining).
   *
   * @param action - The action to evaluate.
   * @param ctx - Execution context carrying identity, usage, and metadata.
   * @returns A PolicyDecision reflecting both base and adaptive evaluation.
   */
  async evaluate(
    action: PolicyAction,
    ctx: ExecContext,
  ): Promise<PolicyDecision> {
    const baseDecision = await this._base.evaluate(action, ctx);
    if (!baseDecision.allowed) {
      return baseDecision;
    }

    if (this.shouldPause(ctx)) {
      return buildPauseDecision();
    }

    if (this.shouldThrottle(ctx)) {
      return this.buildThrottleDecision(ctx);
    }

    return baseDecision;
  }

  /**
   * Delegate recording to the base engine.
   *
   * @param action - The evaluated action.
   * @param decision - The decision that was made.
   * @param ctx - Execution context at the time of decision.
   */
  async record(
    action: PolicyAction,
    decision: PolicyDecision,
    ctx: ExecContext,
  ): Promise<void> {
    await this._base.record(action, decision, ctx);
  }

  // -- Condition checks ---------------------------------------------------

  /**
   * Check if any `extendTimeoutOn` tags are present in `ctx.metadata`.
   *
   * @param ctx - Execution context whose metadata keys to inspect.
   * @returns True if at least one configured tag is present as a metadata key.
   */
  shouldExtendTimeout(ctx: ExecContext): boolean {
    if (this._config.extendTimeoutOn.size === 0) {
      return false;
    }
    const metadataKeys = Object.keys(ctx.metadata);
    for (const key of metadataKeys) {
      if (this._config.extendTimeoutOn.has(key)) {
        return true;
      }
    }
    return false;
  }

  /**
   * Return the extended timeout value.
   *
   * Multiplies `baseTimeoutSeconds` by `timeoutExtensionFactor`.
   *
   * @returns Extended timeout in seconds.
   */
  getExtendedTimeout(): number {
    return this._config.baseTimeoutSeconds * this._config.timeoutExtensionFactor;
  }

  /**
   * Check if cumulative cost has exceeded the throttle threshold.
   *
   * A threshold of `0.0` means throttling is disabled.
   *
   * @param ctx - Execution context carrying accumulated token usage.
   * @returns True if throttling is active and the cost threshold is exceeded.
   */
  shouldThrottle(ctx: ExecContext): boolean {
    if (this._config.throttleAfterCost <= COST_DISABLED) {
      return false;
    }
    return ctx.tokenUsage.costUsd >= this._config.throttleAfterCost;
  }

  /**
   * Check if cumulative cost has exceeded the pause (hard stop) threshold.
   *
   * A threshold of `0.0` means pausing is disabled.
   *
   * @param ctx - Execution context carrying accumulated token usage.
   * @returns True if pausing is active and the cost threshold is exceeded.
   */
  shouldPause(ctx: ExecContext): boolean {
    if (this._config.pauseAfterCost <= COST_DISABLED) {
      return false;
    }
    return ctx.tokenUsage.costUsd >= this._config.pauseAfterCost;
  }

  // -- Decision builders --------------------------------------------------

  /**
   * Build an allow decision with throttle advisory metadata.
   *
   * The action is still permitted, but `budgetRemaining` reflects the
   * distance to the pause threshold (or `null` if no pause threshold).
   *
   * @param ctx - Execution context carrying accumulated cost.
   * @returns A PolicyDecision allowing the action with budget info.
   */
  private buildThrottleDecision(ctx: ExecContext): PolicyDecision {
    const remaining = this.computeBudgetRemaining(ctx);
    return createPolicyDecision({
      allowed: true,
      reason: REASON_THROTTLED,
      budgetRemaining: remaining,
    });
  }

  /**
   * Compute remaining budget distance to the pause threshold.
   *
   * @param ctx - Execution context carrying accumulated cost.
   * @returns Remaining USD before pause, or null if no pause threshold is set.
   */
  private computeBudgetRemaining(ctx: ExecContext): number | null {
    if (this._config.pauseAfterCost <= COST_DISABLED) {
      return null;
    }
    const remaining = this._config.pauseAfterCost - ctx.tokenUsage.costUsd;
    return Math.max(remaining, 0.0);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build a denial decision for budget-exceeded pause.
 *
 * @returns A PolicyDecision denying the action with remaining budget of 0.
 */
function buildPauseDecision(): PolicyDecision {
  return createPolicyDecision({
    allowed: false,
    reason: REASON_BUDGET_EXCEEDED,
    budgetRemaining: 0.0,
  });
}
