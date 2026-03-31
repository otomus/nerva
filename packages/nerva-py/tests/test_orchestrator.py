"""Tests for Orchestrator — full pipeline, middleware, streaming, and edge cases."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from nerva.context import ExecContext, InMemoryStreamSink
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.orchestrator import (
    FALLBACK_HANDLER,
    MiddlewareStage,
    Orchestrator,
    PolicyDeniedError,
)
from nerva.policy import PolicyAction, PolicyDecision
from nerva.responder import API_CHANNEL, Channel, Response
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.router import HandlerCandidate, IntentResult

if TYPE_CHECKING:
    pass

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Mock primitives
# ---------------------------------------------------------------------------


class MockRouter:
    """Returns a pre-configured IntentResult."""

    def __init__(
        self,
        handler_name: str = "test_handler",
        confidence: float = 0.9,
    ) -> None:
        self._handler_name = handler_name
        self._confidence = confidence
        self.classify_calls: list[str] = []

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Record the call and return a fixed result."""
        self.classify_calls.append(message)
        candidate = HandlerCandidate(
            name=self._handler_name,
            score=self._confidence,
            reason="mock",
        )
        return IntentResult(
            intent="test_intent",
            confidence=self._confidence,
            handlers=[candidate],
        )


class EmptyRouter:
    """Returns an IntentResult with no handler candidates."""

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Return an empty result — no handlers matched."""
        return IntentResult(intent="unknown", confidence=0.0, handlers=[])


class MockRuntime:
    """Returns a pre-configured AgentResult."""

    def __init__(
        self,
        output: str = "test output",
        status: AgentStatus = AgentStatus.SUCCESS,
    ) -> None:
        self._output = output
        self._status = status
        self.invoke_calls: list[tuple[str, AgentInput]] = []

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Record the call and return a fixed result."""
        self.invoke_calls.append((handler, input))
        return AgentResult(
            status=self._status,
            output=self._output,
            handler=handler,
        )

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Chain invocation — returns result from last handler."""
        result = AgentResult(status=self._status, output=self._output)
        for h in handlers:
            result = await self.invoke(h, input, ctx)
        return result

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Delegate — same as invoke for mock purposes."""
        return await self.invoke(handler, input, parent_ctx)


class MockResponder:
    """Returns a Response with the agent output text."""

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Wrap the agent output in a Response."""
        return Response(text=output.output, channel=channel)


class DenyAllPolicy:
    """Denies every action."""

    def __init__(self, reason: str = "denied by test policy") -> None:
        self._reason = reason
        self.evaluated: list[PolicyAction] = []
        self.recorded: list[tuple[PolicyAction, PolicyDecision]] = []

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Always deny."""
        self.evaluated.append(action)
        return PolicyDecision(allowed=False, reason=self._reason)

    async def record(
        self, action: PolicyAction, decision: PolicyDecision, ctx: ExecContext
    ) -> None:
        """Record the decision for assertions."""
        self.recorded.append((action, decision))


class AllowAllPolicy:
    """Allows every action — for testing that policy is wired but not blocking."""

    def __init__(self) -> None:
        self.evaluated: list[PolicyAction] = []
        self.recorded: list[tuple[PolicyAction, PolicyDecision]] = []

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Always allow."""
        self.evaluated.append(action)
        return PolicyDecision(allowed=True)

    async def record(
        self, action: PolicyAction, decision: PolicyDecision, ctx: ExecContext
    ) -> None:
        """Record the decision for assertions."""
        self.recorded.append((action, decision))


class MockMemory:
    """In-memory implementation for testing recall/store lifecycle."""

    def __init__(
        self,
        conversation: list[dict[str, str]] | None = None,
    ) -> None:
        self._conversation = conversation or []
        self.recall_calls: list[str] = []
        self.store_calls: list[MemoryEvent] = []

    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        """Return pre-configured conversation history."""
        self.recall_calls.append(query)
        return MemoryContext(conversation=list(self._conversation))

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        """Record stored events for assertions."""
        self.store_calls.append(event)

    async def consolidate(self, ctx: ExecContext) -> None:
        """No-op consolidation."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_orchestrator(
    *,
    router: object | None = None,
    runtime: object | None = None,
    responder: object | None = None,
    memory: object | None = None,
    policy: object | None = None,
) -> Orchestrator:
    """Build an Orchestrator with sensible mock defaults."""
    return Orchestrator(
        router=router or MockRouter(),
        runtime=runtime or MockRuntime(),
        responder=responder or MockResponder(),
        memory=memory,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestOrchestratorHappyPath:
    """Verify the golden path through handle()."""

    @pytest.mark.asyncio
    async def test_handle_returns_response_with_correct_text(self) -> None:
        """handle() wires router->runtime->responder and returns a Response."""
        orch = _build_orchestrator(runtime=MockRuntime(output="hello world"))
        resp = await orch.handle("greet me")

        assert isinstance(resp, Response)
        assert resp.text == "hello world"
        assert resp.channel == API_CHANNEL

    @pytest.mark.asyncio
    async def test_handle_creates_ctx_when_none_provided(self) -> None:
        """When no ctx is passed, handle() creates one internally."""
        router = MockRouter()
        orch = _build_orchestrator(router=router)

        await orch.handle("hi")

        # The router was called, so routing happened — context was created.
        assert len(router.classify_calls) == 1

    @pytest.mark.asyncio
    async def test_handle_uses_provided_ctx(self) -> None:
        """When a ctx is provided, handle() uses it (not a new one)."""
        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)
        ctx = make_ctx(user_id="explicit-user")

        await orch.handle("hi", ctx=ctx)

        # Runtime received a call — verify the handler was invoked with
        # the expected handler name from MockRouter.
        assert len(runtime.invoke_calls) == 1
        handler_name, _ = runtime.invoke_calls[0]
        assert handler_name == "test_handler"

    @pytest.mark.asyncio
    async def test_handle_uses_custom_channel(self) -> None:
        """handle() passes the channel through to the responder."""
        slack = Channel(name="slack", supports_markdown=True, max_length=4000)
        orch = _build_orchestrator()

        resp = await orch.handle("hi", channel=slack)

        assert resp.channel == slack

    @pytest.mark.asyncio
    async def test_handle_defaults_channel_to_api(self) -> None:
        """When no channel is specified, API_CHANNEL is used."""
        orch = _build_orchestrator()
        resp = await orch.handle("hi")
        assert resp.channel == API_CHANNEL


# ---------------------------------------------------------------------------
# Memory integration
# ---------------------------------------------------------------------------


class TestOrchestratorMemory:
    """Verify memory recall and store lifecycle."""

    @pytest.mark.asyncio
    async def test_recall_is_called_with_message(self) -> None:
        """Memory.recall() receives the user message."""
        memory = MockMemory()
        orch = _build_orchestrator(memory=memory)

        await orch.handle("what is the weather?")

        assert memory.recall_calls == ["what is the weather?"]

    @pytest.mark.asyncio
    async def test_store_is_called_on_success(self) -> None:
        """Memory.store() is called when the agent succeeds."""
        memory = MockMemory()
        orch = _build_orchestrator(
            runtime=MockRuntime(output="sunny", status=AgentStatus.SUCCESS),
            memory=memory,
        )

        await orch.handle("weather?")

        assert len(memory.store_calls) == 1
        stored = memory.store_calls[0]
        assert stored.content == "sunny"
        assert stored.tier == MemoryTier.HOT

    @pytest.mark.asyncio
    async def test_store_is_skipped_on_error(self) -> None:
        """Memory.store() is NOT called when the agent errors."""
        memory = MockMemory()
        orch = _build_orchestrator(
            runtime=MockRuntime(output="oops", status=AgentStatus.ERROR),
            memory=memory,
        )

        await orch.handle("fail please")

        assert len(memory.store_calls) == 0

    @pytest.mark.asyncio
    async def test_memory_context_flows_into_agent_input_history(self) -> None:
        """Recalled conversation history populates AgentInput.history."""
        history = [{"role": "user", "content": "earlier question"}]
        memory = MockMemory(conversation=history)
        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime, memory=memory)

        await orch.handle("follow up")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.history == history

    @pytest.mark.asyncio
    async def test_no_memory_gives_empty_history(self) -> None:
        """Without memory, AgentInput.history is empty."""
        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)

        await orch.handle("hi")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.history == []


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestOrchestratorPolicy:
    """Verify policy enforcement in the pipeline."""

    @pytest.mark.asyncio
    async def test_policy_denial_raises_error(self) -> None:
        """DenyAllPolicy raises PolicyDeniedError before routing."""
        policy = DenyAllPolicy(reason="budget exceeded")
        orch = _build_orchestrator(policy=policy)

        with pytest.raises(PolicyDeniedError) as exc_info:
            await orch.handle("do something expensive")

        assert exc_info.value.decision.reason == "budget exceeded"
        assert not exc_info.value.decision.allowed

    @pytest.mark.asyncio
    async def test_policy_denial_records_action(self) -> None:
        """Policy.record() is called even on denial."""
        policy = DenyAllPolicy()
        orch = _build_orchestrator(policy=policy)

        with pytest.raises(PolicyDeniedError):
            await orch.handle("blocked")

        assert len(policy.recorded) == 1
        action, decision = policy.recorded[0]
        assert action.kind == "route"
        assert not decision.allowed

    @pytest.mark.asyncio
    async def test_allow_policy_evaluates_both_route_and_invoke(self) -> None:
        """AllowAllPolicy is called for both route and invoke actions."""
        policy = AllowAllPolicy()
        orch = _build_orchestrator(policy=policy)

        await orch.handle("hello")

        kinds = [a.kind for a in policy.evaluated]
        assert "route" in kinds
        assert "invoke_agent" in kinds

    @pytest.mark.asyncio
    async def test_no_policy_does_not_raise(self) -> None:
        """Without policy, handle() completes normally."""
        orch = _build_orchestrator(policy=None)
        resp = await orch.handle("hi")
        assert resp.text == "test output"


# ---------------------------------------------------------------------------
# Router fallback
# ---------------------------------------------------------------------------


class TestOrchestratorRouterFallback:
    """Verify behaviour when the router returns no handlers."""

    @pytest.mark.asyncio
    async def test_empty_router_result_uses_fallback_handler(self) -> None:
        """When router returns no candidates, FALLBACK_HANDLER is used."""
        runtime = MockRuntime()
        orch = _build_orchestrator(router=EmptyRouter(), runtime=runtime)

        await orch.handle("nonsense gibberish")

        assert len(runtime.invoke_calls) == 1
        handler_name, _ = runtime.invoke_calls[0]
        assert handler_name == FALLBACK_HANDLER

    @pytest.mark.asyncio
    async def test_fallback_still_produces_response(self) -> None:
        """Even with fallback handler, a valid Response is returned."""
        orch = _build_orchestrator(router=EmptyRouter())
        resp = await orch.handle("??")
        assert isinstance(resp, Response)
        assert resp.text == "test output"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TestOrchestratorMiddleware:
    """Verify middleware hook points and execution semantics."""

    @pytest.mark.asyncio
    async def test_before_route_is_called(self) -> None:
        """BEFORE_ROUTE middleware fires with the message as payload."""
        payloads: list[object] = []

        async def mw(ctx: ExecContext, payload: object) -> object | None:
            payloads.append(payload)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, mw)

        await orch.handle("hello")

        assert len(payloads) == 1
        assert payloads[0] == "hello"

    @pytest.mark.asyncio
    async def test_before_invoke_is_called(self) -> None:
        """BEFORE_INVOKE middleware fires with AgentInput as payload."""
        payloads: list[object] = []

        async def mw(ctx: ExecContext, payload: object) -> object | None:
            payloads.append(payload)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_INVOKE, mw)

        await orch.handle("hello")

        assert len(payloads) == 1
        assert isinstance(payloads[0], AgentInput)

    @pytest.mark.asyncio
    async def test_after_invoke_is_called(self) -> None:
        """AFTER_INVOKE middleware fires with AgentResult as payload."""
        payloads: list[object] = []

        async def mw(ctx: ExecContext, payload: object) -> object | None:
            payloads.append(payload)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.AFTER_INVOKE, mw)

        await orch.handle("hello")

        assert len(payloads) == 1
        assert isinstance(payloads[0], AgentResult)

    @pytest.mark.asyncio
    async def test_before_respond_is_called(self) -> None:
        """BEFORE_RESPOND middleware fires with Response as payload."""
        payloads: list[object] = []

        async def mw(ctx: ExecContext, payload: object) -> object | None:
            payloads.append(payload)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_RESPOND, mw)

        await orch.handle("hello")

        assert len(payloads) == 1
        assert isinstance(payloads[0], Response)

    @pytest.mark.asyncio
    async def test_middleware_execution_order(self) -> None:
        """Middleware stages fire in order: route -> invoke -> after_invoke -> respond."""
        order: list[str] = []

        async def make_mw(label: str):
            async def mw(ctx: ExecContext, payload: object) -> object | None:
                order.append(label)
                return None
            return mw

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, await make_mw("before_route"))
        orch.use(MiddlewareStage.BEFORE_INVOKE, await make_mw("before_invoke"))
        orch.use(MiddlewareStage.AFTER_INVOKE, await make_mw("after_invoke"))
        orch.use(MiddlewareStage.BEFORE_RESPOND, await make_mw("before_respond"))

        await orch.handle("hello")

        assert order == [
            "before_route",
            "before_invoke",
            "after_invoke",
            "before_respond",
        ]

    @pytest.mark.asyncio
    async def test_middleware_can_replace_payload(self) -> None:
        """When middleware returns a value, it replaces the payload."""
        replacement = AgentInput(message="replaced", history=[])

        async def replace_mw(ctx: ExecContext, payload: object) -> object | None:
            return replacement

        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)
        orch.use(MiddlewareStage.BEFORE_INVOKE, replace_mw)

        await orch.handle("original")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.message == "replaced"

    @pytest.mark.asyncio
    async def test_middleware_returning_none_keeps_payload(self) -> None:
        """When middleware returns None, the original payload is kept."""
        async def noop_mw(ctx: ExecContext, payload: object) -> object | None:
            return None

        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)
        orch.use(MiddlewareStage.BEFORE_INVOKE, noop_mw)

        await orch.handle("original")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.message == "original"

    @pytest.mark.asyncio
    async def test_multiple_middleware_chain(self) -> None:
        """Multiple middleware on the same stage run in registration order."""
        calls: list[int] = []

        async def mw1(ctx: ExecContext, payload: object) -> object | None:
            calls.append(1)
            return None

        async def mw2(ctx: ExecContext, payload: object) -> object | None:
            calls.append(2)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, mw1)
        orch.use(MiddlewareStage.BEFORE_ROUTE, mw2)

        await orch.handle("hi")

        assert calls == [1, 2]

    @pytest.mark.asyncio
    async def test_middleware_replacement_chains(self) -> None:
        """Second middleware sees the replacement from the first."""
        async def first(ctx: ExecContext, payload: object) -> object | None:
            return "modified_by_first"

        seen: list[object] = []

        async def second(ctx: ExecContext, payload: object) -> object | None:
            seen.append(payload)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, first)
        orch.use(MiddlewareStage.BEFORE_ROUTE, second)

        await orch.handle("original")

        assert seen == ["modified_by_first"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestOrchestratorStream:
    """Verify stream() yields chunks from InMemoryStreamSink."""

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self) -> None:
        """stream() yields chunks that the runtime pushes to the sink."""

        class StreamingRuntime:
            """Pushes chunks to the context's stream sink during invoke."""

            async def invoke(
                self, handler: str, input: AgentInput, ctx: ExecContext
            ) -> AgentResult:
                if ctx.stream is not None:
                    await ctx.stream.push("chunk1")
                    await ctx.stream.push("chunk2")
                return AgentResult(
                    status=AgentStatus.SUCCESS,
                    output="chunk1chunk2",
                    handler=handler,
                )

            async def invoke_chain(self, handlers, input, ctx):
                return await self.invoke(handlers[0], input, ctx)

            async def delegate(self, handler, input, parent_ctx):
                return await self.invoke(handler, input, parent_ctx)

        orch = _build_orchestrator(runtime=StreamingRuntime())

        chunks: list[str] = []
        async for chunk in orch.stream("hello"):
            chunks.append(chunk)

        assert "chunk1" in chunks
        assert "chunk2" in chunks

    @pytest.mark.asyncio
    async def test_stream_sets_sink_on_context(self) -> None:
        """stream() attaches an InMemoryStreamSink to the context."""
        seen_stream: list[object] = []

        class InspectingRuntime:
            async def invoke(self, handler, input, ctx):
                seen_stream.append(ctx.stream)
                return AgentResult(
                    status=AgentStatus.SUCCESS, output="ok", handler=handler
                )

            async def invoke_chain(self, handlers, input, ctx):
                return await self.invoke(handlers[0], input, ctx)

            async def delegate(self, handler, input, parent_ctx):
                return await self.invoke(handler, input, parent_ctx)

        orch = _build_orchestrator(runtime=InspectingRuntime())

        async for _ in orch.stream("hi"):
            pass

        assert len(seen_stream) == 1
        assert isinstance(seen_stream[0], InMemoryStreamSink)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestOrchestratorEdgeCases:
    """Stress the pipeline with unusual inputs."""

    @pytest.mark.asyncio
    async def test_empty_message(self) -> None:
        """An empty message still flows through the pipeline."""
        orch = _build_orchestrator()
        resp = await orch.handle("")
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_none_channel_defaults_to_api(self) -> None:
        """Passing channel=None explicitly defaults to API_CHANNEL."""
        orch = _build_orchestrator()
        resp = await orch.handle("hi", channel=None)
        assert resp.channel == API_CHANNEL

    @pytest.mark.asyncio
    async def test_whitespace_only_message(self) -> None:
        """Whitespace-only message goes through without crashing."""
        orch = _build_orchestrator()
        resp = await orch.handle("   \t\n  ")
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_very_long_message(self) -> None:
        """A 10k character message does not blow up."""
        long_msg = "x" * 10_000
        router = MockRouter()
        orch = _build_orchestrator(router=router)

        resp = await orch.handle(long_msg)

        assert isinstance(resp, Response)
        assert router.classify_calls == [long_msg]

    @pytest.mark.asyncio
    async def test_unicode_message(self) -> None:
        """Unicode and special characters pass through cleanly."""
        msg = "Hola! \u2764\ufe0f \U0001f680 \u00e4\u00f6\u00fc\u00df \u4f60\u597d \n\t\x00"
        orch = _build_orchestrator()
        resp = await orch.handle(msg)
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_policy_denied_error_attributes(self) -> None:
        """PolicyDeniedError exposes the decision object."""
        decision = PolicyDecision(
            allowed=False,
            reason="rate limited",
            budget_remaining=0.0,
        )
        err = PolicyDeniedError(decision)
        assert err.decision is decision
        assert "rate limited" in str(err)

    @pytest.mark.asyncio
    async def test_policy_denied_error_no_reason(self) -> None:
        """PolicyDeniedError with reason=None uses default message."""
        decision = PolicyDecision(allowed=False, reason=None)
        err = PolicyDeniedError(decision)
        assert "denied by policy" in str(err)

    @pytest.mark.asyncio
    async def test_use_registers_middleware(self) -> None:
        """use() appends middleware to the correct stage."""
        orch = _build_orchestrator()

        called = False

        async def mw(ctx, payload):
            nonlocal called
            called = True
            return None

        orch.use(MiddlewareStage.BEFORE_ROUTE, mw)
        await orch.handle("trigger")
        assert called
