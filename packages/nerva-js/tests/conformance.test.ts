/**
 * Conformance tests — validate Node.js model outputs against generated JSON schemas.
 *
 * Loads YAML schemas from spec/generated/, constructs model instances using the
 * TypeScript implementation, serializes them to plain objects, and validates
 * against the corresponding JSON schema using Ajv.
 *
 * Covers both positive tests (valid instances) and negative tests (invalid
 * enum values, missing required fields, out-of-range scores).
 */

import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import Ajv from "ajv";
import addFormats from "ajv-formats";
import * as yaml from "js-yaml";
import { describe, expect, it } from "vitest";

import {
  ExecContext,
  TokenUsage,
  createPermissions,
  type Scope,
  type Span,
  type Event,
} from "../src/context.js";
import {
  createHandlerCandidate,
  createIntentResult,
} from "../src/router/index.js";
import {
  AgentStatus,
  createAgentInput,
  createAgentResult,
} from "../src/runtime/index.js";
import {
  ToolStatus,
  createToolSpec,
  createToolResult,
} from "../src/tools/index.js";
import {
  MemoryTier,
  createMemoryEvent,
  createEmptyMemoryContext,
  type MemoryContext,
} from "../src/memory/index.js";
import {
  createChannel,
  createResponse,
} from "../src/responder/index.js";
import {
  ComponentKind,
  HealthStatus,
  InvocationStats,
  createRegistryEntry,
} from "../src/registry/index.js";
import {
  createPolicyAction,
  createPolicyDecision,
} from "../src/policy/index.js";

// ---------------------------------------------------------------------------
// Schema loading and Ajv setup
// ---------------------------------------------------------------------------

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const GENERATED_DIR = resolve(__dirname, "../../..", "spec", "generated");

/** Load all YAML schemas from the generated directory and register with Ajv. */
function createValidator(): Ajv {
  const ajv = new Ajv({ allErrors: true, strict: false });
  addFormats(ajv);

  const schemaFiles = readdirSync(GENERATED_DIR).filter((f) =>
    f.endsWith(".yaml"),
  );

  // First pass: load all schemas and register them by $id
  const schemas: Record<string, Record<string, unknown>> = {};
  for (const file of schemaFiles) {
    const content = readFileSync(join(GENERATED_DIR, file), "utf-8");
    const schema = yaml.load(content) as Record<string, unknown>;
    schemas[file] = schema;
  }

  // Register each schema with Ajv, using $id as the key
  for (const [_file, schema] of Object.entries(schemas)) {
    const id = (schema["$id"] as string) ?? _file;
    try {
      ajv.addSchema(schema, id);
    } catch {
      // Schema already added — skip
    }
  }

  return ajv;
}

const ajv = createValidator();

/**
 * Assert that an instance conforms to the named schema.
 *
 * @param schemaName - Schema $id (e.g. "ExecContext.yaml").
 * @param instance - Value to validate.
 */
function assertValid(schemaName: string, instance: unknown): void {
  const validate = ajv.getSchema(schemaName);
  if (!validate) {
    throw new Error(`Schema not found: ${schemaName}`);
  }
  const valid = validate(instance);
  if (!valid) {
    throw new Error(
      `Schema ${schemaName} rejected valid instance:\n${JSON.stringify(validate.errors, null, 2)}\nInstance: ${JSON.stringify(instance, null, 2)}`,
    );
  }
}

/**
 * Assert that an instance does NOT conform to the named schema.
 *
 * @param schemaName - Schema $id (e.g. "ExecContext.yaml").
 * @param instance - Value to validate.
 */
function assertInvalid(schemaName: string, instance: unknown): void {
  const validate = ajv.getSchema(schemaName);
  if (!validate) {
    throw new Error(`Schema not found: ${schemaName}`);
  }
  const valid = validate(instance);
  if (valid) {
    throw new Error(
      `Schema ${schemaName} accepted invalid instance: ${JSON.stringify(instance)}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Serialization helpers
// ---------------------------------------------------------------------------

/** Serialize ExecContext to a schema-compatible plain object. */
function serializeExecContext(ctx: ExecContext): Record<string, unknown> {
  return {
    request_id: ctx.requestId,
    trace_id: ctx.traceId,
    user_id: ctx.userId,
    session_id: ctx.sessionId,
    permissions: {
      roles: [...ctx.permissions.roles],
      allowed_tools: ctx.permissions.allowedTools
        ? [...ctx.permissions.allowedTools]
        : null,
      allowed_agents: ctx.permissions.allowedAgents
        ? [...ctx.permissions.allowedAgents]
        : null,
    },
    memory_scope: ctx.memoryScope,
    spans: ctx.spans.map((s) => ({
      span_id: s.spanId,
      name: s.name,
      parent_id: s.parentId,
      started_at: s.startedAt,
      ended_at: s.endedAt,
      attributes: { ...s.attributes },
    })),
    events: ctx.events.map((e) => ({
      timestamp: e.timestamp,
      name: e.name,
      attributes: { ...e.attributes },
    })),
    token_usage: {
      prompt_tokens: ctx.tokenUsage.promptTokens,
      completion_tokens: ctx.tokenUsage.completionTokens,
      total_tokens: ctx.tokenUsage.totalTokens,
      cost_usd: ctx.tokenUsage.costUsd,
    },
    created_at: ctx.createdAt,
    timeout_at: ctx.timeoutAt,
    cancelled: ctx.isCancelled(),
    metadata: { ...ctx.metadata },
  };
}

// ===========================================================================
// Positive conformance tests — valid instances
// ===========================================================================

describe("ExecContext conformance", () => {
  it("minimal context validates", () => {
    const ctx = ExecContext.create();
    assertValid("ExecContext.yaml", serializeExecContext(ctx));
  });

  it("full context validates", () => {
    const ctx = ExecContext.create({
      userId: "u-123",
      sessionId: "s-456",
      permissions: createPermissions({
        roles: new Set(["admin", "user"]),
        allowedTools: new Set(["search"]),
        allowedAgents: new Set(["helper"]),
      }),
      memoryScope: "global",
      timeoutSeconds: 30,
    });
    ctx.metadata["env"] = "test";
    ctx.addSpan("test.span");
    ctx.addEvent("test.event", { detail: "value" });
    ctx.recordTokens(new TokenUsage(10, 5, 15, 0.001));
    assertValid("ExecContext.yaml", serializeExecContext(ctx));
  });

  it("anonymous context (null user/session) validates", () => {
    const ctx = ExecContext.create({ userId: null, sessionId: null });
    const serialized = serializeExecContext(ctx);
    expect(serialized["user_id"]).toBeNull();
    expect(serialized["session_id"]).toBeNull();
    assertValid("ExecContext.yaml", serialized);
  });
});

describe("IntentResult conformance", () => {
  it("intent with handlers validates", () => {
    const handlers = [
      createHandlerCandidate("search", 0.95, "keyword match"),
      createHandlerCandidate("fallback", 0.1),
    ];
    const result = createIntentResult("search_web", 0.9, handlers, {
      search: 0.95,
      fallback: 0.1,
    });
    assertValid("IntentResult.yaml", {
      intent: result.intent,
      confidence: result.confidence,
      handlers: result.handlers.map((h) => ({
        name: h.name,
        score: h.score,
        ...(h.reason ? { reason: h.reason } : {}),
      })),
      raw_scores: { ...result.rawScores },
    });
  });

  it("intent with empty handlers validates", () => {
    const result = createIntentResult("unknown", 0.0, []);
    assertValid("IntentResult.yaml", {
      intent: result.intent,
      confidence: result.confidence,
      handlers: [],
    });
  });

  it("handler candidate at boundary scores validates", () => {
    for (const score of [0.0, 1.0]) {
      const h = createHandlerCandidate("test", score);
      assertValid("HandlerCandidate.yaml", {
        name: h.name,
        score: h.score,
      });
    }
  });
});

describe("AgentInput conformance", () => {
  it("minimal input validates", () => {
    const inp = createAgentInput("hello");
    assertValid("AgentInput.yaml", { message: inp.message });
  });

  it("full input validates", () => {
    const inp = createAgentInput("book a flight", {
      args: { destination: "NYC" },
      tools: [{ name: "calendar", description: "manage events" }],
      history: [{ role: "user", content: "hi" }],
    });
    assertValid("AgentInput.yaml", {
      message: inp.message,
      args: { ...inp.args },
      tools: inp.tools.map((t) => ({ ...t })),
      history: inp.history.map((h) => ({ ...h })),
    });
  });
});

describe("AgentResult conformance", () => {
  it("success result validates", () => {
    const result = createAgentResult(AgentStatus.SUCCESS, {
      output: "Done",
      handler: "search",
    });
    assertValid("AgentResult.yaml", {
      status: result.status,
      output: result.output,
      handler: result.handler,
      error: result.error,
      data: { ...result.data },
    });
  });

  it("error result validates", () => {
    const result = createAgentResult(AgentStatus.ERROR, {
      error: "timeout exceeded",
      handler: "slow_handler",
    });
    assertValid("AgentResult.yaml", {
      status: result.status,
      error: result.error,
      handler: result.handler,
      output: result.output,
      data: { ...result.data },
    });
  });

  it("all status values validate", () => {
    for (const status of Object.values(AgentStatus)) {
      assertValid("AgentStatus.yaml", status);
    }
  });
});

describe("ToolSpec conformance", () => {
  it("minimal spec validates", () => {
    const spec = createToolSpec("search", "Search the web");
    assertValid("ToolSpec.yaml", {
      name: spec.name,
      description: spec.description,
    });
  });

  it("full spec validates", () => {
    const spec = createToolSpec("calendar", "Manage calendar", {
      parameters: { type: "object" },
      requiredPermissions: new Set(["admin"]),
    });
    assertValid("ToolSpec.yaml", {
      name: spec.name,
      description: spec.description,
      parameters: { ...spec.parameters },
      required_permissions: [...spec.requiredPermissions],
    });
  });
});

describe("ToolResult conformance", () => {
  it("success result validates", () => {
    const result = createToolResult(ToolStatus.SUCCESS, {
      output: "found 3 results",
      durationMs: 120.5,
    });
    assertValid("ToolResult.yaml", {
      status: result.status,
      output: result.output,
      error: result.error,
      duration_ms: result.durationMs,
    });
  });

  it("error result validates", () => {
    const result = createToolResult(ToolStatus.PERMISSION_DENIED, {
      error: "not authorized",
      durationMs: 5.0,
    });
    assertValid("ToolResult.yaml", {
      status: result.status,
      error: result.error,
      duration_ms: result.durationMs,
      output: result.output,
    });
  });

  it("all status values validate", () => {
    for (const status of Object.values(ToolStatus)) {
      assertValid("ToolStatus.yaml", status);
    }
  });
});

describe("MemoryEvent conformance", () => {
  it("minimal event validates", () => {
    const event = createMemoryEvent("user said hello");
    assertValid("MemoryEvent.yaml", {
      content: event.content,
      tier: event.tier,
    });
  });

  it("full event validates", () => {
    const event = createMemoryEvent("important fact", {
      tier: MemoryTier.COLD,
      scope: "user" as Scope,
      tags: new Set(["important", "fact"]),
      source: "agent-x",
    });
    assertValid("MemoryEvent.yaml", {
      content: event.content,
      tier: event.tier,
      scope: event.scope,
      tags: [...event.tags],
      source: event.source,
    });
  });

  it("null scope validates", () => {
    const event = createMemoryEvent("test", {
      tier: MemoryTier.WARM,
      scope: null,
    });
    const serialized = {
      content: event.content,
      tier: event.tier,
      scope: event.scope,
    };
    expect(serialized.scope).toBeNull();
    assertValid("MemoryEvent.yaml", serialized);
  });

  it("all tier values validate", () => {
    for (const tier of Object.values(MemoryTier)) {
      assertValid("MemoryTier.yaml", tier);
    }
  });
});

describe("MemoryContext conformance", () => {
  it("empty context validates", () => {
    const ctx = createEmptyMemoryContext();
    assertValid("MemoryContext.yaml", {
      conversation: [...ctx.conversation],
      episodes: [...ctx.episodes],
      facts: [...ctx.facts],
      knowledge: [...ctx.knowledge],
      token_count: ctx.tokenCount,
    });
  });

  it("full context validates", () => {
    const ctx: MemoryContext = {
      conversation: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ],
      episodes: ["episode1", "episode2"],
      facts: ["fact1"],
      knowledge: ["knowledge1"],
      tokenCount: 150,
    };
    assertValid("MemoryContext.yaml", {
      conversation: ctx.conversation.map((c) => ({ ...c })),
      episodes: [...ctx.episodes],
      facts: [...ctx.facts],
      knowledge: [...ctx.knowledge],
      token_count: ctx.tokenCount,
    });
  });
});

describe("Channel conformance", () => {
  it("minimal channel validates", () => {
    const ch = createChannel("api");
    assertValid("Channel.yaml", { name: ch.name });
  });

  it("full channel validates", () => {
    const ch = createChannel("slack", {
      supportsMarkdown: true,
      supportsMedia: false,
      maxLength: 4000,
    });
    assertValid("Channel.yaml", {
      name: ch.name,
      supports_markdown: ch.supportsMarkdown,
      supports_media: ch.supportsMedia,
      max_length: ch.maxLength,
    });
  });
});

describe("Response conformance", () => {
  it("minimal response validates", () => {
    const resp = createResponse("hello", createChannel("api"));
    assertValid("Response.yaml", {
      text: resp.text,
      channel: { name: resp.channel.name },
    });
  });

  it("full response validates", () => {
    const ch = createChannel("websocket", {
      supportsMarkdown: true,
      supportsMedia: true,
    });
    const resp = createResponse("Here is the image", ch, {
      media: ["https://example.com/image.png"],
      metadata: { format: "png" },
    });
    assertValid("Response.yaml", {
      text: resp.text,
      channel: {
        name: resp.channel.name,
        supports_markdown: resp.channel.supportsMarkdown,
        supports_media: resp.channel.supportsMedia,
        max_length: resp.channel.maxLength,
      },
      media: [...resp.media],
      metadata: { ...resp.metadata },
    });
  });
});

describe("RegistryEntry conformance", () => {
  it("minimal entry validates", () => {
    const entry = createRegistryEntry(
      "search-agent",
      ComponentKind.AGENT,
      "Search the web",
    );
    assertValid("RegistryEntry.yaml", {
      name: entry.name,
      kind: entry.kind,
      description: entry.description,
    });
  });

  it("full entry validates", () => {
    const stats = new InvocationStats();
    stats.recordSuccess(42);
    const entry = createRegistryEntry(
      "calendar-tool",
      ComponentKind.TOOL,
      "Manage calendar events",
      {
        schema: { type: "object" },
        metadata: { version: "1.0" },
        health: HealthStatus.HEALTHY,
        stats,
        enabled: true,
        requirements: ["google-creds"],
        permissions: ["admin"],
      },
    );
    assertValid("RegistryEntry.yaml", {
      name: entry.name,
      kind: entry.kind,
      description: entry.description,
      schema: entry.schema,
      metadata: { ...entry.metadata },
      health: entry.health,
      stats: {
        total_calls: entry.stats.totalCalls,
        successes: entry.stats.successes,
        failures: entry.stats.failures,
        last_invoked_at: entry.stats.lastInvokedAt,
        avg_duration_ms: entry.stats.avgDurationMs,
      },
      enabled: entry.enabled,
      requirements: [...entry.requirements],
      permissions: [...entry.permissions],
    });
  });

  it("all component kinds validate", () => {
    for (const kind of Object.values(ComponentKind)) {
      assertValid("ComponentKind.yaml", kind);
    }
  });

  it("all health statuses validate", () => {
    for (const status of Object.values(HealthStatus)) {
      assertValid("HealthStatus.yaml", status);
    }
  });
});

describe("PolicyAction conformance", () => {
  it("minimal action validates", () => {
    const action = createPolicyAction("invoke_agent", "user-1", "search-agent");
    assertValid("PolicyAction.yaml", {
      kind: action.kind,
      subject: action.subject,
      target: action.target,
      metadata: { ...action.metadata },
    });
  });

  it("action with metadata validates", () => {
    const action = createPolicyAction(
      "call_tool",
      "agent-x",
      "web-search",
      { cost_estimate: "0.01" },
    );
    assertValid("PolicyAction.yaml", {
      kind: action.kind,
      subject: action.subject,
      target: action.target,
      metadata: { ...action.metadata },
    });
  });
});

describe("PolicyDecision conformance", () => {
  it("allow decision validates", () => {
    const d = createPolicyDecision({ allowed: true });
    assertValid("PolicyDecision.yaml", {
      allowed: d.allowed,
      reason: d.reason,
      require_approval: d.requireApproval,
      approvers: d.approvers,
      budget_remaining: d.budgetRemaining,
    });
  });

  it("deny with reason validates", () => {
    const d = createPolicyDecision({
      allowed: false,
      reason: "rate limit exceeded",
    });
    assertValid("PolicyDecision.yaml", {
      allowed: d.allowed,
      reason: d.reason,
      require_approval: d.requireApproval,
      approvers: d.approvers,
      budget_remaining: d.budgetRemaining,
    });
  });

  it("full decision validates", () => {
    const d = createPolicyDecision({
      allowed: false,
      reason: "budget exceeded",
      requireApproval: true,
      approvers: ["admin@co.com"],
      budgetRemaining: 42.5,
    });
    assertValid("PolicyDecision.yaml", {
      allowed: d.allowed,
      reason: d.reason,
      require_approval: d.requireApproval,
      approvers: d.approvers ? [...d.approvers] : null,
      budget_remaining: d.budgetRemaining,
    });
  });
});

// ===========================================================================
// Negative conformance tests — invalid instances
// ===========================================================================

describe("negative: invalid enum values", () => {
  it("unknown scope is rejected", () => {
    assertInvalid("Scope.yaml", "unknown");
  });

  it("empty scope is rejected", () => {
    assertInvalid("Scope.yaml", "");
  });

  it("numeric scope is rejected", () => {
    assertInvalid("Scope.yaml", 42);
  });

  it("null scope is rejected", () => {
    assertInvalid("Scope.yaml", null);
  });

  it("invalid agent status is rejected", () => {
    assertInvalid("AgentStatus.yaml", "failed");
  });

  it("invalid tool status is rejected", () => {
    assertInvalid("ToolStatus.yaml", "denied");
  });

  it("invalid memory tier is rejected", () => {
    assertInvalid("MemoryTier.yaml", "archive");
  });

  it("invalid component kind is rejected", () => {
    assertInvalid("ComponentKind.yaml", "service");
  });

  it("invalid health status is rejected", () => {
    assertInvalid("HealthStatus.yaml", "down");
  });
});

describe("negative: missing required fields", () => {
  it("ExecContext without request_id is rejected", () => {
    const ctx = ExecContext.create();
    const serialized = serializeExecContext(ctx);
    delete (serialized as Record<string, unknown>)["request_id"];
    assertInvalid("ExecContext.yaml", serialized);
  });

  it("HandlerCandidate without name is rejected", () => {
    assertInvalid("HandlerCandidate.yaml", { score: 0.5 });
  });

  it("HandlerCandidate without score is rejected", () => {
    assertInvalid("HandlerCandidate.yaml", { name: "test" });
  });

  it("IntentResult without intent is rejected", () => {
    assertInvalid("IntentResult.yaml", { confidence: 0.5, handlers: [] });
  });

  it("IntentResult without confidence is rejected", () => {
    assertInvalid("IntentResult.yaml", { intent: "test", handlers: [] });
  });

  it("IntentResult without handlers is rejected", () => {
    assertInvalid("IntentResult.yaml", { intent: "test", confidence: 0.5 });
  });

  it("AgentInput without message is rejected", () => {
    assertInvalid("AgentInput.yaml", { args: {} });
  });

  it("AgentResult without status is rejected", () => {
    assertInvalid("AgentResult.yaml", { output: "hello" });
  });

  it("ToolSpec without name is rejected", () => {
    assertInvalid("ToolSpec.yaml", { description: "test" });
  });

  it("ToolSpec without description is rejected", () => {
    assertInvalid("ToolSpec.yaml", { name: "test" });
  });

  it("ToolResult without status is rejected", () => {
    assertInvalid("ToolResult.yaml", { output: "hello" });
  });

  it("MemoryEvent without content is rejected", () => {
    assertInvalid("MemoryEvent.yaml", { tier: "hot" });
  });

  it("MemoryEvent without tier is rejected", () => {
    assertInvalid("MemoryEvent.yaml", { content: "test" });
  });

  it("Channel without name is rejected", () => {
    assertInvalid("Channel.yaml", { supports_markdown: true });
  });

  it("Response without text is rejected", () => {
    assertInvalid("Response.yaml", { channel: { name: "api" } });
  });

  it("Response without channel is rejected", () => {
    assertInvalid("Response.yaml", { text: "hello" });
  });

  it("RegistryEntry without name is rejected", () => {
    assertInvalid("RegistryEntry.yaml", {
      kind: "agent",
      description: "test",
    });
  });

  it("RegistryEntry without kind is rejected", () => {
    assertInvalid("RegistryEntry.yaml", {
      name: "test",
      description: "test",
    });
  });

  it("RegistryEntry without description is rejected", () => {
    assertInvalid("RegistryEntry.yaml", { name: "test", kind: "agent" });
  });

  it("PolicyAction without kind is rejected", () => {
    assertInvalid("PolicyAction.yaml", { subject: "u1", target: "t1" });
  });

  it("PolicyAction without subject is rejected", () => {
    assertInvalid("PolicyAction.yaml", { kind: "invoke_agent", target: "t1" });
  });

  it("PolicyAction without target is rejected", () => {
    assertInvalid("PolicyAction.yaml", {
      kind: "invoke_agent",
      subject: "u1",
    });
  });

  it("PolicyDecision without allowed is rejected", () => {
    assertInvalid("PolicyDecision.yaml", { reason: "test" });
  });

  it("Permissions without roles is rejected", () => {
    assertInvalid("Permissions.yaml", {});
  });

  it("TokenUsage missing required fields is rejected", () => {
    assertInvalid("TokenUsage.yaml", { prompt_tokens: 0 });
  });

  it("empty object is rejected for all object schemas with required fields", () => {
    const schemas = [
      "ExecContext.yaml",
      "HandlerCandidate.yaml",
      "IntentResult.yaml",
      "AgentInput.yaml",
      "AgentResult.yaml",
      "ToolSpec.yaml",
      "ToolResult.yaml",
      "MemoryEvent.yaml",
      "Channel.yaml",
      "Response.yaml",
      "RegistryEntry.yaml",
      "PolicyAction.yaml",
      "PolicyDecision.yaml",
      "Permissions.yaml",
      "TokenUsage.yaml",
    ];
    for (const schemaName of schemas) {
      assertInvalid(schemaName, {});
    }
  });
});

describe("negative: out-of-range scores", () => {
  it("handler score above 1 is rejected", () => {
    assertInvalid("HandlerCandidate.yaml", { name: "test", score: 1.5 });
  });

  it("handler score below 0 is rejected", () => {
    assertInvalid("HandlerCandidate.yaml", { name: "test", score: -0.1 });
  });

  it("intent confidence above 1 is rejected", () => {
    assertInvalid("IntentResult.yaml", {
      intent: "test",
      confidence: 1.5,
      handlers: [],
    });
  });

  it("intent confidence below 0 is rejected", () => {
    assertInvalid("IntentResult.yaml", {
      intent: "test",
      confidence: -0.1,
      handlers: [],
    });
  });
});

describe("negative: wrong types", () => {
  it("Permissions roles as string is rejected", () => {
    assertInvalid("Permissions.yaml", { roles: "admin" });
  });

  it("Permissions roles with non-string items is rejected", () => {
    assertInvalid("Permissions.yaml", { roles: [42] });
  });

  it("TokenUsage with string prompt_tokens is rejected", () => {
    assertInvalid("TokenUsage.yaml", {
      prompt_tokens: "ten",
      completion_tokens: 0,
      total_tokens: 0,
      cost_usd: 0.0,
    });
  });

  it("PolicyDecision with allowed as string is rejected", () => {
    assertInvalid("PolicyDecision.yaml", { allowed: "yes" });
  });

  it("HandlerCandidate with string score is rejected", () => {
    assertInvalid("HandlerCandidate.yaml", { name: "test", score: "high" });
  });

  it("Channel with numeric name is rejected", () => {
    assertInvalid("Channel.yaml", { name: 42 });
  });

  it("AgentInput with numeric message is rejected", () => {
    assertInvalid("AgentInput.yaml", { message: 42 });
  });
});
