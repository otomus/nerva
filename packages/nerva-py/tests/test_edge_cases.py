"""Cross-cutting edge case tests — stress boundaries across multiple primitives."""

from __future__ import annotations

import asyncio
import time

import pytest

from nerva.context import ExecContext, InMemoryStreamSink, Permissions, Scope, TokenUsage
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.orchestrator import FALLBACK_HANDLER, Orchestrator, PolicyDeniedError
from nerva.policy import PolicyAction, PolicyDecision
from nerva.registry import (
    ComponentKind,
    HealthStatus,
    InvocationStats,
    RegistryEntry,
    RegistryPatch,
)
from nerva.responder import API_CHANNEL, Channel, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.router.rule import Rule, RuleRouter
from nerva.runtime import AgentInput, AgentResult, AgentStatus

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Lightweight mocks (same pattern as test_orchestrator, minimal versions)
# ---------------------------------------------------------------------------


class _StubRouter:
    """Returns a fixed handler or empty result."""

    def __init__(self, handler: str | None = "stub") -> None:
        self._handler = handler

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        if self._handler is None:
            return IntentResult(intent="unknown", confidence=0.0, handlers=[])
        candidate = HandlerCandidate(name=self._handler, score=0.8, reason="stub")
        return IntentResult(
            intent="stub", confidence=0.8, handlers=[candidate]
        )


class _StubRuntime:
    """Always returns SUCCESS with echoed input."""

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=input.message,
            handler=handler,
        )

    async def invoke_chain(self, handlers, input, ctx):
        return await self.invoke(handlers[-1] if handlers else "none", input, ctx)

    async def delegate(self, handler, input, parent_ctx):
        return await self.invoke(handler, input, parent_ctx)


class _StubResponder:
    async def format(self, output: AgentResult, channel: Channel, ctx: ExecContext) -> Response:
        return Response(text=output.output, channel=channel)


class _StubMemory:
    """Tracks recall/store calls."""

    def __init__(self) -> None:
        self.recall_calls: list[str] = []
        self.store_calls: list[MemoryEvent] = []

    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        self.recall_calls.append(query)
        return MemoryContext()

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        self.store_calls.append(event)

    async def consolidate(self, ctx: ExecContext) -> None:
        pass


def _orch(
    *,
    router: object | None = None,
    runtime: object | None = None,
    responder: object | None = None,
    memory: object | None = None,
    policy: object | None = None,
) -> Orchestrator:
    return Orchestrator(
        router=router or _StubRouter(),
        runtime=runtime or _StubRuntime(),
        responder=responder or _StubResponder(),
        memory=memory,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# ExecContext edge cases
# ---------------------------------------------------------------------------


class TestExecContextEdgeCases:
    """Push ExecContext beyond normal operating bounds."""

    @pytest.mark.asyncio
    async def test_timeout_zero_is_immediately_timed_out(self) -> None:
        """timeout_seconds=0 means the context is timed out the instant it's created."""
        ctx = make_ctx(timeout_seconds=0)
        # time.time() will be >= created_at + 0
        assert ctx.is_timed_out()

    @pytest.mark.asyncio
    async def test_child_inherits_cancelled_state(self) -> None:
        """If the parent is cancelled before child(), the child is also cancelled."""
        parent = make_ctx()
        parent.cancelled.set()

        child = parent.child("sub_handler")

        assert child.is_cancelled()

    @pytest.mark.asyncio
    async def test_child_shares_cancellation_event(self) -> None:
        """Cancelling the parent after creating a child cancels both."""
        parent = make_ctx()
        child = parent.child("sub_handler")

        assert not child.is_cancelled()
        parent.cancelled.set()
        assert child.is_cancelled()

    @pytest.mark.asyncio
    async def test_child_inherits_trace_id(self) -> None:
        """Child context keeps the parent's trace_id."""
        parent = make_ctx()
        child = parent.child("handler_x")
        assert child.trace_id == parent.trace_id

    @pytest.mark.asyncio
    async def test_child_gets_fresh_request_id(self) -> None:
        """Child context gets a new request_id distinct from the parent."""
        parent = make_ctx()
        child = parent.child("handler_x")
        assert child.request_id != parent.request_id

    @pytest.mark.asyncio
    async def test_child_inherits_timeout(self) -> None:
        """Child context preserves the parent's timeout_at."""
        parent = make_ctx(timeout_seconds=60.0)
        child = parent.child("sub")
        assert child.timeout_at == parent.timeout_at

    @pytest.mark.asyncio
    async def test_negative_timeout_is_immediately_timed_out(self) -> None:
        """A negative timeout results in an already-expired context."""
        ctx = make_ctx(timeout_seconds=-1.0)
        assert ctx.is_timed_out()

    @pytest.mark.asyncio
    async def test_elapsed_seconds_non_negative(self) -> None:
        """elapsed_seconds() should be >= 0."""
        ctx = make_ctx()
        assert ctx.elapsed_seconds() >= 0.0

    @pytest.mark.asyncio
    async def test_add_event_stores_attributes(self) -> None:
        """add_event() preserves keyword attributes."""
        ctx = make_ctx()
        event = ctx.add_event("test.event", foo="bar", baz="qux")
        assert event.attributes == {"foo": "bar", "baz": "qux"}
        assert ctx.events[-1] is event

    @pytest.mark.asyncio
    async def test_token_usage_add_is_immutable(self) -> None:
        """TokenUsage.add() returns a new object, not mutating either operand."""
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        b = TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        c = a.add(b)

        assert c.prompt_tokens == 13
        assert a.prompt_tokens == 10  # unchanged
        assert b.prompt_tokens == 3   # unchanged


# ---------------------------------------------------------------------------
# RuleRouter edge cases
# ---------------------------------------------------------------------------


class TestRuleRouterEdgeCases:
    """Boundary conditions for the rule-based router."""

    @pytest.mark.asyncio
    async def test_empty_rules_returns_no_match(self) -> None:
        """RuleRouter with zero rules returns an empty result."""
        router = RuleRouter(rules=[], default_handler=None)
        ctx = make_ctx()
        result = await router.classify("anything", ctx)

        assert result.intent == "unknown"
        assert result.confidence == 0.0
        assert result.handlers == []

    @pytest.mark.asyncio
    async def test_empty_rules_with_default_uses_default(self) -> None:
        """RuleRouter with no rules but a default handler uses the default."""
        router = RuleRouter(rules=[], default_handler="fallback_agent")
        ctx = make_ctx()
        result = await router.classify("anything", ctx)

        assert result.handlers[0].name == "fallback_agent"

    @pytest.mark.asyncio
    async def test_whitespace_message_returns_empty(self) -> None:
        """Whitespace-only message returns empty result even with rules."""
        rules = [Rule(pattern="hello", handler="greeter", intent="greet")]
        router = RuleRouter(rules=rules)
        ctx = make_ctx()
        result = await router.classify("   ", ctx)

        assert result.handlers == []

    @pytest.mark.asyncio
    async def test_rules_must_be_list(self) -> None:
        """Passing a non-list for rules raises TypeError."""
        with pytest.raises(TypeError, match="rules must be a list"):
            RuleRouter(rules="not a list")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_invalid_regex_raises(self) -> None:
        """A rule with invalid regex raises re.error at construction."""
        import re
        with pytest.raises(re.error):
            RuleRouter(rules=[Rule(pattern="[invalid", handler="h", intent="i")])


# ---------------------------------------------------------------------------
# Registry data structures
# ---------------------------------------------------------------------------


class TestRegistryEdgeCases:
    """Edge cases for registry value types."""

    def test_invocation_stats_zero_calls(self) -> None:
        """Fresh InvocationStats has all zeros."""
        stats = InvocationStats()
        assert stats.total_calls == 0
        assert stats.successes == 0
        assert stats.failures == 0
        assert stats.avg_duration_ms == 0.0
        assert stats.last_invoked_at is None

    def test_invocation_stats_first_success(self) -> None:
        """First success sets avg_duration directly (no smoothing)."""
        stats = InvocationStats()
        stats.record_success(100.0)
        assert stats.total_calls == 1
        assert stats.successes == 1
        assert stats.avg_duration_ms == 100.0

    def test_invocation_stats_ema_after_multiple(self) -> None:
        """After multiple calls, avg_duration uses EMA smoothing."""
        stats = InvocationStats()
        stats.record_success(100.0)  # first: sets to 100
        stats.record_success(200.0)  # second: EMA(0.2 * 200 + 0.8 * 100) = 120
        assert abs(stats.avg_duration_ms - 120.0) < 0.01

    def test_registry_entry_defaults(self) -> None:
        """RegistryEntry has sensible defaults."""
        entry = RegistryEntry(
            name="test_agent",
            kind=ComponentKind.AGENT,
            description="A test agent",
        )
        assert entry.health == HealthStatus.HEALTHY
        assert entry.enabled is True
        assert entry.requirements == []
        assert entry.permissions == []

    def test_registry_patch_all_none(self) -> None:
        """RegistryPatch with all None fields is valid (no-op update)."""
        patch = RegistryPatch()
        assert patch.description is None
        assert patch.health is None
        assert patch.enabled is None


# ---------------------------------------------------------------------------
# PolicyDecision edge cases
# ---------------------------------------------------------------------------


class TestPolicyDecisionEdgeCases:
    """Boundary conditions for policy decisions."""

    def test_budget_remaining_zero(self) -> None:
        """budget_remaining=0.0 is a valid (exhausted) state."""
        decision = PolicyDecision(allowed=True, budget_remaining=0.0)
        assert decision.budget_remaining == 0.0
        assert decision.allowed

    def test_deny_with_approvers(self) -> None:
        """A denial can require human approval from specific approvers."""
        decision = PolicyDecision(
            allowed=False,
            reason="needs approval",
            require_approval=True,
            approvers=["admin@example.com"],
        )
        assert not decision.allowed
        assert decision.require_approval
        assert decision.approvers == ["admin@example.com"]


# ---------------------------------------------------------------------------
# Full pipeline edge cases
# ---------------------------------------------------------------------------


class TestFullPipelineEdgeCases:
    """End-to-end scenarios that combine multiple primitives."""

    @pytest.mark.asyncio
    async def test_empty_message_through_pipeline(self) -> None:
        """Empty string flows through the full pipeline without errors."""
        orch = _orch()
        resp = await orch.handle("")
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_very_long_message_through_pipeline(self) -> None:
        """10k character message survives the full pipeline."""
        msg = "a" * 10_000
        orch = _orch()
        resp = await orch.handle(msg)
        assert resp.text == msg  # _StubRuntime echoes input

    @pytest.mark.asyncio
    async def test_unicode_through_pipeline(self) -> None:
        """Unicode, emoji, CJK, and control chars survive the pipeline."""
        msg = "\u00e9\u00e0\u00fc \U0001f600 \u4e16\u754c \u0000\n\t"
        orch = _orch()
        resp = await orch.handle(msg)
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_orchestrator_minimal_setup(self) -> None:
        """Orchestrator works with only required primitives (no memory, policy, etc.)."""
        orch = Orchestrator(
            router=_StubRouter(),
            runtime=_StubRuntime(),
            responder=_StubResponder(),
        )
        resp = await orch.handle("minimal")
        assert resp.text == "minimal"

    @pytest.mark.asyncio
    async def test_newlines_and_tabs_in_message(self) -> None:
        """Messages with embedded newlines and tabs are handled."""
        msg = "line1\nline2\ttab"
        orch = _orch()
        resp = await orch.handle(msg)
        assert resp.text == msg

    @pytest.mark.asyncio
    async def test_special_json_chars_in_message(self) -> None:
        """Braces, quotes, backslashes in messages don't break anything."""
        msg = '{"key": "value\\n"}'
        orch = _orch()
        resp = await orch.handle(msg)
        assert resp.text == msg

    @pytest.mark.asyncio
    async def test_memory_store_skipped_for_non_success(self) -> None:
        """Memory.store() is NOT called when runtime returns ERROR."""

        class ErrorRuntime:
            async def invoke(self, handler, input, ctx):
                return AgentResult(
                    status=AgentStatus.ERROR,
                    output="failure",
                    error="boom",
                    handler=handler,
                )

            async def invoke_chain(self, handlers, input, ctx):
                return await self.invoke(handlers[0], input, ctx)

            async def delegate(self, handler, input, parent_ctx):
                return await self.invoke(handler, input, parent_ctx)

        memory = _StubMemory()
        orch = _orch(runtime=ErrorRuntime(), memory=memory)
        await orch.handle("fail")

        assert len(memory.recall_calls) == 1
        assert len(memory.store_calls) == 0

    @pytest.mark.asyncio
    async def test_memory_store_skipped_for_timeout_status(self) -> None:
        """Memory.store() is NOT called when runtime returns TIMEOUT."""

        class TimeoutRuntime:
            async def invoke(self, handler, input, ctx):
                return AgentResult(
                    status=AgentStatus.TIMEOUT,
                    output="timed out",
                    handler=handler,
                )

            async def invoke_chain(self, handlers, input, ctx):
                return await self.invoke(handlers[0], input, ctx)

            async def delegate(self, handler, input, parent_ctx):
                return await self.invoke(handler, input, parent_ctx)

        memory = _StubMemory()
        orch = _orch(runtime=TimeoutRuntime(), memory=memory)
        await orch.handle("slow request")

        assert len(memory.store_calls) == 0


# ---------------------------------------------------------------------------
# InMemoryStreamSink edge cases
# ---------------------------------------------------------------------------


class TestInMemoryStreamSinkEdgeCases:
    """Boundary conditions for the test stream sink."""

    @pytest.mark.asyncio
    async def test_push_after_close_raises(self) -> None:
        """Pushing to a closed sink raises RuntimeError."""
        sink = InMemoryStreamSink()
        await sink.close()

        with pytest.raises(RuntimeError, match="Cannot push to a closed"):
            await sink.push("too late")

    @pytest.mark.asyncio
    async def test_double_close_raises(self) -> None:
        """Closing an already-closed sink raises RuntimeError."""
        sink = InMemoryStreamSink()
        await sink.close()

        with pytest.raises(RuntimeError, match="already closed"):
            await sink.close()

    @pytest.mark.asyncio
    async def test_empty_chunk_is_recorded(self) -> None:
        """An empty string chunk is still added to the buffer."""
        sink = InMemoryStreamSink()
        await sink.push("")
        assert sink.chunks == [""]

    @pytest.mark.asyncio
    async def test_chunks_preserve_order(self) -> None:
        """Chunks come out in push order."""
        sink = InMemoryStreamSink()
        for i in range(100):
            await sink.push(str(i))
        assert sink.chunks == [str(i) for i in range(100)]


# ---------------------------------------------------------------------------
# Permissions edge cases
# ---------------------------------------------------------------------------


class TestPermissionsEdgeCases:
    """Boundary conditions for the Permissions dataclass."""

    def test_empty_allowed_tools_denies_everything(self) -> None:
        """An empty frozenset means no tools are allowed."""
        perms = Permissions(allowed_tools=frozenset())
        assert not perms.can_use_tool("any_tool")

    def test_none_allowed_tools_permits_everything(self) -> None:
        """None means unrestricted — all tools allowed."""
        perms = Permissions(allowed_tools=None)
        assert perms.can_use_tool("any_tool")

    def test_empty_allowed_agents_denies_everything(self) -> None:
        """An empty frozenset means no agents are allowed."""
        perms = Permissions(allowed_agents=frozenset())
        assert not perms.can_use_agent("any_agent")

    def test_none_allowed_agents_permits_everything(self) -> None:
        """None means unrestricted — all agents allowed."""
        perms = Permissions(allowed_agents=None)
        assert perms.can_use_agent("any_agent")

    def test_has_role_with_empty_roles(self) -> None:
        """No roles assigned means has_role is always False."""
        perms = Permissions(roles=frozenset())
        assert not perms.has_role("admin")

    def test_specific_tool_in_allowlist(self) -> None:
        """Only the listed tool is permitted."""
        perms = Permissions(allowed_tools=frozenset({"read_file"}))
        assert perms.can_use_tool("read_file")
        assert not perms.can_use_tool("delete_file")


# ---------------------------------------------------------------------------
# HandlerCandidate / IntentResult validation
# ---------------------------------------------------------------------------


class TestRouterValueObjectEdgeCases:
    """Validation rules on router data classes."""

    def test_handler_candidate_score_below_zero_raises(self) -> None:
        """Score < 0.0 is rejected."""
        with pytest.raises(ValueError, match="score must be between"):
            HandlerCandidate(name="h", score=-0.1)

    def test_handler_candidate_score_above_one_raises(self) -> None:
        """Score > 1.0 is rejected."""
        with pytest.raises(ValueError, match="score must be between"):
            HandlerCandidate(name="h", score=1.01)

    def test_intent_result_confidence_below_zero_raises(self) -> None:
        """Confidence < 0.0 is rejected."""
        with pytest.raises(ValueError, match="confidence must be between"):
            IntentResult(intent="x", confidence=-0.01, handlers=[])

    def test_intent_result_confidence_above_one_raises(self) -> None:
        """Confidence > 1.0 is rejected."""
        with pytest.raises(ValueError, match="confidence must be between"):
            IntentResult(intent="x", confidence=1.1, handlers=[])

    def test_intent_result_boundary_values(self) -> None:
        """Exact boundary values 0.0 and 1.0 are accepted."""
        low = IntentResult(intent="x", confidence=0.0, handlers=[])
        high = IntentResult(intent="x", confidence=1.0, handlers=[])
        assert low.confidence == 0.0
        assert high.confidence == 1.0

    def test_best_handler_empty_list_returns_none(self) -> None:
        """best_handler is None when handlers list is empty."""
        result = IntentResult(intent="x", confidence=0.5, handlers=[])
        assert result.best_handler is None

    def test_best_handler_returns_first(self) -> None:
        """best_handler returns the first element."""
        h1 = HandlerCandidate(name="first", score=0.9)
        h2 = HandlerCandidate(name="second", score=0.5)
        result = IntentResult(intent="x", confidence=0.9, handlers=[h1, h2])
        assert result.best_handler is h1
