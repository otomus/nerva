"""Tests for Nerva testkit — spies, builders, assertions, and boundaries."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.policy import ALLOW, PolicyAction, PolicyDecision
from nerva.responder import API_CHANNEL, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.testkit import (
    AllowAllPolicy,
    DenyAllPolicy,
    SpyMemory,
    SpyPolicy,
    SpyResponder,
    SpyRouter,
    SpyRuntime,
    SpyToolManager,
    StubLLMHandler,
    TestOrchestrator,
    assert_handler_invoked,
    assert_memory_recalled,
    assert_memory_stored,
    assert_no_unconsumed_expectations,
    assert_pipeline_order,
    assert_policy_allowed,
    assert_policy_denied,
    assert_routed_to,
    assert_tool_called,
)
from nerva.tools import ToolResult, ToolStatus

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> ExecContext:
    """Build a minimal ExecContext for tests."""
    return make_ctx()


# ---------------------------------------------------------------------------
# SpyRouter
# ---------------------------------------------------------------------------


class TestSpyRouter:
    """Verify SpyRouter records calls and consumes expectations."""

    @pytest.mark.asyncio
    async def test_passthrough_records_call(self) -> None:
        """Without expectations, SpyRouter delegates and records."""
        from nerva.router.rule import Rule, RuleRouter

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="catch", intent="any")])
        spy = SpyRouter(inner)
        ctx = _make_ctx()

        result = await spy.classify("hello", ctx)

        assert len(spy.classify_calls) == 1
        assert spy.classify_calls[0].message == "hello"
        assert spy.classify_calls[0].was_expected is False
        assert result.best_handler is not None
        assert result.best_handler.name == "catch"

    @pytest.mark.asyncio
    async def test_expectation_consumed_fifo(self) -> None:
        """expect_handler() expectations are consumed in FIFO order."""
        from nerva.router.rule import Rule, RuleRouter

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="real", intent="any")])
        spy = SpyRouter(inner)
        ctx = _make_ctx()

        spy.expect_handler("first_agent")
        spy.expect_handler("second_agent")

        r1 = await spy.classify("msg1", ctx)
        r2 = await spy.classify("msg2", ctx)
        r3 = await spy.classify("msg3", ctx)

        assert r1.best_handler is not None and r1.best_handler.name == "first_agent"
        assert r2.best_handler is not None and r2.best_handler.name == "second_agent"
        assert r3.best_handler is not None and r3.best_handler.name == "real"
        assert spy.classify_calls[0].was_expected is True
        assert spy.classify_calls[1].was_expected is True
        assert spy.classify_calls[2].was_expected is False

    @pytest.mark.asyncio
    async def test_expect_intent_returns_full_result(self) -> None:
        """expect_intent() returns the exact IntentResult provided."""
        from nerva.router.rule import Rule, RuleRouter

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="real", intent="any")])
        spy = SpyRouter(inner)

        custom = IntentResult(
            intent="custom",
            confidence=0.8,
            handlers=[HandlerCandidate(name="custom_handler", score=0.8)],
        )
        spy.expect_intent(custom)

        result = await spy.classify("test", _make_ctx())
        assert result is custom

    def test_reset_clears_everything(self) -> None:
        """reset() clears calls and expectations."""
        from nerva.router.rule import Rule, RuleRouter

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="r", intent="i")])
        spy = SpyRouter(inner)
        spy.expect_handler("x")
        spy.reset()

        assert spy.pending_expectations == 0
        assert len(spy.classify_calls) == 0

    def test_verify_expectations_consumed_raises(self) -> None:
        """verify_expectations_consumed() raises when expectations remain."""
        from nerva.router.rule import Rule, RuleRouter

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="r", intent="i")])
        spy = SpyRouter(inner)
        spy.expect_handler("unconsumed")

        with pytest.raises(AssertionError, match="unconsumed"):
            spy.verify_expectations_consumed()


# ---------------------------------------------------------------------------
# SpyRuntime
# ---------------------------------------------------------------------------


class TestSpyRuntime:
    """Verify SpyRuntime records calls and consumes expectations."""

    @pytest.mark.asyncio
    async def test_expect_llm_response(self) -> None:
        """expect_llm_response() returns a SUCCESS result with given output."""
        from nerva.runtime.inprocess import InProcessRuntime

        inner = InProcessRuntime()
        spy = SpyRuntime(inner)
        spy.expect_llm_response("Hello from LLM!")

        result = await spy.invoke("any_handler", AgentInput(message="hi"), _make_ctx())

        assert result.status == AgentStatus.SUCCESS
        assert result.output == "Hello from LLM!"
        assert len(spy.invoke_calls) == 1
        assert spy.invoke_calls[0].was_expected is True

    @pytest.mark.asyncio
    async def test_expect_result_custom(self) -> None:
        """expect_result() returns the exact AgentResult provided."""
        from nerva.runtime.inprocess import InProcessRuntime

        inner = InProcessRuntime()
        spy = SpyRuntime(inner)

        custom = AgentResult(status=AgentStatus.ERROR, error="something broke")
        spy.expect_result(custom)

        result = await spy.invoke("h", AgentInput(message="x"), _make_ctx())
        assert result is custom

    @pytest.mark.asyncio
    async def test_passthrough_after_expectations_exhausted(self) -> None:
        """After expectations are consumed, calls delegate to real runtime."""
        from nerva.runtime.inprocess import InProcessRuntime

        inner = InProcessRuntime()

        async def echo_handler(inp: AgentInput, ctx: ExecContext) -> str:
            return f"echo: {inp.message}"

        inner.register("echo", echo_handler)
        spy = SpyRuntime(inner)
        spy.expect_llm_response("expected")

        r1 = await spy.invoke("echo", AgentInput(message="first"), _make_ctx())
        r2 = await spy.invoke("echo", AgentInput(message="second"), _make_ctx())

        assert r1.output == "expected"
        assert r2.output == "echo: second"


# ---------------------------------------------------------------------------
# SpyMemory
# ---------------------------------------------------------------------------


class TestSpyMemory:
    """Verify SpyMemory records calls and consumes expectations."""

    @pytest.mark.asyncio
    async def test_recall_with_expectation(self) -> None:
        """expect_recall() returns the configured MemoryContext."""
        from nerva.memory.tiered import TieredMemory
        from nerva.memory.hot import InMemoryHotMemory

        inner = TieredMemory(hot=InMemoryHotMemory())
        spy = SpyMemory(inner)

        expected_ctx = MemoryContext(
            conversation=[{"role": "user", "content": "prev"}],
        )
        spy.expect_recall(expected_ctx)

        result = await spy.recall("query", _make_ctx())

        assert result is expected_ctx
        assert len(spy.recall_calls) == 1
        assert spy.recall_calls[0].was_expected is True

    @pytest.mark.asyncio
    async def test_store_records_call(self) -> None:
        """store() records the event and delegates to real memory."""
        from nerva.memory.tiered import TieredMemory
        from nerva.memory.hot import InMemoryHotMemory

        inner = TieredMemory(hot=InMemoryHotMemory())
        spy = SpyMemory(inner)

        event = MemoryEvent(content="test", tier=MemoryTier.HOT, source="test")
        await spy.store(event, _make_ctx())

        assert len(spy.store_calls) == 1
        assert spy.store_calls[0].event.content == "test"


# ---------------------------------------------------------------------------
# SpyPolicy
# ---------------------------------------------------------------------------


class TestSpyPolicy:
    """Verify SpyPolicy records calls and consumes expectations."""

    @pytest.mark.asyncio
    async def test_expect_allow(self) -> None:
        """expect_allow() returns ALLOW on next evaluate()."""
        from nerva.policy.noop import NoopPolicyEngine

        spy = SpyPolicy(NoopPolicyEngine())
        spy.expect_allow()

        action = PolicyAction(kind="test", subject="user", target="target")
        result = await spy.evaluate(action, _make_ctx())

        assert result.allowed is True
        assert spy.evaluate_calls[0].was_expected is True

    @pytest.mark.asyncio
    async def test_expect_deny(self) -> None:
        """expect_deny() returns a denial on next evaluate()."""
        from nerva.policy.noop import NoopPolicyEngine

        spy = SpyPolicy(NoopPolicyEngine())
        spy.expect_deny(reason="over budget")

        action = PolicyAction(kind="test", subject="user", target="target")
        result = await spy.evaluate(action, _make_ctx())

        assert result.allowed is False
        assert result.reason == "over budget"

    @pytest.mark.asyncio
    async def test_record_is_tracked(self) -> None:
        """record() calls are tracked in record_calls."""
        from nerva.policy.noop import NoopPolicyEngine

        spy = SpyPolicy(NoopPolicyEngine())

        action = PolicyAction(kind="test", subject="user", target="target")
        decision = ALLOW
        await spy.record(action, decision, _make_ctx())

        assert len(spy.record_calls) == 1
        assert spy.record_calls[0].action is action


# ---------------------------------------------------------------------------
# SpyToolManager
# ---------------------------------------------------------------------------


class TestSpyToolManager:
    """Verify SpyToolManager records calls and consumes expectations."""

    @pytest.mark.asyncio
    async def test_expect_tool_result(self) -> None:
        """expect_tool_result() returns configured result for specific tool."""
        from nerva.tools.function import FunctionToolManager

        inner = FunctionToolManager()
        spy = SpyToolManager(inner)

        expected = ToolResult(status=ToolStatus.SUCCESS, output="found 3 results")
        spy.expect_tool_result("search", expected)

        result = await spy.call("search", {"q": "cats"}, _make_ctx())

        assert result is expected
        assert len(spy.call_calls) == 1
        assert spy.call_calls[0].tool_name == "search"
        assert spy.call_calls[0].was_expected is True

    @pytest.mark.asyncio
    async def test_different_tools_have_independent_queues(self) -> None:
        """Each tool name has its own expectation queue."""
        from nerva.tools.function import FunctionToolManager

        inner = FunctionToolManager()
        spy = SpyToolManager(inner)

        spy.expect_tool_result("search", ToolResult(status=ToolStatus.SUCCESS, output="search result"))
        spy.expect_tool_result("calc", ToolResult(status=ToolStatus.SUCCESS, output="42"))

        r1 = await spy.call("calc", {}, _make_ctx())
        r2 = await spy.call("search", {}, _make_ctx())

        assert r1.output == "42"
        assert r2.output == "search result"


# ---------------------------------------------------------------------------
# TestOrchestrator builder
# ---------------------------------------------------------------------------


class TestTestOrchestrator:
    """Verify the builder wires everything correctly."""

    @pytest.mark.asyncio
    async def test_build_with_defaults(self) -> None:
        """build() creates a working orchestrator with spy-wrapped defaults."""
        result = TestOrchestrator.build()

        assert result.orchestrator is not None
        assert isinstance(result.router, SpyRouter)
        assert isinstance(result.runtime, SpyRuntime)
        assert isinstance(result.responder, SpyResponder)
        assert isinstance(result.memory, SpyMemory)
        assert isinstance(result.policy, SpyPolicy)
        assert isinstance(result.tools, SpyToolManager)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_expectations(self) -> None:
        """End-to-end: set expectations and verify pipeline runs."""
        result = TestOrchestrator.build()

        result.runtime.expect_llm_response("Hello from agent!")

        response = await result.orchestrator.handle("hi")

        assert response.text == "Hello from agent!"
        assert_routed_to(result.router, "default")

    @pytest.mark.asyncio
    async def test_reset_all(self) -> None:
        """reset_all() clears all spy state."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("test")
        await result.orchestrator.handle("hi")

        result.reset_all()

        assert len(result.router.classify_calls) == 0
        assert len(result.runtime.invoke_calls) == 0
        assert result.router.pending_expectations == 0

    @pytest.mark.asyncio
    async def test_custom_handlers(self) -> None:
        """build() accepts handler functions for InProcessRuntime."""
        async def greet(inp: AgentInput, ctx: ExecContext) -> str:
            return f"Hi, {inp.message}!"

        result = TestOrchestrator.build(handlers={"default": greet})

        response = await result.orchestrator.handle("world")
        assert response.text == "Hi, world!"
        assert_handler_invoked(result.runtime, "default")


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


class TestAssertions:
    """Verify assertion helpers produce correct pass/fail."""

    @pytest.mark.asyncio
    async def test_assert_routed_to_passes(self) -> None:
        """assert_routed_to passes when handler matches."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("hi")
        assert_routed_to(result.router, "default")

    @pytest.mark.asyncio
    async def test_assert_routed_to_fails(self) -> None:
        """assert_routed_to fails when handler doesn't match."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("hi")

        with pytest.raises(AssertionError, match="wrong_handler"):
            assert_routed_to(result.router, "wrong_handler")

    @pytest.mark.asyncio
    async def test_assert_policy_allowed_passes(self) -> None:
        """assert_policy_allowed passes when policy allows."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("hi")
        assert_policy_allowed(result.policy)

    @pytest.mark.asyncio
    async def test_assert_policy_denied_passes(self) -> None:
        """assert_policy_denied passes when policy denies."""
        from nerva.policy.noop import NoopPolicyEngine

        spy = SpyPolicy(NoopPolicyEngine())
        spy.expect_deny(reason="budget exceeded")

        action = PolicyAction(kind="test", subject="u", target="t")
        await spy.evaluate(action, _make_ctx())

        assert_policy_denied(spy, reason="budget exceeded")

    @pytest.mark.asyncio
    async def test_assert_memory_stored_passes(self) -> None:
        """assert_memory_stored passes when content matches."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("stored_content")
        await result.orchestrator.handle("hi")
        assert_memory_stored(result.memory, content="stored_content")

    @pytest.mark.asyncio
    async def test_assert_memory_recalled_passes(self) -> None:
        """assert_memory_recalled passes when query was used."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("my query")
        assert_memory_recalled(result.memory, query="my query")

    @pytest.mark.asyncio
    async def test_assert_no_unconsumed_expectations_passes(self) -> None:
        """assert_no_unconsumed_expectations passes when all consumed."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("hi")
        assert_no_unconsumed_expectations(result)

    @pytest.mark.asyncio
    async def test_assert_no_unconsumed_expectations_fails(self) -> None:
        """assert_no_unconsumed_expectations fails with pending expectations."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("never consumed")

        with pytest.raises(AssertionError, match="unconsumed"):
            assert_no_unconsumed_expectations(result)

    @pytest.mark.asyncio
    async def test_assert_pipeline_order(self) -> None:
        """assert_pipeline_order verifies primitives execute in order."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("ok")
        await result.orchestrator.handle("hi")

        assert_pipeline_order(result, ["policy", "memory", "router", "runtime", "responder"])


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


class TestBoundaries:
    """Verify boundary stubs."""

    @pytest.mark.asyncio
    async def test_stub_llm_handler_returns_canned_responses(self) -> None:
        """StubLLMHandler returns responses in order, then default."""
        handler = StubLLMHandler(responses=["first", "second"], default_response="default")

        r1 = await handler(AgentInput(message="a"), _make_ctx())
        r2 = await handler(AgentInput(message="b"), _make_ctx())
        r3 = await handler(AgentInput(message="c"), _make_ctx())

        assert r1.output == "first"
        assert r2.output == "second"
        assert r3.output == "default"
        assert handler.call_count == 3

    @pytest.mark.asyncio
    async def test_deny_all_policy(self) -> None:
        """DenyAllPolicy always denies."""
        policy = DenyAllPolicy(reason="test denial")
        action = PolicyAction(kind="test", subject="u", target="t")
        result = await policy.evaluate(action, _make_ctx())
        assert result.allowed is False
        assert result.reason == "test denial"

    @pytest.mark.asyncio
    async def test_allow_all_policy(self) -> None:
        """AllowAllPolicy always allows."""
        policy = AllowAllPolicy()
        action = PolicyAction(kind="test", subject="u", target="t")
        result = await policy.evaluate(action, _make_ctx())
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Stress testkit components with unusual inputs."""

    @pytest.mark.asyncio
    async def test_spy_with_empty_message(self) -> None:
        """Spies handle empty string messages."""
        result = TestOrchestrator.build()
        # InMemoryHotMemory rejects empty content, so use non-empty output
        result.runtime.expect_llm_response("nonempty")
        response = await result.orchestrator.handle("")
        assert isinstance(response, Response)

    @pytest.mark.asyncio
    async def test_spy_with_unicode_message(self) -> None:
        """Spies handle unicode and special characters."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("\u2764\ufe0f \U0001f680")
        response = await result.orchestrator.handle("\u4f60\u597d")
        assert "\u2764" in response.text

    @pytest.mark.asyncio
    async def test_multiple_sequential_expectations(self) -> None:
        """Multiple expectations are consumed in order across calls."""
        result = TestOrchestrator.build()
        result.runtime.expect_llm_response("first")
        result.runtime.expect_llm_response("second")
        result.runtime.expect_llm_response("third")

        r1 = await result.orchestrator.handle("a")
        r2 = await result.orchestrator.handle("b")
        r3 = await result.orchestrator.handle("c")

        assert r1.text == "first"
        assert r2.text == "second"
        assert r3.text == "third"

    @pytest.mark.asyncio
    async def test_tool_expectation_for_nonexistent_tool(self) -> None:
        """Tool expectations work even for tools not registered."""
        result = TestOrchestrator.build()
        expected = ToolResult(status=ToolStatus.SUCCESS, output="magic")
        result.tools.expect_tool_result("nonexistent", expected)

        tool_result = await result.tools.call("nonexistent", {}, _make_ctx())
        assert tool_result.output == "magic"

    def test_spy_inner_property(self) -> None:
        """Each spy exposes its inner implementation via .inner property."""
        from nerva.router.rule import RuleRouter, Rule

        inner = RuleRouter(rules=[Rule(pattern=".*", handler="h", intent="i")])
        spy = SpyRouter(inner)
        assert spy.inner is inner
