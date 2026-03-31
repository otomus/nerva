/**
 * Registry — unified catalog of agents, tools, and components.
 *
 * Defines the core interfaces, enums, and value types for Nerva's
 * component registry system.
 *
 * @module registry
 */

import type { ExecContext, Permissions } from "../context.js";

export type { ExecContext, Permissions };

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/**
 * Classification of a registered component.
 */
export enum ComponentKind {
  /** An agent handler that processes user input. */
  AGENT = "agent",
  /** A tool invocable by agents (e.g. MCP tool). */
  TOOL = "tool",
  /** A sensory input processor (e.g. vision, hearing). */
  SENSE = "sense",
  /** An extension that hooks into lifecycle events. */
  PLUGIN = "plugin",
}

/**
 * Operational health of a registered component.
 */
export enum HealthStatus {
  /** Fully operational. */
  HEALTHY = "healthy",
  /** Operational with reduced capability or elevated error rate. */
  DEGRADED = "degraded",
  /** Not accepting invocations. */
  UNAVAILABLE = "unavailable",
}

// ---------------------------------------------------------------------------
// InvocationStats
// ---------------------------------------------------------------------------

/** Exponential moving average weight for new duration observations. */
export const DURATION_SMOOTHING_FACTOR = 0.2;

/**
 * Tracks invocation metrics for a registered component.
 *
 * Uses an exponential moving average for `avgDurationMs` so that
 * recent latency is weighted more heavily than historical.
 */
export class InvocationStats {
  /** Total number of invocations (success + failure). */
  totalCalls: number = 0;
  /** Number of successful invocations. */
  successes: number = 0;
  /** Number of failed invocations. */
  failures: number = 0;
  /** Unix timestamp of the most recent invocation, or null. */
  lastInvokedAt: number | null = null;
  /** Exponential moving average of invocation duration in ms. */
  avgDurationMs: number = 0;

  /**
   * Record a successful invocation.
   *
   * @param durationMs - Wall-clock duration of the invocation in milliseconds.
   */
  recordSuccess(durationMs: number): void {
    this.totalCalls += 1;
    this.successes += 1;
    this.lastInvokedAt = Date.now() / 1000;
    this.updateAvgDuration(durationMs);
  }

  /**
   * Record a failed invocation.
   *
   * @param durationMs - Wall-clock duration of the invocation in milliseconds.
   */
  recordFailure(durationMs: number): void {
    this.totalCalls += 1;
    this.failures += 1;
    this.lastInvokedAt = Date.now() / 1000;
    this.updateAvgDuration(durationMs);
  }

  /**
   * Update the exponential moving average of duration.
   *
   * On the first call, sets the average directly. On subsequent calls,
   * blends the new observation using {@link DURATION_SMOOTHING_FACTOR}.
   *
   * @param durationMs - Latest observed duration in milliseconds.
   */
  private updateAvgDuration(durationMs: number): void {
    if (this.totalCalls <= 1) {
      this.avgDurationMs = durationMs;
      return;
    }

    const alpha = DURATION_SMOOTHING_FACTOR;
    this.avgDurationMs = alpha * durationMs + (1 - alpha) * this.avgDurationMs;
  }
}

// ---------------------------------------------------------------------------
// RegistryEntry
// ---------------------------------------------------------------------------

/**
 * A registered component in the catalog.
 */
export interface RegistryEntry {
  /** Unique identifier for the component. */
  readonly name: string;
  /** Component type (agent, tool, sense, plugin). */
  readonly kind: ComponentKind;
  /** What it does — used by the router for matching. */
  description: string;
  /** Input/output JSON schema (primarily for tools), or null. */
  readonly schema: Record<string, unknown> | null;
  /** Custom key-value fields (role, origin, version, etc.). */
  metadata: Record<string, string>;
  /** Current operational health status. */
  health: HealthStatus;
  /** Invocation metrics tracked over the component's lifetime. */
  readonly stats: InvocationStats;
  /** Whether the component is active. Can be disabled without removal. */
  enabled: boolean;
  /** Dependencies — credential names, other component names. */
  requirements: string[];
  /** Role names required to access this component. */
  permissions: string[];
}

/**
 * Create a {@link RegistryEntry} with sensible defaults.
 *
 * @param name - Unique identifier for the component.
 * @param kind - Component type.
 * @param description - What the component does.
 * @param overrides - Optional fields to override defaults.
 * @returns A RegistryEntry ready for registration.
 */
export function createRegistryEntry(
  name: string,
  kind: ComponentKind,
  description: string,
  overrides?: Partial<Omit<RegistryEntry, "name" | "kind" | "description">>,
): RegistryEntry {
  return {
    name,
    kind,
    description,
    schema: overrides?.schema ?? null,
    metadata: overrides?.metadata ?? {},
    health: overrides?.health ?? HealthStatus.HEALTHY,
    stats: overrides?.stats ?? new InvocationStats(),
    enabled: overrides?.enabled ?? true,
    requirements: overrides?.requirements ?? [],
    permissions: overrides?.permissions ?? [],
  };
}

// ---------------------------------------------------------------------------
// RegistryPatch
// ---------------------------------------------------------------------------

/**
 * Partial update for a registry entry.
 *
 * Only non-undefined fields are applied when passed to `Registry.update()`.
 */
export interface RegistryPatch {
  /** New description, or undefined to leave unchanged. */
  readonly description?: string | undefined;
  /** New metadata dict, or undefined to leave unchanged. */
  readonly metadata?: Record<string, string> | undefined;
  /** New health status, or undefined to leave unchanged. */
  readonly health?: HealthStatus | undefined;
  /** New enabled flag, or undefined to leave unchanged. */
  readonly enabled?: boolean | undefined;
  /** New requirements list, or undefined to leave unchanged. */
  readonly requirements?: string[] | undefined;
  /** New permissions list, or undefined to leave unchanged. */
  readonly permissions?: string[] | undefined;
}

// ---------------------------------------------------------------------------
// Registry interface
// ---------------------------------------------------------------------------

/**
 * Unified catalog of agents, tools, senses, and plugins.
 *
 * Implementations may be backed by an in-memory Map, a database, or
 * a remote service. All methods accept an {@link ExecContext} so that
 * permission checks and observability flow through naturally.
 */
export interface Registry {
  /**
   * Add or replace a component in the registry.
   *
   * If an entry with the same name already exists, it is overwritten.
   *
   * @param entry - Component definition to register.
   * @param ctx - Execution context carrying identity and permissions.
   */
  register(entry: RegistryEntry, ctx: ExecContext): Promise<void>;

  /**
   * List components of a given kind visible to the caller.
   *
   * Filters out disabled entries, unavailable entries, and entries
   * whose required permissions are not satisfied by `ctx.permissions`.
   *
   * @param kind - Component type to filter by.
   * @param ctx - Execution context used for permission checks.
   * @returns List of matching RegistryEntry objects.
   */
  discover(kind: ComponentKind, ctx: ExecContext): Promise<RegistryEntry[]>;

  /**
   * Look up a single component by name.
   *
   * @param name - Unique component identifier.
   * @param ctx - Execution context (for future permission gating).
   * @returns The matching RegistryEntry, or null if not found.
   */
  resolve(name: string, ctx: ExecContext): Promise<RegistryEntry | null>;

  /**
   * Get the current health status of a component.
   *
   * @param name - Unique component identifier.
   * @returns Current HealthStatus.
   * @throws {@link Error} If no component with the given name is registered.
   */
  health(name: string): Promise<HealthStatus>;

  /**
   * Apply a partial update to a registered component.
   *
   * Only non-undefined fields in the patch are written.
   *
   * @param name - Unique component identifier to update.
   * @param patch - Fields to overwrite.
   * @throws {@link Error} If no component with the given name is registered.
   */
  update(name: string, patch: RegistryPatch): Promise<void>;
}
