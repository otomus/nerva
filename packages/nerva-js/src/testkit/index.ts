/**
 * Nerva testkit — spy wrappers, builders, assertions, and factories for testing.
 *
 * The testkit provides reusable test infrastructure for code built on Nerva
 * primitives. Instead of hand-rolling mocks, import spy-wrapped real
 * implementations that record every call and support expectation-setting.
 *
 * @example
 * ```ts
 * import { createTestOrchestrator, assertRoutedTo } from "@otomus/nerva/testkit";
 *
 * const result = createTestOrchestrator();
 * result.runtime.expectLlmResponse("Hello!");
 * const response = await result.orchestrator.handle("hi");
 * assertRoutedTo(result.router, "default");
 * ```
 *
 * @module testkit
 */

// -- Spies ----------------------------------------------------------------
export {
  type ClassifyCall,
  type InvokeCall,
  type DelegateCall,
  type FormatCall,
  type RecallCall,
  type StoreCall,
  type EvaluateCall,
  type PolicyRecordCall,
  type DiscoverToolsCall,
  type ToolCallRecord,
  SpyRouter,
  SpyRuntime,
  SpyResponder,
  SpyMemory,
  SpyPolicy,
  SpyToolManager,
} from "./spies.js";

// -- Builders -------------------------------------------------------------
export {
  type TestOrchestratorResult,
  type TestOrchestratorOptions,
  createTestOrchestrator,
} from "./builders.js";

// -- Assertions -----------------------------------------------------------
export {
  assertRoutedTo,
  assertHandlerInvoked,
  assertPolicyAllowed,
  assertPolicyDenied,
  assertMemoryStored,
  assertMemoryRecalled,
  assertToolCalled,
  assertNoUnconsumedExpectations,
  assertPipelineOrder,
} from "./assertions.js";

// -- Factories ------------------------------------------------------------
export {
  type CreateTestCtxOptions,
  createTestCtx,
  createSpyRouter,
  createSpyRuntime,
  createSpyResponder,
  createSpyMemory,
  createSpyPolicy,
  createSpyToolManager,
} from "./factories.js";

// -- Boundaries -----------------------------------------------------------
export {
  StubLLMHandler,
  DenyAllPolicy,
  AllowAllPolicy,
} from "./boundaries.js";

// -- MCP ------------------------------------------------------------------
export { MCPTestHarness } from "./mcp.js";
