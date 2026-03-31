/**
 * In-memory registry — for testing and simple deployments.
 *
 * Backed by a plain `Map`. No persistence across restarts.
 *
 * @module registry/inmemory
 */

import type {
  ExecContext,
  RegistryEntry,
  RegistryPatch,
} from "./index.js";
import {
  ComponentKind,
  HealthStatus,
} from "./index.js";

// ---------------------------------------------------------------------------
// Patchable fields
// ---------------------------------------------------------------------------

/** Fields on RegistryEntry that RegistryPatch can overwrite. */
const PATCHABLE_FIELDS = [
  "description",
  "metadata",
  "health",
  "enabled",
  "requirements",
  "permissions",
] as const;

type PatchableField = typeof PATCHABLE_FIELDS[number];

// ---------------------------------------------------------------------------
// InMemoryRegistry
// ---------------------------------------------------------------------------

/**
 * Registry backed by a plain Map. No persistence.
 *
 * Suitable for tests and single-process deployments where component
 * registration does not need to survive restarts.
 *
 * `discover()` filters results by:
 * - `kind` matches the requested component type
 * - `enabled` is true
 * - `health` is not UNAVAILABLE
 * - If the entry declares `permissions`, the caller must hold at
 *   least one matching role in `ctx.permissions.roles`
 */
export class InMemoryRegistry {
  private readonly entries: Map<string, RegistryEntry> = new Map();

  /**
   * Add or replace a component in the registry.
   *
   * If an entry with the same name already exists, it is overwritten.
   *
   * @param entry - Component definition to register.
   * @param _ctx - Execution context (reserved for future use).
   */
  async register(entry: RegistryEntry, _ctx: ExecContext): Promise<void> {
    this.entries.set(entry.name, entry);
  }

  /**
   * List components of a given kind visible to the caller.
   *
   * Filters out disabled entries, unavailable entries, and entries
   * whose required permissions are not satisfied by `ctx.permissions`.
   *
   * @param kind - Component type to filter by.
   * @param ctx - Execution context used for permission checks.
   * @returns List of matching RegistryEntry objects, sorted by name.
   */
  async discover(kind: ComponentKind, ctx: ExecContext): Promise<RegistryEntry[]> {
    const results: RegistryEntry[] = [];

    for (const entry of this.entries.values()) {
      if (matchesDiscoveryCriteria(entry, kind, ctx)) {
        results.push(entry);
      }
    }

    return results.sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Look up a single component by name.
   *
   * @param name - Unique component identifier.
   * @param _ctx - Execution context (reserved for future permission gating).
   * @returns The matching RegistryEntry, or null if not found.
   */
  async resolve(name: string, _ctx: ExecContext): Promise<RegistryEntry | null> {
    return this.entries.get(name) ?? null;
  }

  /**
   * Get the current health status of a component.
   *
   * @param name - Unique component identifier.
   * @returns Current HealthStatus.
   * @throws {@link Error} If no component with the given name is registered.
   */
  async health(name: string): Promise<HealthStatus> {
    const entry = this.entries.get(name);
    if (!entry) {
      throw new Error(`Component not found: "${name}"`);
    }
    return entry.health;
  }

  /**
   * Apply a partial update to a registered component.
   *
   * Only non-undefined fields in the patch are written to the entry.
   *
   * @param name - Unique component identifier to update.
   * @param patch - Fields to overwrite.
   * @throws {@link Error} If no component with the given name is registered.
   */
  async update(name: string, patch: RegistryPatch): Promise<void> {
    const entry = this.entries.get(name);
    if (!entry) {
      throw new Error(`Component not found: "${name}"`);
    }
    applyPatch(entry, patch);
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Check whether a registry entry passes all discovery filters.
 *
 * @param entry - Candidate entry to evaluate.
 * @param kind - Required component type.
 * @param ctx - Execution context carrying caller permissions.
 * @returns True if the entry should be included in discovery results.
 */
function matchesDiscoveryCriteria(
  entry: RegistryEntry,
  kind: ComponentKind,
  ctx: ExecContext,
): boolean {
  if (entry.kind !== kind) {
    return false;
  }
  if (!entry.enabled) {
    return false;
  }
  if (entry.health === HealthStatus.UNAVAILABLE) {
    return false;
  }
  if (entry.permissions.length > 0 && !hasRequiredPermission(entry, ctx)) {
    return false;
  }
  return true;
}

/**
 * Check that the caller holds at least one role required by the entry.
 *
 * @param entry - Entry with a non-empty permissions list.
 * @param ctx - Execution context carrying caller roles.
 * @returns True if the caller has at least one matching role.
 */
function hasRequiredPermission(entry: RegistryEntry, ctx: ExecContext): boolean {
  const callerRoles = ctx.permissions.roles;
  for (const requiredRole of entry.permissions) {
    if (callerRoles.has(requiredRole)) {
      return true;
    }
  }
  return false;
}

/**
 * Write non-undefined patch fields onto the entry.
 *
 * @param entry - Entry to mutate in place.
 * @param patch - Partial update — only non-undefined fields are applied.
 */
function applyPatch(entry: RegistryEntry, patch: RegistryPatch): void {
  for (const fieldName of PATCHABLE_FIELDS) {
    const value = patch[fieldName as PatchableField];
    if (value !== undefined) {
      // Safe to assign: fieldName is a mutable field on RegistryEntry
      // and the patch value type matches.
      (entry as unknown as Record<string, unknown>)[fieldName] = value;
    }
  }
}
