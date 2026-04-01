/**
 * TestOrchestrator builder — wires real defaults with spy wrappers.
 *
 * The builder creates an `Orchestrator` with spy-wrapped real implementations
 * as defaults, letting users override any primitive while keeping the rest wired
 * to lightweight, in-memory implementations.
 *
 * @module testkit/builders
 */

import {
  Orchestrator,
  type AgentRuntime as OrchestratorRuntime,
  type Responder as OrchestratorResponder,
  type Memory as OrchestratorMemory,
  type PolicyEngine as OrchestratorPolicyEngine,
  type ToolManager as OrchestratorToolManager,
} from "../orchestrator.js";
import { RuleRouter } from "../router/rule.js";
import { InProcessRuntime } from "../runtime/inprocess.js";
import type { HandlerFn } from "../runtime/inprocess.js";
import { PassthroughResponder } from "../responder/passthrough.js";
import { TieredMemory } from "../memory/tiered.js";
import { InMemoryHotMemory } from "../memory/hot.js";
import { NoopPolicyEngine } from "../policy/noop.js";
import { FunctionToolManager } from "../tools/function.js";
import type { IntentRouter } from "../router/index.js";
import type { AgentRuntime } from "../runtime/index.js";
import type { Responder } from "../responder/index.js";
import type { Memory } from "../memory/index.js";
import type { PolicyEngine } from "../policy/index.js";
import type { ToolManager } from "../tools/index.js";

import {
  SpyRouter,
  SpyRuntime,
  SpyResponder,
  SpyMemory,
  SpyPolicy,
  SpyToolManager,
} from "./spies.js";

// ---------------------------------------------------------------------------
// Result container
// ---------------------------------------------------------------------------

/**
 * Container holding the orchestrator and all spy references.
 */
export interface TestOrchestratorResult {
  /** The wired Orchestrator instance. */
  readonly orchestrator: Orchestrator;
  /** SpyRouter wrapping the real (or provided) router. */
  readonly router: SpyRouter;
  /** SpyRuntime wrapping the real (or provided) runtime. */
  readonly runtime: SpyRuntime;
  /** SpyResponder wrapping the real (or provided) responder. */
  readonly responder: SpyResponder;
  /** SpyMemory wrapping the real (or provided) memory. */
  readonly memory: SpyMemory;
  /** SpyPolicy wrapping the real (or provided) policy engine. */
  readonly policy: SpyPolicy;
  /** SpyToolManager wrapping the real (or provided) tool manager. */
  readonly tools: SpyToolManager;

  /** Reset all spies — clears call history and pending expectations. */
  resetAll(): void;

  /** Assert that no spy has unconsumed expectations. */
  verifyAllExpectationsConsumed(): void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATCH_ALL_PATTERN = ".*";
const CATCH_ALL_HANDLER = "default";
const CATCH_ALL_INTENT = "general";

// ---------------------------------------------------------------------------
// Build options
// ---------------------------------------------------------------------------

/**
 * Options for creating a test orchestrator.
 */
export interface TestOrchestratorOptions {
  /** IntentRouter or SpyRouter override. */
  router?: IntentRouter;
  /** AgentRuntime or SpyRuntime override. */
  runtime?: AgentRuntime;
  /** Responder or SpyResponder override. */
  responder?: Responder;
  /** Memory or SpyMemory override. */
  memory?: Memory;
  /** PolicyEngine or SpyPolicy override. */
  policy?: PolicyEngine;
  /** ToolManager or SpyToolManager override. */
  tools?: ToolManager;
  /** Handler functions to register in the default InProcessRuntime. */
  handlers?: Record<string, HandlerFn>;
}

// ---------------------------------------------------------------------------
// Builder
// ---------------------------------------------------------------------------

/**
 * Create a fully-wired test orchestrator with spy-wrapped real defaults.
 *
 * All primitives default to spy-wrapped real implementations. Provide
 * overrides for any primitive — if the override is already a spy, it
 * is used directly; otherwise it gets wrapped in one.
 *
 * @param options - Optional overrides for each primitive.
 * @returns TestOrchestratorResult with orchestrator and spy references.
 */
export function createTestOrchestrator(
  options: TestOrchestratorOptions = {},
): TestOrchestratorResult {
  const spyRouter = ensureSpyRouter(options.router);
  const spyRuntime = ensureSpyRuntime(options.runtime, options.handlers);
  const spyResponder = ensureSpyResponder(options.responder);
  const spyMemory = ensureSpyMemory(options.memory);
  const spyPolicy = ensureSpyPolicy(options.policy);
  const spyTools = ensureSpyTools(options.tools);

  // The Orchestrator's interfaces are simplified versions of the full
  // primitive interfaces. The spies are structurally compatible but need
  // type assertions because of minor differences (optional vs required fields).
  const orchestrator = new Orchestrator({
    router: spyRouter,
    runtime: spyRuntime as unknown as OrchestratorRuntime,
    responder: spyResponder as unknown as OrchestratorResponder,
    memory: spyMemory as unknown as OrchestratorMemory,
    policy: spyPolicy as unknown as OrchestratorPolicyEngine,
    tools: spyTools as unknown as OrchestratorToolManager,
  });

  return {
    orchestrator,
    router: spyRouter,
    runtime: spyRuntime,
    responder: spyResponder,
    memory: spyMemory,
    policy: spyPolicy,
    tools: spyTools,

    resetAll() {
      spyRouter.reset();
      spyRuntime.reset();
      spyResponder.reset();
      spyMemory.reset();
      spyPolicy.reset();
      spyTools.reset();
    },

    verifyAllExpectationsConsumed() {
      spyRouter.verifyExpectationsConsumed();
      spyRuntime.verifyExpectationsConsumed();
      spyResponder.verifyExpectationsConsumed();
      spyMemory.verifyExpectationsConsumed();
      spyPolicy.verifyExpectationsConsumed();
      spyTools.verifyExpectationsConsumed();
    },
  };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function ensureSpyRouter(provided?: IntentRouter): SpyRouter {
  if (provided instanceof SpyRouter) return provided;
  if (provided) return new SpyRouter(provided);

  const defaultRouter = new RuleRouter(
    [{ pattern: CATCH_ALL_PATTERN, handler: CATCH_ALL_HANDLER, intent: CATCH_ALL_INTENT }],
    null,
  );
  return new SpyRouter(defaultRouter);
}

function ensureSpyRuntime(
  provided?: AgentRuntime,
  handlers?: Record<string, HandlerFn>,
): SpyRuntime {
  if (provided instanceof SpyRuntime) return provided;
  if (provided) return new SpyRuntime(provided);

  const defaultRuntime = new InProcessRuntime();
  if (handlers) {
    for (const [name, fn] of Object.entries(handlers)) {
      defaultRuntime.register(name, fn);
    }
  }
  return new SpyRuntime(defaultRuntime);
}

function ensureSpyResponder(provided?: Responder): SpyResponder {
  if (provided instanceof SpyResponder) return provided;
  if (provided) return new SpyResponder(provided);
  return new SpyResponder(new PassthroughResponder());
}

function ensureSpyMemory(provided?: Memory): SpyMemory {
  if (provided instanceof SpyMemory) return provided;
  if (provided) return new SpyMemory(provided);
  return new SpyMemory(new TieredMemory({ hot: new InMemoryHotMemory() }));
}

function ensureSpyPolicy(provided?: PolicyEngine): SpyPolicy {
  if (provided instanceof SpyPolicy) return provided;
  if (provided) return new SpyPolicy(provided);
  return new SpyPolicy(new NoopPolicyEngine());
}

function ensureSpyTools(provided?: ToolManager): SpyToolManager {
  if (provided instanceof SpyToolManager) return provided;
  if (provided) return new SpyToolManager(provided);
  return new SpyToolManager(new FunctionToolManager());
}
