import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/**
 * Classification of a registered component.
 */
@jsonSchema
enum ComponentKind {
  /** An agent handler that processes user input. */
  agent: "agent",

  /** A tool invocable by agents (e.g. MCP tool). */
  tool: "tool",

  /** A sensory input processor (e.g. vision, hearing). */
  sense: "sense",

  /** An extension that hooks into lifecycle events. */
  plugin: "plugin",
}

/**
 * Operational health of a registered component.
 */
@jsonSchema
enum HealthStatus {
  /** Fully operational. */
  healthy: "healthy",

  /** Operational with reduced capability or elevated error rate. */
  degraded: "degraded",

  /** Not accepting invocations. */
  unavailable: "unavailable",
}

/**
 * Invocation metrics for a registered component.
 *
 * Uses an exponential moving average for avg_duration_ms so that
 * recent latency is weighted more heavily than historical.
 */
@jsonSchema
model InvocationStats {
  /** Total number of invocations (success + failure). */
  total_calls: int32;

  /** Number of successful invocations. */
  successes: int32;

  /** Number of failed invocations. */
  failures: int32;

  /** Unix timestamp of the most recent invocation, or null. */
  last_invoked_at?: float64 | null;

  /** Exponential moving average of invocation duration in milliseconds. */
  avg_duration_ms: float64;
}

/**
 * A registered component in the catalog.
 */
@jsonSchema
model RegistryEntry {
  /** Unique identifier for the component. */
  name: string;

  /** Component type (agent, tool, sense, plugin). */
  kind: ComponentKind;

  /** What it does — used by the router for matching. */
  description: string;

  /** Input/output JSON schema (primarily for tools), or null. */
  schema?: Record<unknown> | null;

  /** Custom key-value fields (role, origin, version, etc.). */
  metadata?: Record<string>;

  /** Current operational health status. */
  health?: HealthStatus;

  /** Invocation metrics tracked over the component's lifetime. */
  stats?: InvocationStats;

  /** Whether the component is active. */
  enabled?: boolean;

  /** Dependencies — credential names, other component names. */
  requirements?: string[];

  /** Role names required to access this component. */
  permissions?: string[];
}

/**
 * Partial update for a registry entry.
 *
 * Only non-null fields are applied when passed to Registry.update().
 */
@jsonSchema
model RegistryPatch {
  /** New description, or null to leave unchanged. */
  description?: string | null;

  /** New metadata dict, or null to leave unchanged. */
  metadata?: Record<string> | null;

  /** New health status, or null to leave unchanged. */
  health?: HealthStatus | null;

  /** New enabled flag, or null to leave unchanged. */
  enabled?: boolean | null;

  /** New requirements list, or null to leave unchanged. */
  requirements?: string[] | null;

  /** New permissions list, or null to leave unchanged. */
  permissions?: string[] | null;
}

/**
 * Registry interface — unified catalog of agents, tools, senses, and plugins.
 *
 * Implementations may be backed by an in-memory dict, a database, or
 * a remote service.
 */
@jsonSchema
model RegistryContract {
  /** Add or replace a component in the registry. */
  register: {
    input: {
      /** Component definition to register. */
      entry: RegistryEntry;

      /** Execution context. */
      ctx: ExecContext;
    };
  };

  /** List components of a given kind visible to the caller. */
  discover: {
    input: {
      /** Component type to filter by. */
      kind: ComponentKind;

      /** Execution context for permission checks. */
      ctx: ExecContext;
    };
    output: RegistryEntry[];
  };

  /** Look up a single component by name. */
  resolve: {
    input: {
      /** Unique component identifier. */
      name: string;

      /** Execution context. */
      ctx: ExecContext;
    };
    output: RegistryEntry | null;
  };

  /** Get the current health status of a component. */
  health: {
    input: {
      /** Unique component identifier. */
      name: string;
    };
    output: HealthStatus;
  };

  /** Apply a partial update to a registered component. */
  update: {
    input: {
      /** Unique component identifier to update. */
      name: string;

      /** Fields to overwrite. */
      patch: RegistryPatch;
    };
  };
}
