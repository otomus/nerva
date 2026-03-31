/**
 * Policy decorator — per-agent policy overrides merged with YAML defaults.
 *
 * Allows agents to declare policy overrides via an `agentPolicy()` helper.
 * At evaluation time, decorator overrides are merged on top of YAML-loaded
 * defaults so that code-level declarations win over config-file defaults.
 *
 * @module policy/decorator
 */

// ---------------------------------------------------------------------------
// Override config
// ---------------------------------------------------------------------------

/** Fields on {@link AgentPolicyConfig} that can override YAML values. */
const OVERRIDE_FIELDS = [
  "requiresApproval",
  "timeoutSeconds",
  "maxToolCalls",
  "maxCostUsd",
  "approvers",
] as const;

/**
 * Per-agent policy overrides from the `agentPolicy()` decorator.
 *
 * Every field defaults to `undefined`, meaning "no override — use YAML
 * default". Only non-`undefined` values participate in the merge.
 */
export interface AgentPolicyConfig {
  /** Override approval requirement. */
  readonly requiresApproval?: boolean | undefined;
  /** Override execution timeout in seconds. */
  readonly timeoutSeconds?: number | undefined;
  /** Override max tool calls per invocation. */
  readonly maxToolCalls?: number | undefined;
  /** Override per-invocation cost limit in USD. */
  readonly maxCostUsd?: number | undefined;
  /** Override approver list. */
  readonly approvers?: readonly string[] | undefined;
}

// ---------------------------------------------------------------------------
// Global registry
// ---------------------------------------------------------------------------

const agentPolicies: Map<string, AgentPolicyConfig> = new Map();

// ---------------------------------------------------------------------------
// Decorator / helper
// ---------------------------------------------------------------------------

/**
 * Attach policy configuration to an agent handler.
 *
 * Returns a decorator function that registers per-agent policy overrides
 * in a module-level registry keyed by `name`. The decorated class or
 * function is returned unchanged.
 *
 * @example
 * ```ts
 * @agentPolicy("deploy_agent", { requiresApproval: true, timeoutSeconds: 120 })
 * class DeployAgent { ... }
 * ```
 *
 * @param name - Agent name matching the registry entry.
 * @param config - Policy overrides. Unknown keys are silently ignored.
 * @returns A decorator that registers the config and returns the target unchanged.
 * @throws {@link Error} If `name` is empty.
 */
export function agentPolicy<T>(
  name: string,
  config: AgentPolicyConfig = {},
): (target: T) => T {
  if (!name) {
    throw new Error("agent name must be a non-empty string");
  }

  const validated = buildConfig(config);

  return (target: T): T => {
    agentPolicies.set(name, validated);
    return target;
  };
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

/**
 * Look up decorator policy for a named agent.
 *
 * @param name - Agent name to look up.
 * @returns The AgentPolicyConfig if one was registered, otherwise `null`.
 */
export function getAgentPolicy(name: string): AgentPolicyConfig | null {
  return agentPolicies.get(name) ?? null;
}

// ---------------------------------------------------------------------------
// Merge
// ---------------------------------------------------------------------------

/**
 * Merge YAML defaults with decorator overrides. Decorator wins.
 *
 * Resolution order: YAML defaults -> decorator overrides.
 * Only non-`undefined` decorator fields replace YAML values.
 *
 * @param yamlConfig - Base policy config from YAML.
 * @param agentName - Agent to look up decorator overrides for.
 * @returns Merged policy config dict with decorator values taking precedence.
 */
export function resolvePolicy(
  yamlConfig: Record<string, unknown>,
  agentName: string,
): Record<string, unknown> {
  const override = agentPolicies.get(agentName);
  if (!override) {
    return { ...yamlConfig };
  }

  return mergeOverride(yamlConfig, override);
}

// ---------------------------------------------------------------------------
// Testing helpers
// ---------------------------------------------------------------------------

/**
 * Remove all registered agent policies.
 *
 * Intended for test teardown — not for production use.
 */
export function clearRegistry(): void {
  agentPolicies.clear();
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Build a validated AgentPolicyConfig, keeping only recognised fields.
 *
 * @param raw - Raw config object that may contain unknown keys.
 * @returns A clean AgentPolicyConfig with only valid fields.
 */
function buildConfig(raw: AgentPolicyConfig): AgentPolicyConfig {
  const result: Record<string, unknown> = {};
  for (const key of OVERRIDE_FIELDS) {
    const value = raw[key];
    if (value !== undefined) {
      result[key] = value;
    }
  }
  return result as AgentPolicyConfig;
}

/**
 * Apply non-undefined override fields on top of a base config.
 *
 * @param base - YAML-sourced config dict.
 * @param override - Decorator-sourced overrides.
 * @returns New dict with override values replacing base values where set.
 */
function mergeOverride(
  base: Record<string, unknown>,
  override: AgentPolicyConfig,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...base };
  for (const key of OVERRIDE_FIELDS) {
    const value = override[key];
    if (value !== undefined) {
      merged[key] = value;
    }
  }
  return merged;
}
