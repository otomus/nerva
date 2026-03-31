/**
 * SQLite-backed registry — persistent single-node component catalog.
 *
 * Implements the {@link Registry} interface using `better-sqlite3` for
 * persistence across process restarts. Complex fields (schema, metadata,
 * stats, requirements, permissions) are stored as JSON text columns.
 *
 * All SQL uses parameterized queries — no string interpolation — to
 * prevent SQL injection.
 *
 * @module registry/sqlite
 */

import type {
  ExecContext,
  RegistryEntry,
  RegistryPatch,
} from "./index.js";
import {
  ComponentKind,
  HealthStatus,
  InvocationStats,
  createRegistryEntry,
} from "./index.js";
import { createPermissions } from "../context.js";

// ---------------------------------------------------------------------------
// Table and SQL constants
// ---------------------------------------------------------------------------

/** Name of the SQLite table used for component storage. */
const TABLE_NAME = "components";

/** Fields on RegistryEntry that RegistryPatch can overwrite. */
const PATCHABLE_FIELDS = [
  "description",
  "metadata",
  "health",
  "enabled",
  "requirements",
  "permissions",
] as const;

type PatchableField = (typeof PATCHABLE_FIELDS)[number];

const CREATE_TABLE_SQL = `
  CREATE TABLE IF NOT EXISTS ${TABLE_NAME} (
    name              TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    description       TEXT NOT NULL,
    schema_json       TEXT,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    health            TEXT NOT NULL DEFAULT 'healthy',
    stats_json        TEXT NOT NULL DEFAULT '{}',
    enabled           INTEGER NOT NULL DEFAULT 1,
    requirements_json TEXT NOT NULL DEFAULT '[]',
    permissions_json  TEXT NOT NULL DEFAULT '[]',
    updated_at        REAL NOT NULL
  )
`;

const UPSERT_SQL = `
  INSERT OR REPLACE INTO ${TABLE_NAME}
    (name, kind, description, schema_json, metadata_json,
     health, stats_json, enabled, requirements_json,
     permissions_json, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`;

const SELECT_ALL_BY_NAME_SQL = `SELECT * FROM ${TABLE_NAME} WHERE name = ?`;

const SELECT_DISCOVERABLE_SQL = `
  SELECT * FROM ${TABLE_NAME}
  WHERE kind = ? AND enabled = 1 AND health != ?
  ORDER BY name
`;

const SELECT_HEALTH_SQL = `SELECT health FROM ${TABLE_NAME} WHERE name = ?`;

// ---------------------------------------------------------------------------
// better-sqlite3 type shims
//
// We type the minimal surface we need so consumers can install
// better-sqlite3 as an optional peer dependency without forcing it
// on every user of the package.
// ---------------------------------------------------------------------------

/** Minimal row shape returned by better-sqlite3 when using `.all()` or `.get()`. */
interface SqliteRow {
  [column: string]: unknown;
}

/** Minimal prepared-statement shape from better-sqlite3. */
interface SqliteStatement {
  run(...params: unknown[]): unknown;
  get(...params: unknown[]): SqliteRow | undefined;
  all(...params: unknown[]): SqliteRow[];
}

/** Minimal database shape from better-sqlite3. */
interface SqliteDatabase {
  prepare(sql: string): SqliteStatement;
  exec(sql: string): void;
  close(): void;
}

/** Constructor signature for a better-sqlite3 Database. */
type SqliteDatabaseConstructor = new (
  filename: string,
  options?: Record<string, unknown>,
) => SqliteDatabase;

// ---------------------------------------------------------------------------
// Serialization helpers
// ---------------------------------------------------------------------------

/** Row tuple type matching the column order in UPSERT_SQL. */
type RowTuple = [
  string,
  string,
  string,
  string | null,
  string,
  string,
  string,
  number,
  string,
  string,
  number,
];

/**
 * Serialize a RegistryEntry into a row tuple for SQLite insertion.
 *
 * @param entry - Entry to serialize.
 * @returns Tuple of column values matching the table schema.
 */
function entryToRow(entry: RegistryEntry): RowTuple {
  return [
    entry.name,
    entry.kind,
    entry.description,
    entry.schema !== null ? JSON.stringify(entry.schema) : null,
    JSON.stringify(entry.metadata),
    entry.health,
    statsToJson(entry.stats),
    entry.enabled ? 1 : 0,
    JSON.stringify(entry.requirements),
    JSON.stringify(entry.permissions),
    Date.now() / 1000,
  ];
}

/**
 * Deserialize a SQLite row into a RegistryEntry.
 *
 * @param row - Database row with named columns.
 * @returns Fully populated RegistryEntry.
 */
function rowToEntry(row: SqliteRow): RegistryEntry {
  const schemaJson = row["schema_json"];
  const parsedSchema =
    typeof schemaJson === "string"
      ? (JSON.parse(schemaJson) as Record<string, unknown>)
      : null;

  return createRegistryEntry(
    String(row["name"]),
    row["kind"] as ComponentKind,
    String(row["description"]),
    {
      schema: parsedSchema,
      metadata: JSON.parse(String(row["metadata_json"])) as Record<string, string>,
      health: row["health"] as HealthStatus,
      stats: statsFromJson(String(row["stats_json"])),
      enabled: Boolean(row["enabled"]),
      requirements: JSON.parse(String(row["requirements_json"])) as string[],
      permissions: JSON.parse(String(row["permissions_json"])) as string[],
    },
  );
}

/**
 * Serialize InvocationStats to a JSON string.
 *
 * @param stats - Stats instance to serialize.
 * @returns JSON string representation.
 */
function statsToJson(stats: InvocationStats): string {
  return JSON.stringify({
    total_calls: stats.totalCalls,
    successes: stats.successes,
    failures: stats.failures,
    last_invoked_at: stats.lastInvokedAt,
    avg_duration_ms: stats.avgDurationMs,
  });
}

/** Shape of a serialized stats JSON payload. */
interface StatsPayload {
  total_calls?: number;
  successes?: number;
  failures?: number;
  last_invoked_at?: number | null;
  avg_duration_ms?: number;
}

/**
 * Deserialize a JSON string into an InvocationStats instance.
 *
 * Missing fields default to zero/null.
 *
 * @param raw - JSON string (may be empty `"{}"`).
 * @returns Populated InvocationStats.
 */
function statsFromJson(raw: string): InvocationStats {
  const data: StatsPayload = raw ? (JSON.parse(raw) as StatsPayload) : {};
  const stats = new InvocationStats();

  // Replay recorded values into the stats instance
  const totalCalls = data.total_calls ?? 0;
  const successes = data.successes ?? 0;
  const failures = data.failures ?? 0;

  // Restore counters by simulating recorded calls
  for (let i = 0; i < successes; i++) {
    stats.recordSuccess(0);
  }
  for (let i = 0; i < failures; i++) {
    stats.recordFailure(0);
  }

  // Overwrite with exact persisted values
  stats.totalCalls = totalCalls;
  stats.successes = successes;
  stats.failures = failures;
  stats.lastInvokedAt = data.last_invoked_at ?? null;
  stats.avgDurationMs = data.avg_duration_ms ?? 0;

  return stats;
}

// ---------------------------------------------------------------------------
// Permission filtering
// ---------------------------------------------------------------------------

/**
 * Convert rows to entries, filtering out those the caller cannot access.
 *
 * An entry with no declared permissions is visible to everyone.
 * An entry with permissions requires the caller to hold at least one
 * matching role in `ctx.permissions.roles`.
 *
 * @param rows - Raw database rows.
 * @param ctx - Execution context with caller permissions.
 * @returns List of accessible RegistryEntry objects.
 */
function filterByPermissions(
  rows: SqliteRow[],
  ctx: ExecContext,
): RegistryEntry[] {
  const results: RegistryEntry[] = [];
  const callerRoles = ctx.permissions.roles;

  for (const row of rows) {
    const entry = rowToEntry(row);
    if (entry.permissions.length > 0 && !callerHasPermission(entry, callerRoles)) {
      continue;
    }
    results.push(entry);
  }

  return results;
}

/**
 * Check that the caller holds at least one required role.
 *
 * @param entry - Entry with a non-empty permissions list.
 * @param callerRoles - Roles held by the caller.
 * @returns True if the caller has at least one matching role.
 */
function callerHasPermission(
  entry: RegistryEntry,
  callerRoles: ReadonlySet<string>,
): boolean {
  for (const role of entry.permissions) {
    if (callerRoles.has(role)) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Patch helpers
// ---------------------------------------------------------------------------

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
      (entry as unknown as Record<string, unknown>)[fieldName] = value;
    }
  }
}

// ---------------------------------------------------------------------------
// Internal no-op context for update() resolve call
// ---------------------------------------------------------------------------

/**
 * Minimal stand-in ExecContext used internally by `update()`.
 *
 * Only `update()` calls `resolve()` internally — it does not need
 * a real ExecContext since resolve performs no permission checks.
 */
const NOOP_CTX: ExecContext = {
  requestId: "",
  traceId: "",
  userId: null,
  sessionId: null,
  permissions: createPermissions(),
  memoryScope: "session",
  spans: [],
  events: [],
  tokenUsage: { promptTokens: 0, completionTokens: 0, totalTokens: 0, costUsd: 0, add: () => NOOP_CTX.tokenUsage },
  createdAt: 0,
  timeoutAt: null,
  stream: null,
  metadata: {},
  cancelSignal: new AbortController().signal,
  cancel: () => undefined,
  isTimedOut: () => false,
  isCancelled: () => false,
  elapsedSeconds: () => 0,
  addSpan: () => ({ spanId: "", name: "", parentId: null, startedAt: 0, endedAt: null, attributes: {} }),
  addEvent: () => ({ timestamp: 0, name: "", attributes: {} }),
  recordTokens: () => undefined,
  child: () => NOOP_CTX,
} as unknown as ExecContext;

// ---------------------------------------------------------------------------
// SqliteRegistry
// ---------------------------------------------------------------------------

/**
 * Registry backed by SQLite for persistence across process restarts.
 *
 * Creates a single table `components` with columns matching
 * `RegistryEntry` fields. Complex fields are serialized as JSON text.
 *
 * Requires `better-sqlite3` as a peer dependency. Pass the default
 * export of `better-sqlite3` as the `Database` constructor, or let
 * the constructor dynamically import it.
 */
export class SqliteRegistry {
  private readonly db: SqliteDatabase;

  /**
   * Create a new SqliteRegistry.
   *
   * @param Database - The `better-sqlite3` Database constructor.
   * @param path - Path to SQLite database file. Use `":memory:"` for testing.
   */
  constructor(Database: SqliteDatabaseConstructor, path: string = ":memory:") {
    this.db = new Database(path);
    this.db.exec(CREATE_TABLE_SQL);
  }

  /**
   * Add or replace a component in the registry.
   *
   * Uses `INSERT OR REPLACE` so existing entries are overwritten.
   *
   * @param entry - Component definition to register.
   * @param _ctx - Execution context (reserved for future use).
   */
  async register(entry: RegistryEntry, _ctx: ExecContext): Promise<void> {
    const row = entryToRow(entry);
    this.db.prepare(UPSERT_SQL).run(...row);
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
    const rows = this.db
      .prepare(SELECT_DISCOVERABLE_SQL)
      .all(kind, HealthStatus.UNAVAILABLE);

    return filterByPermissions(rows, ctx);
  }

  /**
   * Look up a single component by name.
   *
   * @param name - Unique component identifier.
   * @param _ctx - Execution context (reserved for future permission gating).
   * @returns The matching RegistryEntry, or null if not found.
   */
  async resolve(name: string, _ctx: ExecContext): Promise<RegistryEntry | null> {
    const row = this.db.prepare(SELECT_ALL_BY_NAME_SQL).get(name);
    if (!row) {
      return null;
    }
    return rowToEntry(row);
  }

  /**
   * Get the current health status of a component.
   *
   * @param name - Unique component identifier.
   * @returns Current HealthStatus.
   * @throws {@link Error} If no component with the given name is registered.
   */
  async health(name: string): Promise<HealthStatus> {
    const row = this.db.prepare(SELECT_HEALTH_SQL).get(name);
    if (!row) {
      throw new Error(`Component not found: "${name}"`);
    }
    return row["health"] as HealthStatus;
  }

  /**
   * Apply a partial update to a registered component.
   *
   * Only non-undefined fields in the patch are written. The entry
   * is read, patched in memory, then written back as a full row.
   *
   * @param name - Unique component identifier to update.
   * @param patch - Fields to overwrite.
   * @throws {@link Error} If no component with the given name is registered.
   */
  async update(name: string, patch: RegistryPatch): Promise<void> {
    const entry = await this.resolve(name, NOOP_CTX);
    if (!entry) {
      throw new Error(`Component not found: "${name}"`);
    }

    applyPatch(entry, patch);
    const row = entryToRow(entry);
    this.db.prepare(UPSERT_SQL).run(...row);
  }

  /**
   * Close the underlying database connection.
   *
   * After calling this, all other methods will throw.
   */
  close(): void {
    this.db.close();
  }
}
