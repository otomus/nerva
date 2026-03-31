"""Tests for delegation — depth limiting, permission checks, token accumulation, edge cases."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext, Permissions, TokenUsage
from nerva.orchestrator import (
    DEFAULT_MAX_DELEGATION_DEPTH,
    DELEGATION_DEPTH_EXCEEDED_TEMPLATE,
    Orchestrator,
)
from nerva.responder import Channel, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.runtime import AgentInput, AgentResult, AgentStatus

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Mock primitives
# ---------------------------------------------------------------------------


class MockRouter:
    """Returns a fixed handler candidate."""

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Return a single catch-all handler."""
        candidate = HandlerCandidate(name="catch-all", score=0.9, reason="mock")
        return IntentResult(intent="test", confidence=0.9, handlers=[candidate])


class DelegationRuntime:
    """Runtime that records invocations and returns scripted results.

    Optionally records token usage on the child context so tests can
    verify accumulation back to the parent.
    """

    def __init__(
        self,
        output: str = "delegated result",
        token_usage: TokenUsage | None = None,
    ) -> None:
        self._output = output
        self._token_usage = token_usage
        self.invoke_calls: list[tuple[str, AgentInput, ExecContext]] = []

    async def invoke(
        self, handler: str, agent_input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Record the call, optionally set token usage, and return a fixed result."""
        self.invoke_calls.append((handler, agent_input, ctx))
        if self._token_usage is not None:
            ctx.record_tokens(self._token_usage)
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=self._output,
            handler=handler,
        )

    async def invoke_chain(
        self, handlers: list[str], agent_input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Chain invocation — returns result from last handler."""
        result = AgentResult(status=AgentStatus.ERROR, error="no handlers ran")
        for h in handlers:
            result = await self.invoke(h, agent_input, ctx)
        return result

    async def delegate(
        self, handler: str, agent_input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Delegate — same as invoke for mock purposes."""
        return await self.invoke(handler, agent_input, parent_ctx)


class MockResponder:
    """Wraps the agent output in a Response."""

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Return the output text as a Response."""
        return Response(text=output.output, channel=channel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_orchestrator(
    *,
    runtime: object | None = None,
    max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
) -> Orchestrator:
    """Build an Orchestrator wired for delegation testing."""
    return Orchestrator(
        router=MockRouter(),
        runtime=runtime or DelegationRuntime(),
        responder=MockResponder(),
        max_delegation_depth=max_delegation_depth,
    )


# ---------------------------------------------------------------------------
# Delegation chain A -> B -> C (3 levels)
# ---------------------------------------------------------------------------


class TestDelegationChain:
    """Verify multi-level delegation chains work correctly."""

    @pytest.mark.asyncio
    async def test_three_level_chain(self) -> None:
        """A delegates to B, B delegates to C — all succeed."""
        results: list[str] = []

        class ChainRuntime:
            """Runtime that simulates chained delegation via the orchestrator."""

            def __init__(self, orchestrator: Orchestrator) -> None:
                self._orch = orchestrator

            async def invoke(
                self, handler: str, agent_input: AgentInput, ctx: ExecContext
            ) -> AgentResult:
                results.append(handler)
                if handler == "A":
                    return await self._orch.delegate("B", "from A", ctx)
                if handler == "B":
                    return await self._orch.delegate("C", "from B", ctx)
                return AgentResult(
                    status=AgentStatus.SUCCESS,
                    output=f"final from {handler}",
                    handler=handler,
                )

            async def invoke_chain(self, handlers, inp, ctx):
                return await self.invoke(handlers[0], inp, ctx)

            async def delegate(self, handler, inp, parent_ctx):
                return await self.invoke(handler, inp, parent_ctx)

        orch = _build_orchestrator()
        chain_runtime = ChainRuntime(orch)
        # Replace the runtime after construction
        orch._runtime = chain_runtime

        ctx = make_ctx()
        result = await orch.delegate("A", "start", ctx)

        assert result.status == AgentStatus.SUCCESS
        assert result.output == "final from C"
        assert results == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_chain_preserves_trace_id(self) -> None:
        """All contexts in a chain share the same trace_id."""
        trace_ids: list[str] = []

        class TraceRuntime:
            def __init__(self, orchestrator: Orchestrator) -> None:
                self._orch = orchestrator

            async def invoke(
                self, handler: str, agent_input: AgentInput, ctx: ExecContext
            ) -> AgentResult:
                trace_ids.append(ctx.trace_id)
                if handler == "A":
                    return await self._orch.delegate("B", "msg", ctx)
                return AgentResult(
                    status=AgentStatus.SUCCESS, output="done", handler=handler
                )

            async def invoke_chain(self, handlers, inp, ctx):
                return await self.invoke(handlers[0], inp, ctx)

            async def delegate(self, handler, inp, parent_ctx):
                return await self.invoke(handler, inp, parent_ctx)

        orch = _build_orchestrator()
        orch._runtime = TraceRuntime(orch)

        ctx = make_ctx()
        await orch.delegate("A", "start", ctx)

        assert len(trace_ids) == 2
        assert trace_ids[0] == trace_ids[1] == ctx.trace_id


# ---------------------------------------------------------------------------
# Depth limiting (N-631)
# ---------------------------------------------------------------------------


class TestDelegationDepthLimit:
    """Verify depth limiting at configurable thresholds."""

    @pytest.mark.asyncio
    async def test_exceeds_default_depth(self) -> None:
        """Delegation at depth > DEFAULT_MAX_DELEGATION_DEPTH returns error."""
        orch = _build_orchestrator()
        ctx = make_ctx()
        # Manually set depth to the max so next child() exceeds it
        ctx.depth = DEFAULT_MAX_DELEGATION_DEPTH

        result = await orch.delegate("deep_handler", "msg", ctx)

        assert result.status == AgentStatus.ERROR
        expected_msg = DELEGATION_DEPTH_EXCEEDED_TEMPLATE.format(
            n=DEFAULT_MAX_DELEGATION_DEPTH
        )
        assert result.error == expected_msg

    @pytest.mark.asyncio
    async def test_custom_depth_limit(self) -> None:
        """Custom max_delegation_depth of 2 blocks at depth 3."""
        orch = _build_orchestrator(max_delegation_depth=2)
        ctx = make_ctx()
        ctx.depth = 2

        result = await orch.delegate("handler", "msg", ctx)

        assert result.status == AgentStatus.ERROR
        assert "max: 2" in (result.error or "")

    @pytest.mark.asyncio
    async def test_at_exact_limit_succeeds(self) -> None:
        """Delegation exactly at max_depth (child depth == max) succeeds."""
        orch = _build_orchestrator(max_delegation_depth=3)
        ctx = make_ctx()
        ctx.depth = 2  # child will be 3, which equals max — allowed

        result = await orch.delegate("handler", "msg", ctx)

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_depth_zero_succeeds(self) -> None:
        """Root context (depth=0) can delegate without issue."""
        orch = _build_orchestrator()
        ctx = make_ctx()

        result = await orch.delegate("handler", "msg", ctx)

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_depth_limit_event_recorded(self) -> None:
        """When depth is exceeded, an event is recorded on the parent context."""
        orch = _build_orchestrator(max_delegation_depth=1)
        ctx = make_ctx()
        ctx.depth = 1

        await orch.delegate("handler", "msg", ctx)

        event_names = [e.name for e in ctx.events]
        assert "delegation.depth_exceeded" in event_names


# ---------------------------------------------------------------------------
# Permission denied
# ---------------------------------------------------------------------------


class TestDelegationPermissions:
    """Verify permission checks on delegation."""

    @pytest.mark.asyncio
    async def test_denied_when_agent_not_in_allowlist(self) -> None:
        """If allowed_agents does not include the target, delegation is denied."""
        ctx = make_ctx(allowed_agents=frozenset({"other_handler"}))
        orch = _build_orchestrator()

        result = await orch.delegate("forbidden_handler", "msg", ctx)

        assert result.status == AgentStatus.ERROR
        assert "permission denied" in (result.error or "")

    @pytest.mark.asyncio
    async def test_allowed_when_agent_in_allowlist(self) -> None:
        """If allowed_agents includes the target, delegation proceeds."""
        ctx = make_ctx(allowed_agents=frozenset({"my_handler"}))
        orch = _build_orchestrator()

        result = await orch.delegate("my_handler", "msg", ctx)

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_allowed_when_no_restrictions(self) -> None:
        """If allowed_agents is None (unrestricted), all agents are permitted."""
        ctx = make_ctx(allowed_agents=None)
        orch = _build_orchestrator()

        result = await orch.delegate("any_handler", "msg", ctx)

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_denied_when_empty_allowlist(self) -> None:
        """If allowed_agents is an empty set, nothing is permitted."""
        ctx = make_ctx(allowed_agents=frozenset())
        orch = _build_orchestrator()

        result = await orch.delegate("handler", "msg", ctx)

        assert result.status == AgentStatus.ERROR

    @pytest.mark.asyncio
    async def test_permission_denied_event_recorded(self) -> None:
        """When permission is denied, an event is recorded."""
        ctx = make_ctx(allowed_agents=frozenset({"other"}))
        orch = _build_orchestrator()

        await orch.delegate("blocked", "msg", ctx)

        event_names = [e.name for e in ctx.events]
        assert "delegation.denied" in event_names


# ---------------------------------------------------------------------------
# Token accumulation
# ---------------------------------------------------------------------------


class TestDelegationTokenAccumulation:
    """Verify child token usage flows back to the parent context."""

    @pytest.mark.asyncio
    async def test_tokens_accumulate_to_parent(self) -> None:
        """Token usage from child context is added to parent after delegation."""
        child_tokens = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
        )
        runtime = DelegationRuntime(token_usage=child_tokens)
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        await orch.delegate("handler", "msg", ctx)

        assert ctx.token_usage.prompt_tokens == 100
        assert ctx.token_usage.completion_tokens == 50
        assert ctx.token_usage.total_tokens == 150
        assert ctx.token_usage.cost_usd == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_multiple_delegations_accumulate(self) -> None:
        """Two delegations sum their token usage on the parent."""
        child_tokens = TokenUsage(
            prompt_tokens=50,
            completion_tokens=25,
            total_tokens=75,
            cost_usd=0.005,
        )
        runtime = DelegationRuntime(token_usage=child_tokens)
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        await orch.delegate("handler_a", "msg1", ctx)
        await orch.delegate("handler_b", "msg2", ctx)

        assert ctx.token_usage.prompt_tokens == 100
        assert ctx.token_usage.total_tokens == 150
        assert ctx.token_usage.cost_usd == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_no_tokens_when_runtime_does_not_record(self) -> None:
        """When the runtime records no tokens, parent stays at zero."""
        runtime = DelegationRuntime(token_usage=None)
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        await orch.delegate("handler", "msg", ctx)

        assert ctx.token_usage.prompt_tokens == 0
        assert ctx.token_usage.total_tokens == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestDelegationEdgeCases:
    """Stress delegation with unusual inputs."""

    @pytest.mark.asyncio
    async def test_delegate_to_self(self) -> None:
        """Delegating to the same handler that invoked works (until depth)."""
        call_count = 0

        class SelfDelegatingRuntime:
            def __init__(self, orchestrator: Orchestrator) -> None:
                self._orch = orchestrator

            async def invoke(
                self, handler: str, agent_input: AgentInput, ctx: ExecContext
            ) -> AgentResult:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return await self._orch.delegate("self_handler", "again", ctx)
                return AgentResult(
                    status=AgentStatus.SUCCESS,
                    output="finally done",
                    handler=handler,
                )

            async def invoke_chain(self, handlers, inp, ctx):
                return await self.invoke(handlers[0], inp, ctx)

            async def delegate(self, handler, inp, parent_ctx):
                return await self.invoke(handler, inp, parent_ctx)

        orch = _build_orchestrator(max_delegation_depth=5)
        orch._runtime = SelfDelegatingRuntime(orch)
        ctx = make_ctx()

        result = await orch.delegate("self_handler", "start", ctx)

        assert result.status == AgentStatus.SUCCESS
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_delegate_with_cancelled_context(self) -> None:
        """Delegation with a cancelled context still completes the invoke."""
        runtime = DelegationRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()
        ctx.cancelled.set()

        result = await orch.delegate("handler", "msg", ctx)

        # The runtime still runs — cancellation is advisory, not blocking.
        assert result.status == AgentStatus.SUCCESS
        assert len(runtime.invoke_calls) == 1

    @pytest.mark.asyncio
    async def test_delegate_with_empty_handler_name(self) -> None:
        """Empty handler name returns an error result immediately."""
        orch = _build_orchestrator()
        ctx = make_ctx()

        result = await orch.delegate("", "msg", ctx)

        assert result.status == AgentStatus.ERROR
        assert "must not be empty" in (result.error or "")

    @pytest.mark.asyncio
    async def test_delegate_with_empty_message(self) -> None:
        """Empty message is passed through to the runtime."""
        runtime = DelegationRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        result = await orch.delegate("handler", "", ctx)

        assert result.status == AgentStatus.SUCCESS
        _, agent_input, _ = runtime.invoke_calls[0]
        assert agent_input.message == ""

    @pytest.mark.asyncio
    async def test_delegate_with_unicode_message(self) -> None:
        """Unicode and special characters pass through delegation cleanly."""
        msg = "Hola! \u2764\ufe0f \U0001f680 \u00e4\u00f6\u00fc\u00df \u4f60\u597d"
        runtime = DelegationRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        result = await orch.delegate("handler", msg, ctx)

        assert result.status == AgentStatus.SUCCESS
        _, agent_input, _ = runtime.invoke_calls[0]
        assert agent_input.message == msg

    @pytest.mark.asyncio
    async def test_child_context_gets_incremented_depth(self) -> None:
        """The child context passed to runtime has depth = parent.depth + 1."""
        runtime = DelegationRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()
        ctx.depth = 2

        await orch.delegate("handler", "msg", ctx)

        _, _, child_ctx = runtime.invoke_calls[0]
        assert child_ctx.depth == 3

    @pytest.mark.asyncio
    async def test_child_context_has_fresh_request_id(self) -> None:
        """The child context has a different request_id from the parent."""
        runtime = DelegationRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx()

        await orch.delegate("handler", "msg", ctx)

        _, _, child_ctx = runtime.invoke_calls[0]
        assert child_ctx.request_id != ctx.request_id

    @pytest.mark.asyncio
    async def test_depth_limit_of_one(self) -> None:
        """max_delegation_depth=1 allows root->child but not child->grandchild."""
        orch = _build_orchestrator(max_delegation_depth=1)
        ctx = make_ctx()

        # Root (depth=0) -> child (depth=1) should succeed
        result = await orch.delegate("handler", "msg", ctx)
        assert result.status == AgentStatus.SUCCESS

        # Now if child (depth=1) tries to delegate -> grandchild (depth=2) should fail
        ctx.depth = 1
        result = await orch.delegate("handler", "msg", ctx)
        assert result.status == AgentStatus.ERROR
        assert "max: 1" in (result.error or "")
