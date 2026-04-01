"""Assertion helpers for Nerva testkit.

Concise, readable assertions for the most common test scenarios.
Each function inspects spy call records and raises ``AssertionError``
with a descriptive message on failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.testkit.builders import TestOrchestratorResult
    from nerva.testkit.spies import (
        SpyMemory,
        SpyPolicy,
        SpyRouter,
        SpyRuntime,
        SpyToolManager,
    )


def assert_routed_to(spy_router: SpyRouter, handler_name: str) -> None:
    """Assert that the router's most recent classify() selected the given handler.

    Args:
        spy_router: The SpyRouter to inspect.
        handler_name: Expected handler name.

    Raises:
        AssertionError: If no classify calls recorded or handler doesn't match.
    """
    assert len(spy_router.classify_calls) > 0, "SpyRouter has no recorded classify calls"
    last_call = spy_router.classify_calls[-1]
    best = last_call.result.best_handler
    actual_name = best.name if best else None
    assert actual_name == handler_name, (
        f"Expected route to '{handler_name}', got '{actual_name}'"
    )


def assert_handler_invoked(
    spy_runtime: SpyRuntime,
    handler_name: str,
    *,
    message: str | None = None,
) -> None:
    """Assert that the runtime invoked a specific handler.

    Args:
        spy_runtime: The SpyRuntime to inspect.
        handler_name: Expected handler name.
        message: If provided, also assert the input message matches.

    Raises:
        AssertionError: If no matching invoke call is found.
    """
    matching = [c for c in spy_runtime.invoke_calls if c.handler == handler_name]
    assert len(matching) > 0, (
        f"Handler '{handler_name}' was never invoked. "
        f"Invoked handlers: {[c.handler for c in spy_runtime.invoke_calls]}"
    )
    if message is not None:
        messages = [c.input.message for c in matching]
        assert message in messages, (
            f"Handler '{handler_name}' was invoked but not with message '{message}'. "
            f"Messages seen: {messages}"
        )


def assert_policy_allowed(spy_policy: SpyPolicy) -> None:
    """Assert that the most recent policy evaluation allowed the action.

    Args:
        spy_policy: The SpyPolicy to inspect.

    Raises:
        AssertionError: If no evaluate calls or the last one denied.
    """
    assert len(spy_policy.evaluate_calls) > 0, "SpyPolicy has no recorded evaluate calls"
    last_call = spy_policy.evaluate_calls[-1]
    assert last_call.result.allowed, (
        f"Expected policy to allow, but it denied with reason: {last_call.result.reason}"
    )


def assert_policy_denied(spy_policy: SpyPolicy, *, reason: str | None = None) -> None:
    """Assert that the most recent policy evaluation denied the action.

    Args:
        spy_policy: The SpyPolicy to inspect.
        reason: If provided, also assert the denial reason matches.

    Raises:
        AssertionError: If no evaluate calls or the last one allowed.
    """
    assert len(spy_policy.evaluate_calls) > 0, "SpyPolicy has no recorded evaluate calls"
    last_call = spy_policy.evaluate_calls[-1]
    assert not last_call.result.allowed, "Expected policy to deny, but it allowed"
    if reason is not None:
        assert last_call.result.reason == reason, (
            f"Expected denial reason '{reason}', got '{last_call.result.reason}'"
        )


def assert_memory_stored(spy_memory: SpyMemory, *, content: str | None = None) -> None:
    """Assert that at least one memory store() call was made.

    Args:
        spy_memory: The SpyMemory to inspect.
        content: If provided, assert that at least one stored event has this content.

    Raises:
        AssertionError: If no store calls or no matching content.
    """
    assert len(spy_memory.store_calls) > 0, "SpyMemory has no recorded store calls"
    if content is not None:
        contents = [c.event.content for c in spy_memory.store_calls]
        assert content in contents, (
            f"No stored event with content '{content}'. Stored: {contents}"
        )


def assert_memory_recalled(spy_memory: SpyMemory, *, query: str | None = None) -> None:
    """Assert that at least one memory recall() call was made.

    Args:
        spy_memory: The SpyMemory to inspect.
        query: If provided, assert that at least one recall used this query.

    Raises:
        AssertionError: If no recall calls or no matching query.
    """
    assert len(spy_memory.recall_calls) > 0, "SpyMemory has no recorded recall calls"
    if query is not None:
        queries = [c.query for c in spy_memory.recall_calls]
        assert query in queries, (
            f"No recall with query '{query}'. Queries: {queries}"
        )


def assert_tool_called(
    spy_tools: SpyToolManager,
    tool_name: str,
    *,
    args: dict[str, object] | None = None,
) -> None:
    """Assert that a specific tool was called.

    Args:
        spy_tools: The SpyToolManager to inspect.
        tool_name: Expected tool name.
        args: If provided, assert the tool was called with these args.

    Raises:
        AssertionError: If no matching tool call is found.
    """
    matching = [c for c in spy_tools.call_calls if c.tool_name == tool_name]
    assert len(matching) > 0, (
        f"Tool '{tool_name}' was never called. "
        f"Called tools: {[c.tool_name for c in spy_tools.call_calls]}"
    )
    if args is not None:
        actual_args = [c.args for c in matching]
        assert args in actual_args, (
            f"Tool '{tool_name}' was called but not with args {args}. "
            f"Args seen: {actual_args}"
        )


def assert_no_unconsumed_expectations(result: TestOrchestratorResult) -> None:
    """Assert that all spies have consumed their expectations.

    Args:
        result: The TestOrchestratorResult to inspect.

    Raises:
        AssertionError: If any spy has pending expectations.
    """
    result.verify_all_expectations_consumed()


def assert_pipeline_order(
    result: TestOrchestratorResult,
    expected_order: list[str],
) -> None:
    """Assert that primitives were called in the expected order.

    Compares timestamps of the earliest call to each primitive.
    Valid primitive names: ``"router"``, ``"runtime"``, ``"responder"``,
    ``"memory"``, ``"policy"``, ``"tools"``.

    Args:
        result: The TestOrchestratorResult to inspect.
        expected_order: List of primitive names in expected execution order.

    Raises:
        AssertionError: If the actual order doesn't match.
    """
    timestamp_map: dict[str, float] = {}

    if result.router.classify_calls:
        timestamp_map["router"] = result.router.classify_calls[0].timestamp
    if result.runtime.invoke_calls:
        timestamp_map["runtime"] = result.runtime.invoke_calls[0].timestamp
    if result.responder.format_calls:
        timestamp_map["responder"] = result.responder.format_calls[0].timestamp
    if result.memory.recall_calls:
        timestamp_map["memory"] = result.memory.recall_calls[0].timestamp
    if result.policy.evaluate_calls:
        timestamp_map["policy"] = result.policy.evaluate_calls[0].timestamp
    if result.tools.discover_calls or result.tools.call_calls:
        tool_timestamps = (
            [c.timestamp for c in result.tools.discover_calls]
            + [c.timestamp for c in result.tools.call_calls]
        )
        timestamp_map["tools"] = min(tool_timestamps)

    actual_order = [
        name for name in expected_order if name in timestamp_map
    ]
    actual_sorted = sorted(actual_order, key=lambda n: timestamp_map[n])

    assert actual_sorted == actual_order, (
        f"Expected pipeline order {actual_order}, but got {actual_sorted}"
    )
