/**
 * YAML-based policy engine — load rules from config, evaluate at runtime.
 *
 * Supports four policy dimensions:
 *
 * - **Rate limiting** — per-user requests-per-minute with sliding window.
 * - **Budget** — per-agent token and cost caps with configurable on-exceed action.
 * - **Approval** — named agents that require human sign-off before execution.
 * - **Execution** — depth and tool-call guards to prevent runaway recursion.
 *
 * Example YAML structure:
 * ```yaml
 * policies:
 *   budget:
 *     per_agent:
 *       max_tokens_per_hour: 100000
 *       max_cost_per_day_usd: 5.00
 *       on_exceed: pause
 *   rate_limit:
 *     per_user:
 *       max_requests_per_minute: 30
 *       on_exceed: queue
 *   approval:
 *     agents:
 *       - name: deploy_agent
 *         requires_approval: true
 *         approvers: [admin]
 *   execution:
 *     max_depth: 5
 *     max_tool_calls_per_invocation: 20
 *     timeout_seconds: 30
 * ```
 *
 * Requires the `js-yaml` package as a runtime dependency.
 *
 * @module policy/yaml-engine
 */

import * as fs from "node:fs";
import * as path from "node:path";
import yaml from "js-yaml";

import type { ExecContext, PolicyAction, PolicyDecision } from "./index.js";
import { ALLOW, createPolicyDecision } from "./index.js";

// ---------------------------------------------------------------------------
// Named constants
// ---------------------------------------------------------------------------

const SECONDS_PER_MINUTE = 60;
const SECONDS_PER_HOUR = 3_600;
const SECONDS_PER_DAY = 86_400;

const DEFAULT_MAX_DEPTH = 10;
const DEFAULT_MAX_TOOL_CALLS = 50;
const DEFAULT_TIMEOUT_SECONDS = 30.0;

/** Strategy: block the action entirely. */
export const ON_EXCEED_BLOCK = "block";
/** Strategy: reject with an error response. */
export const ON_EXCEED_REJECT = "reject";
/** Strategy: pause execution and wait for budget replenishment. */
export const ON_EXCEED_PAUSE = "pause";
/** Strategy: allow but emit a warning. */
export const ON_EXCEED_WARN = "warn";
/** Strategy: queue the action for later execution. */
export const ON_EXCEED_QUEUE = "queue";
/** Strategy: allow with degraded capability. */
export const ON_EXCEED_DEGRADE = "degrade";

/** Sentinel value meaning "no limit enforced". */
const UNLIMITED = 0;

// ---------------------------------------------------------------------------
// PolicyConfig
// ---------------------------------------------------------------------------

/**
 * Parsed, validated policy configuration.
 *
 * Zero values for limits mean "unlimited" (no enforcement).
 */
export interface PolicyConfig {
  /** Token ceiling per agent per hour. */
  readonly budgetMaxTokensPerHour: number;
  /** Dollar ceiling per agent per day. */
  readonly budgetMaxCostPerDayUsd: number;
  /** Strategy when budget is exceeded. */
  readonly budgetOnExceed: string;
  /** Request ceiling per user per minute. */
  readonly rateLimitMaxPerMinute: number;
  /** Strategy when rate limit is hit. */
  readonly rateLimitOnExceed: string;
  /** Mapping of agent name to list of approver roles. */
  readonly approvalAgents: Readonly<Record<string, ReadonlyArray<string>>>;
  /** Maximum delegation depth for a single request. */
  readonly maxDepth: number;
  /** Maximum tool invocations per single agent invocation. */
  readonly maxToolCalls: number;
  /** Per-action timeout in seconds. */
  readonly timeoutSeconds: number;
}

// ---------------------------------------------------------------------------
// Config parsing
// ---------------------------------------------------------------------------

/**
 * Parse a raw YAML object into a validated {@link PolicyConfig}.
 *
 * Looks for a top-level `policies` key. Missing sections use defaults.
 *
 * @param raw - Parsed YAML object (may or may not contain `policies`).
 * @returns A fully populated PolicyConfig.
 */
export function parsePolicyConfig(raw: Record<string, unknown>): PolicyConfig {
  const policies = raw["policies"];
  if (!policies || typeof policies !== "object" || Array.isArray(policies)) {
    return createDefaultConfig();
  }

  const policiesObj = policies as Record<string, unknown>;
  const budget = extractBudget(policiesObj);
  const rateLimit = extractRateLimit(policiesObj);
  const approvalAgents = extractApprovalAgents(policiesObj);
  const execution = extractExecution(policiesObj);

  return {
    budgetMaxTokensPerHour: budget.maxTokensPerHour,
    budgetMaxCostPerDayUsd: budget.maxCostPerDayUsd,
    budgetOnExceed: budget.onExceed,
    rateLimitMaxPerMinute: rateLimit.maxPerMinute,
    rateLimitOnExceed: rateLimit.onExceed,
    approvalAgents,
    maxDepth: execution.maxDepth,
    maxToolCalls: execution.maxToolCalls,
    timeoutSeconds: execution.timeoutSeconds,
  };
}

/**
 * Create a PolicyConfig with all defaults (unlimited/permissive).
 *
 * @returns A PolicyConfig where all limits are disabled.
 */
function createDefaultConfig(): PolicyConfig {
  return {
    budgetMaxTokensPerHour: UNLIMITED,
    budgetMaxCostPerDayUsd: 0.0,
    budgetOnExceed: ON_EXCEED_BLOCK,
    rateLimitMaxPerMinute: UNLIMITED,
    rateLimitOnExceed: ON_EXCEED_REJECT,
    approvalAgents: {},
    maxDepth: DEFAULT_MAX_DEPTH,
    maxToolCalls: DEFAULT_MAX_TOOL_CALLS,
    timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
  };
}

/** Extracted budget fields. */
interface BudgetFields {
  readonly maxTokensPerHour: number;
  readonly maxCostPerDayUsd: number;
  readonly onExceed: string;
}

/**
 * Extract budget fields from the policies dict.
 *
 * @param policies - The `policies` section of the YAML config.
 * @returns Parsed budget fields with defaults for missing values.
 */
function extractBudget(policies: Record<string, unknown>): BudgetFields {
  const budget = policies["budget"];
  if (!budget || typeof budget !== "object" || Array.isArray(budget)) {
    return { maxTokensPerHour: UNLIMITED, maxCostPerDayUsd: 0.0, onExceed: ON_EXCEED_BLOCK };
  }

  const budgetObj = budget as Record<string, unknown>;
  const perAgent = budgetObj["per_agent"];
  if (!perAgent || typeof perAgent !== "object" || Array.isArray(perAgent)) {
    return { maxTokensPerHour: UNLIMITED, maxCostPerDayUsd: 0.0, onExceed: ON_EXCEED_BLOCK };
  }

  const pa = perAgent as Record<string, unknown>;
  return {
    maxTokensPerHour: safeInt(pa["max_tokens_per_hour"], UNLIMITED),
    maxCostPerDayUsd: safeFloat(pa["max_cost_per_day_usd"], 0.0),
    onExceed: safeString(pa["on_exceed"], ON_EXCEED_BLOCK),
  };
}

/** Extracted rate limit fields. */
interface RateLimitFields {
  readonly maxPerMinute: number;
  readonly onExceed: string;
}

/**
 * Extract rate limit fields from the policies dict.
 *
 * @param policies - The `policies` section of the YAML config.
 * @returns Parsed rate limit fields with defaults for missing values.
 */
function extractRateLimit(policies: Record<string, unknown>): RateLimitFields {
  const rateLimit = policies["rate_limit"];
  if (!rateLimit || typeof rateLimit !== "object" || Array.isArray(rateLimit)) {
    return { maxPerMinute: UNLIMITED, onExceed: ON_EXCEED_REJECT };
  }

  const rlObj = rateLimit as Record<string, unknown>;
  const perUser = rlObj["per_user"];
  if (!perUser || typeof perUser !== "object" || Array.isArray(perUser)) {
    return { maxPerMinute: UNLIMITED, onExceed: ON_EXCEED_REJECT };
  }

  const pu = perUser as Record<string, unknown>;
  return {
    maxPerMinute: safeInt(pu["max_requests_per_minute"], UNLIMITED),
    onExceed: safeString(pu["on_exceed"], ON_EXCEED_REJECT),
  };
}

/**
 * Extract approval agent mappings from the policies dict.
 *
 * @param policies - The `policies` section of the YAML config.
 * @returns Dict mapping agent names to their required approver lists.
 */
function extractApprovalAgents(
  policies: Record<string, unknown>,
): Record<string, string[]> {
  const approval = policies["approval"];
  if (!approval || typeof approval !== "object" || Array.isArray(approval)) {
    return {};
  }

  const approvalObj = approval as Record<string, unknown>;
  const agentsList = approvalObj["agents"];
  if (!Array.isArray(agentsList)) {
    return {};
  }

  const result: Record<string, string[]> = {};

  for (const entry of agentsList) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      continue;
    }
    const entryObj = entry as Record<string, unknown>;
    const name = entryObj["name"];
    if (typeof name !== "string") {
      continue;
    }
    if (!entryObj["requires_approval"]) {
      continue;
    }
    const approvers = entryObj["approvers"];
    if (Array.isArray(approvers)) {
      result[name] = approvers.map((a) => String(a));
    }
  }

  return result;
}

/** Extracted execution limit fields. */
interface ExecutionFields {
  readonly maxDepth: number;
  readonly maxToolCalls: number;
  readonly timeoutSeconds: number;
}

/**
 * Extract execution limit fields from the policies dict.
 *
 * @param policies - The `policies` section of the YAML config.
 * @returns Parsed execution fields with defaults for missing values.
 */
function extractExecution(policies: Record<string, unknown>): ExecutionFields {
  const execution = policies["execution"];
  if (!execution || typeof execution !== "object" || Array.isArray(execution)) {
    return {
      maxDepth: DEFAULT_MAX_DEPTH,
      maxToolCalls: DEFAULT_MAX_TOOL_CALLS,
      timeoutSeconds: DEFAULT_TIMEOUT_SECONDS,
    };
  }

  const ex = execution as Record<string, unknown>;
  return {
    maxDepth: safeInt(ex["max_depth"], DEFAULT_MAX_DEPTH),
    maxToolCalls: safeInt(ex["max_tool_calls_per_invocation"], DEFAULT_MAX_TOOL_CALLS),
    timeoutSeconds: safeFloat(ex["timeout_seconds"], DEFAULT_TIMEOUT_SECONDS),
  };
}

// ---------------------------------------------------------------------------
// Constructor options
// ---------------------------------------------------------------------------

/** Options for creating a {@link YamlPolicyEngine}. */
export interface YamlPolicyEngineOptions {
  /** Path to a YAML file containing a `policies` section. */
  readonly configPath?: string | undefined;
  /** Pre-parsed dict (used instead of configPath if given). */
  readonly configDict?: Record<string, unknown> | undefined;
}

// ---------------------------------------------------------------------------
// YamlPolicyEngine
// ---------------------------------------------------------------------------

/**
 * Policy engine that loads rules from YAML configuration.
 *
 * Evaluates budget, rate limit, approval, and execution policies.
 * Tracks per-user request timestamps and per-agent token usage in memory.
 */
export class YamlPolicyEngine {
  private readonly _config: PolicyConfig;

  /** Sliding window: userId -> list of request timestamps (seconds). */
  private readonly requestTimestamps: Map<string, number[]> = new Map();

  /** Token tracking: agentName -> list of [timestamp, tokenCount]. */
  private readonly tokenLedger: Map<string, Array<[number, number]>> = new Map();

  /** Cost tracking: agentName -> list of [timestamp, costUsd]. */
  private readonly costLedger: Map<string, Array<[number, number]>> = new Map();

  /**
   * @param options - Config path or pre-parsed dict.
   * @throws {@link Error} If neither configPath nor configDict is provided.
   * @throws {@link Error} If configPath does not exist.
   */
  constructor(options: YamlPolicyEngineOptions) {
    const raw = loadRawConfig(options.configPath, options.configDict);
    this._config = parsePolicyConfig(raw);
  }

  /**
   * The parsed policy configuration.
   *
   * @returns The immutable PolicyConfig loaded at init time.
   */
  get config(): PolicyConfig {
    return this._config;
  }

  // -- Public protocol ----------------------------------------------------

  /**
   * Evaluate action against all applicable policies.
   *
   * Checks run in order: rate limit, budget, approval, execution limits.
   * The first denial short-circuits — remaining checks are skipped.
   *
   * @param action - The action to evaluate.
   * @param ctx - Execution context carrying identity, usage, and metadata.
   * @returns A PolicyDecision — either ALLOW or a denial/approval-required.
   */
  async evaluate(
    action: PolicyAction,
    ctx: ExecContext,
  ): Promise<PolicyDecision> {
    const checks = [
      () => this.checkRateLimit(ctx),
      () => this.checkBudget(action.target),
      () => this.checkApproval(action.target),
      () => this.checkExecution(ctx),
    ];

    for (const check of checks) {
      const decision = check();
      if (!decision.allowed || decision.requireApproval) {
        return decision;
      }
    }

    return ALLOW;
  }

  /**
   * Record the decision and update internal counters.
   *
   * Only updates counters when the action was allowed — denied actions
   * should not consume budget or rate-limit quota.
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
    if (!decision.allowed) {
      return;
    }

    const now = Date.now() / 1000;
    const userId = ctx.userId ?? "anonymous";

    const timestamps = this.requestTimestamps.get(userId) ?? [];
    timestamps.push(now);
    this.requestTimestamps.set(userId, timestamps);

    this.recordTokenUsage(action.target, now, ctx);
  }

  // -- Individual checks --------------------------------------------------

  /**
   * Check per-user request rate against the configured limit.
   *
   * @param ctx - Execution context carrying user identity.
   * @returns ALLOW or a denial with reason and on-exceed strategy.
   */
  private checkRateLimit(ctx: ExecContext): PolicyDecision {
    const limit = this._config.rateLimitMaxPerMinute;
    if (limit === UNLIMITED) {
      return ALLOW;
    }

    const userId = ctx.userId ?? "anonymous";
    const now = Date.now() / 1000;
    const cutoff = now - SECONDS_PER_MINUTE;

    const timestamps = this.requestTimestamps.get(userId) ?? [];
    const recent = timestamps.filter((ts) => ts > cutoff);
    this.requestTimestamps.set(userId, recent);

    if (recent.length >= limit) {
      return createPolicyDecision({
        allowed: false,
        reason:
          `rate limit exceeded: ${recent.length}/${limit} ` +
          `requests per minute (on_exceed=${this._config.rateLimitOnExceed})`,
      });
    }

    return ALLOW;
  }

  /**
   * Check per-agent token and cost budgets.
   *
   * @param agentName - The agent whose budget to check.
   * @returns ALLOW or a denial with remaining budget info.
   */
  private checkBudget(agentName: string): PolicyDecision {
    const tokenDecision = this.checkTokenBudget(agentName);
    if (!tokenDecision.allowed) {
      return tokenDecision;
    }
    return this.checkCostBudget(agentName);
  }

  /**
   * Check hourly token consumption for an agent.
   *
   * @param agentName - The agent whose budget to check.
   * @returns ALLOW or a denial if the token ceiling is breached.
   */
  private checkTokenBudget(agentName: string): PolicyDecision {
    const limit = this._config.budgetMaxTokensPerHour;
    if (limit === UNLIMITED) {
      return ALLOW;
    }

    const now = Date.now() / 1000;
    const cutoff = now - SECONDS_PER_HOUR;

    const entries = this.tokenLedger.get(agentName) ?? [];
    const recent = entries.filter(([ts]) => ts > cutoff);
    this.tokenLedger.set(agentName, recent);

    let totalTokens = 0;
    for (const [, tokens] of recent) {
      totalTokens += tokens;
    }
    const remaining = limit - totalTokens;

    if (remaining <= 0) {
      return createPolicyDecision({
        allowed: false,
        reason:
          `token budget exceeded: ${totalTokens}/${limit} ` +
          `tokens per hour (on_exceed=${this._config.budgetOnExceed})`,
        budgetRemaining: 0,
      });
    }

    return ALLOW;
  }

  /**
   * Check daily cost consumption for an agent.
   *
   * @param agentName - The agent whose cost budget to check.
   * @returns ALLOW or a denial if the daily cost ceiling is breached.
   */
  private checkCostBudget(agentName: string): PolicyDecision {
    const limit = this._config.budgetMaxCostPerDayUsd;
    if (limit <= 0) {
      return ALLOW;
    }

    const now = Date.now() / 1000;
    const cutoff = now - SECONDS_PER_DAY;

    const entries = this.costLedger.get(agentName) ?? [];
    const recent = entries.filter(([ts]) => ts > cutoff);
    this.costLedger.set(agentName, recent);

    let totalCost = 0;
    for (const [, cost] of recent) {
      totalCost += cost;
    }
    const remaining = limit - totalCost;

    if (remaining <= 0) {
      return createPolicyDecision({
        allowed: false,
        reason:
          `cost budget exceeded: $${totalCost.toFixed(2)}/$${limit.toFixed(2)} ` +
          `per day (on_exceed=${this._config.budgetOnExceed})`,
        budgetRemaining: 0,
      });
    }

    return createPolicyDecision({
      allowed: true,
      budgetRemaining: remaining,
    });
  }

  /**
   * Check whether the target agent requires human approval.
   *
   * @param agentTarget - The agent name to check.
   * @returns ALLOW or a require-approval decision with the approver list.
   */
  private checkApproval(agentTarget: string): PolicyDecision {
    const approvers = this._config.approvalAgents[agentTarget];
    if (!approvers) {
      return ALLOW;
    }

    return createPolicyDecision({
      allowed: true,
      requireApproval: true,
      approvers: [...approvers],
      reason: `agent '${agentTarget}' requires approval`,
    });
  }

  /**
   * Check execution depth and tool-call count limits.
   *
   * Reads `depth` and `tool_call_count` from `ctx.metadata`.
   *
   * @param ctx - Execution context carrying metadata with depth/tool counts.
   * @returns ALLOW or a denial if execution limits are breached.
   */
  private checkExecution(ctx: ExecContext): PolicyDecision {
    const depthDecision = this.checkDepth(ctx);
    if (!depthDecision.allowed) {
      return depthDecision;
    }
    return this.checkToolCallCount(ctx);
  }

  /**
   * Check current delegation depth against the configured maximum.
   *
   * @param ctx - Execution context with `depth` in metadata.
   * @returns ALLOW or a denial if depth exceeds the limit.
   */
  private checkDepth(ctx: ExecContext): PolicyDecision {
    const depthStr = ctx.metadata["depth"] ?? "0";
    const depth = isDigitString(depthStr) ? parseInt(depthStr, 10) : 0;

    if (depth > this._config.maxDepth) {
      return createPolicyDecision({
        allowed: false,
        reason:
          `execution depth ${depth} exceeds maximum ` +
          `${this._config.maxDepth}`,
      });
    }

    return ALLOW;
  }

  /**
   * Check accumulated tool-call count against the configured maximum.
   *
   * @param ctx - Execution context with `tool_call_count` in metadata.
   * @returns ALLOW or a denial if tool calls exceed the limit.
   */
  private checkToolCallCount(ctx: ExecContext): PolicyDecision {
    const countStr = ctx.metadata["tool_call_count"] ?? "0";
    const count = isDigitString(countStr) ? parseInt(countStr, 10) : 0;

    if (count > this._config.maxToolCalls) {
      return createPolicyDecision({
        allowed: false,
        reason:
          `tool call count ${count} exceeds maximum ` +
          `${this._config.maxToolCalls}`,
      });
    }

    return ALLOW;
  }

  // -- Internal helpers ---------------------------------------------------

  /**
   * Append current token usage and cost to the tracking ledgers.
   *
   * @param agentName - Agent whose budget to charge.
   * @param now - Current timestamp in seconds.
   * @param ctx - Execution context with accumulated token usage.
   */
  private recordTokenUsage(
    agentName: string,
    now: number,
    ctx: ExecContext,
  ): void {
    const tokens = ctx.tokenUsage.totalTokens;
    if (tokens > 0) {
      const entries = this.tokenLedger.get(agentName) ?? [];
      entries.push([now, tokens]);
      this.tokenLedger.set(agentName, entries);
    }

    const cost = ctx.tokenUsage.costUsd;
    if (cost > 0) {
      const entries = this.costLedger.get(agentName) ?? [];
      entries.push([now, cost]);
      this.costLedger.set(agentName, entries);
    }
  }
}

// ---------------------------------------------------------------------------
// Config loading
// ---------------------------------------------------------------------------

/**
 * Load raw config from a file path or pre-parsed dict.
 *
 * @param configPath - Path to a YAML file, or undefined.
 * @param configDict - Pre-parsed dict, or undefined.
 * @returns The raw configuration object.
 * @throws {@link Error} If neither argument is provided.
 * @throws {@link Error} If configPath does not exist.
 */
function loadRawConfig(
  configPath: string | undefined,
  configDict: Record<string, unknown> | undefined,
): Record<string, unknown> {
  if (configDict !== undefined) {
    return configDict;
  }

  if (configPath === undefined) {
    throw new Error("Either configPath or configDict must be provided");
  }

  const resolved = path.resolve(configPath);
  if (!fs.existsSync(resolved)) {
    throw new Error(`Policy config not found: ${resolved}`);
  }

  const content = fs.readFileSync(resolved, "utf-8");
  const loaded = yaml.load(content);

  if (!loaded || typeof loaded !== "object" || Array.isArray(loaded)) {
    return {};
  }

  return loaded as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

/**
 * Safely parse an unknown value as an integer with a fallback default.
 *
 * @param value - The value to parse.
 * @param defaultValue - Fallback if value is not a valid number.
 * @returns The parsed integer or the default.
 */
function safeInt(value: unknown, defaultValue: number): number {
  if (typeof value === "number") {
    return Math.floor(value);
  }
  if (typeof value === "string") {
    const parsed = parseInt(value, 10);
    return isNaN(parsed) ? defaultValue : parsed;
  }
  return defaultValue;
}

/**
 * Safely parse an unknown value as a float with a fallback default.
 *
 * @param value - The value to parse.
 * @param defaultValue - Fallback if value is not a valid number.
 * @returns The parsed float or the default.
 */
function safeFloat(value: unknown, defaultValue: number): number {
  if (typeof value === "number") {
    return value;
  }
  if (typeof value === "string") {
    const parsed = parseFloat(value);
    return isNaN(parsed) ? defaultValue : parsed;
  }
  return defaultValue;
}

/**
 * Safely coerce an unknown value to a string with a fallback default.
 *
 * @param value - The value to coerce.
 * @param defaultValue - Fallback if value is not a string.
 * @returns The string value or the default.
 */
function safeString(value: unknown, defaultValue: string): string {
  if (typeof value === "string") {
    return value;
  }
  return defaultValue;
}

/**
 * Check whether a string contains only digit characters.
 *
 * @param str - The string to test.
 * @returns True if the string matches /^\d+$/.
 */
function isDigitString(str: string): boolean {
  return /^\d+$/.test(str);
}
