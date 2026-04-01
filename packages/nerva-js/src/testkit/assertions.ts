/**
 * Assertion helpers for Nerva testkit.
 *
 * Concise, readable assertions for the most common test scenarios.
 * Each function inspects spy call records and throws `Error` with a
 * descriptive message on failure.
 *
 * @module testkit/assertions
 */

import type { SpyRouter } from "./spies.js";
import type { SpyRuntime } from "./spies.js";
import type { SpyPolicy } from "./spies.js";
import type { SpyMemory } from "./spies.js";
import type { SpyToolManager } from "./spies.js";
import type { TestOrchestratorResult } from "./builders.js";

/**
 * Assert that the router's most recent classify() selected the given handler.
 *
 * @param spyRouter - The SpyRouter to inspect.
 * @param handlerName - Expected handler name.
 * @throws {Error} If no classify calls recorded or handler doesn't match.
 */
export function assertRoutedTo(
  spyRouter: SpyRouter,
  handlerName: string,
): void {
  if (spyRouter.classifyCalls.length === 0) {
    throw new Error("SpyRouter has no recorded classify calls");
  }
  const lastCall = spyRouter.classifyCalls[spyRouter.classifyCalls.length - 1]!;
  const actualName = lastCall.result.bestHandler?.name ?? null;
  if (actualName !== handlerName) {
    throw new Error(
      `Expected route to '${handlerName}', got '${actualName}'`,
    );
  }
}

/**
 * Assert that the runtime invoked a specific handler.
 *
 * @param spyRuntime - The SpyRuntime to inspect.
 * @param handlerName - Expected handler name.
 * @param options - Optional message filter.
 * @throws {Error} If no matching invoke call is found.
 */
export function assertHandlerInvoked(
  spyRuntime: SpyRuntime,
  handlerName: string,
  options?: { message?: string },
): void {
  const matching = spyRuntime.invokeCalls.filter(
    (c) => c.handler === handlerName,
  );
  if (matching.length === 0) {
    const invoked = spyRuntime.invokeCalls.map((c) => c.handler);
    throw new Error(
      `Handler '${handlerName}' was never invoked. Invoked: ${JSON.stringify(invoked)}`,
    );
  }
  if (options?.message !== undefined) {
    const messages = matching.map((c) => c.input.message);
    if (!messages.includes(options.message)) {
      throw new Error(
        `Handler '${handlerName}' was invoked but not with message '${options.message}'. Messages: ${JSON.stringify(messages)}`,
      );
    }
  }
}

/**
 * Assert that the most recent policy evaluation allowed the action.
 *
 * @param spyPolicy - The SpyPolicy to inspect.
 * @throws {Error} If no evaluate calls or the last one denied.
 */
export function assertPolicyAllowed(spyPolicy: SpyPolicy): void {
  if (spyPolicy.evaluateCalls.length === 0) {
    throw new Error("SpyPolicy has no recorded evaluate calls");
  }
  const lastCall = spyPolicy.evaluateCalls[spyPolicy.evaluateCalls.length - 1]!;
  if (!lastCall.result.allowed) {
    throw new Error(
      `Expected policy to allow, but it denied with reason: ${lastCall.result.reason}`,
    );
  }
}

/**
 * Assert that the most recent policy evaluation denied the action.
 *
 * @param spyPolicy - The SpyPolicy to inspect.
 * @param options - Optional reason filter.
 * @throws {Error} If no evaluate calls or the last one allowed.
 */
export function assertPolicyDenied(
  spyPolicy: SpyPolicy,
  options?: { reason?: string },
): void {
  if (spyPolicy.evaluateCalls.length === 0) {
    throw new Error("SpyPolicy has no recorded evaluate calls");
  }
  const lastCall = spyPolicy.evaluateCalls[spyPolicy.evaluateCalls.length - 1]!;
  if (lastCall.result.allowed) {
    throw new Error("Expected policy to deny, but it allowed");
  }
  if (options?.reason !== undefined && lastCall.result.reason !== options.reason) {
    throw new Error(
      `Expected denial reason '${options.reason}', got '${lastCall.result.reason}'`,
    );
  }
}

/**
 * Assert that at least one memory store() call was made.
 *
 * @param spyMemory - The SpyMemory to inspect.
 * @param options - Optional content filter.
 * @throws {Error} If no store calls or no matching content.
 */
export function assertMemoryStored(
  spyMemory: SpyMemory,
  options?: { content?: string },
): void {
  if (spyMemory.storeCalls.length === 0) {
    throw new Error("SpyMemory has no recorded store calls");
  }
  if (options?.content !== undefined) {
    const contents = spyMemory.storeCalls.map((c) => c.event.content);
    if (!contents.includes(options.content)) {
      throw new Error(
        `No stored event with content '${options.content}'. Stored: ${JSON.stringify(contents)}`,
      );
    }
  }
}

/**
 * Assert that at least one memory recall() call was made.
 *
 * @param spyMemory - The SpyMemory to inspect.
 * @param options - Optional query filter.
 * @throws {Error} If no recall calls or no matching query.
 */
export function assertMemoryRecalled(
  spyMemory: SpyMemory,
  options?: { query?: string },
): void {
  if (spyMemory.recallCalls.length === 0) {
    throw new Error("SpyMemory has no recorded recall calls");
  }
  if (options?.query !== undefined) {
    const queries = spyMemory.recallCalls.map((c) => c.query);
    if (!queries.includes(options.query)) {
      throw new Error(
        `No recall with query '${options.query}'. Queries: ${JSON.stringify(queries)}`,
      );
    }
  }
}

/**
 * Assert that a specific tool was called.
 *
 * @param spyTools - The SpyToolManager to inspect.
 * @param toolName - Expected tool name.
 * @param options - Optional args filter.
 * @throws {Error} If no matching tool call is found.
 */
export function assertToolCalled(
  spyTools: SpyToolManager,
  toolName: string,
  options?: { args?: Record<string, unknown> },
): void {
  const matching = spyTools.callCalls.filter((c) => c.toolName === toolName);
  if (matching.length === 0) {
    const called = spyTools.callCalls.map((c) => c.toolName);
    throw new Error(
      `Tool '${toolName}' was never called. Called: ${JSON.stringify(called)}`,
    );
  }
  if (options?.args !== undefined) {
    const argsMatch = matching.some(
      (c) => JSON.stringify(c.args) === JSON.stringify(options.args),
    );
    if (!argsMatch) {
      const actualArgs = matching.map((c) => c.args);
      throw new Error(
        `Tool '${toolName}' was called but not with args ${JSON.stringify(options.args)}. Args: ${JSON.stringify(actualArgs)}`,
      );
    }
  }
}

/**
 * Assert that all spies have consumed their expectations.
 *
 * @param result - The TestOrchestratorResult to inspect.
 * @throws {Error} If any spy has pending expectations.
 */
export function assertNoUnconsumedExpectations(
  result: TestOrchestratorResult,
): void {
  result.verifyAllExpectationsConsumed();
}

/**
 * Assert that primitives were called in the expected order.
 *
 * @param result - The TestOrchestratorResult to inspect.
 * @param expectedOrder - List of primitive names in expected execution order.
 * @throws {Error} If the actual order doesn't match.
 */
export function assertPipelineOrder(
  result: TestOrchestratorResult,
  expectedOrder: string[],
): void {
  const timestampMap: Record<string, number> = {};

  if (result.router.classifyCalls.length > 0) {
    timestampMap["router"] = result.router.classifyCalls[0]!.timestamp;
  }
  if (result.runtime.invokeCalls.length > 0) {
    timestampMap["runtime"] = result.runtime.invokeCalls[0]!.timestamp;
  }
  if (result.responder.formatCalls.length > 0) {
    timestampMap["responder"] = result.responder.formatCalls[0]!.timestamp;
  }
  if (result.memory.recallCalls.length > 0) {
    timestampMap["memory"] = result.memory.recallCalls[0]!.timestamp;
  }
  if (result.policy.evaluateCalls.length > 0) {
    timestampMap["policy"] = result.policy.evaluateCalls[0]!.timestamp;
  }
  const toolTimestamps = [
    ...result.tools.discoverCalls.map((c) => c.timestamp),
    ...result.tools.callCalls.map((c) => c.timestamp),
  ];
  if (toolTimestamps.length > 0) {
    timestampMap["tools"] = Math.min(...toolTimestamps);
  }

  const actualOrder = expectedOrder.filter((name) => name in timestampMap);
  const actualSorted = [...actualOrder].sort(
    (a, b) => (timestampMap[a] ?? 0) - (timestampMap[b] ?? 0),
  );

  if (JSON.stringify(actualSorted) !== JSON.stringify(actualOrder)) {
    throw new Error(
      `Expected pipeline order ${JSON.stringify(actualOrder)}, but got ${JSON.stringify(actualSorted)}`,
    );
  }
}
