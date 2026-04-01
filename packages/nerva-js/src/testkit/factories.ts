/**
 * Factory functions for creating test contexts and individual spies.
 *
 * @module testkit/factories
 */

import { ExecContext } from "../context.js";
import type { IntentRouter } from "../router/index.js";
import type { AgentRuntime } from "../runtime/index.js";
import type { Responder } from "../responder/index.js";
import type { Memory } from "../memory/index.js";
import type { PolicyEngine } from "../policy/index.js";
import type { ToolManager } from "../tools/index.js";

import { RuleRouter } from "../router/rule.js";
import { InProcessRuntime } from "../runtime/inprocess.js";
import { PassthroughResponder } from "../responder/passthrough.js";
import { TieredMemory } from "../memory/tiered.js";
import { InMemoryHotMemory } from "../memory/hot.js";
import { NoopPolicyEngine } from "../policy/noop.js";
import { FunctionToolManager } from "../tools/function.js";

import {
  SpyRouter,
  SpyRuntime,
  SpyResponder,
  SpyMemory,
  SpyPolicy,
  SpyToolManager,
} from "./spies.js";

/**
 * Options for creating a test ExecContext.
 */
export interface CreateTestCtxOptions {
  /** User identifier. Defaults to "test-user". */
  userId?: string;
  /** Session identifier. Defaults to "test-session". */
  sessionId?: string;
  /** Timeout in seconds. */
  timeoutSeconds?: number;
}

/**
 * Create a test ExecContext with sensible defaults.
 *
 * @param options - Optional overrides for user and session IDs.
 * @returns A fresh ExecContext.
 */
export function createTestCtx(options: CreateTestCtxOptions = {}): ExecContext {
  return ExecContext.create({
    userId: options.userId ?? "test-user",
    sessionId: options.sessionId ?? "test-session",
    timeoutSeconds: options.timeoutSeconds ?? null,
  });
}

/**
 * Create a SpyRouter wrapping a default catch-all RuleRouter.
 *
 * @param inner - Optional custom IntentRouter to wrap.
 * @returns A SpyRouter instance.
 */
export function createSpyRouter(inner?: IntentRouter): SpyRouter {
  const wrapped =
    inner ??
    new RuleRouter(
      [{ pattern: ".*", handler: "default", intent: "general" }],
      null,
    );
  return new SpyRouter(wrapped);
}

/**
 * Create a SpyRuntime wrapping a default InProcessRuntime.
 *
 * @param inner - Optional custom AgentRuntime to wrap.
 * @returns A SpyRuntime instance.
 */
export function createSpyRuntime(inner?: AgentRuntime): SpyRuntime {
  return new SpyRuntime(inner ?? new InProcessRuntime());
}

/**
 * Create a SpyResponder wrapping a default PassthroughResponder.
 *
 * @param inner - Optional custom Responder to wrap.
 * @returns A SpyResponder instance.
 */
export function createSpyResponder(inner?: Responder): SpyResponder {
  return new SpyResponder(inner ?? new PassthroughResponder());
}

/**
 * Create a SpyMemory wrapping a default TieredMemory with InMemoryHotMemory.
 *
 * @param inner - Optional custom Memory to wrap.
 * @returns A SpyMemory instance.
 */
export function createSpyMemory(inner?: Memory): SpyMemory {
  return new SpyMemory(
    inner ?? new TieredMemory({ hot: new InMemoryHotMemory() }),
  );
}

/**
 * Create a SpyPolicy wrapping a default NoopPolicyEngine.
 *
 * @param inner - Optional custom PolicyEngine to wrap.
 * @returns A SpyPolicy instance.
 */
export function createSpyPolicy(inner?: PolicyEngine): SpyPolicy {
  return new SpyPolicy(inner ?? new NoopPolicyEngine());
}

/**
 * Create a SpyToolManager wrapping a default FunctionToolManager.
 *
 * @param inner - Optional custom ToolManager to wrap.
 * @returns A SpyToolManager instance.
 */
export function createSpyToolManager(inner?: ToolManager): SpyToolManager {
  return new SpyToolManager(inner ?? new FunctionToolManager());
}
