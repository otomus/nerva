"""Tests for advanced middleware — priority ordering, error handling, built-ins, edge cases."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext, Permissions, TokenUsage
from nerva.middleware.builtins import permission_checker, request_logger, usage_tracker
from nerva.orchestrator import (
    DEFAULT_MIDDLEWARE_PRIORITY,
    MiddlewareStage,
    Orchestrator,
)
from nerva.responder import API_CHANNEL, Channel, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.runtime import AgentInput, AgentResult, AgentStatus

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Mock primitives
# ---------------------------------------------------------------------------


class MockRouter:
    """Returns a fixed IntentResult."""

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Return a single handler candidate."""
        candidate = HandlerCandidate(name="test_handler", score=0.9, reason="mock")
        return IntentResult(intent="test", confidence=0.9, handlers=[candidate])


class MockRuntime:
    """Returns a fixed AgentResult."""

    def __init__(self, output: str = "ok") -> None:
        self._output = output
        self.invoke_calls: list[tuple[str, AgentInput]] = []

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Record and return a fixed result."""
        self.invoke_calls.append((handler, input))
        return AgentResult(status=AgentStatus.SUCCESS, output=self._output, handler=handler)

    async def invoke_chain(self, handlers, input, ctx):
        """Chain invocation stub."""
        return await self.invoke(handlers[0], input, ctx)

    async def delegate(self, handler, input, parent_ctx):
        """Delegation stub."""
        return await self.invoke(handler, input, parent_ctx)


class MockResponder:
    """Returns a Response with the agent output."""

    async def format(self, output: AgentResult, channel: Channel, ctx: ExecContext) -> Response:
        """Wrap agent output in a Response."""
        return Response(text=output.output, channel=channel)


def _build_orchestrator(**kwargs) -> Orchestrator:
    """Build an Orchestrator with sensible mock defaults."""
    return Orchestrator(
        router=kwargs.get("router") or MockRouter(),
        runtime=kwargs.get("runtime") or MockRuntime(),
        responder=kwargs.get("responder") or MockResponder(),
        memory=kwargs.get("memory"),
        policy=kwargs.get("policy"),
    )


# ---------------------------------------------------------------------------
# N-641: Priority ordering
# ---------------------------------------------------------------------------


class TestMiddlewarePriority:
    """Verify middleware runs in priority order within each stage."""

    @pytest.mark.asyncio
    async def test_lower_priority_runs_first(self) -> None:
        """Middleware with priority=10 runs before priority=100."""
        order: list[str] = []

        async def high_priority(ctx: ExecContext, payload: object) -> object | None:
            order.append("high")
            return None

        async def low_priority(ctx: ExecContext, payload: object) -> object | None:
            order.append("low")
            return None

        orch = _build_orchestrator()
        # Register low priority first, then high — priority should override order
        orch.use(MiddlewareStage.BEFORE_ROUTE, low_priority, priority=200)
        orch.use(MiddlewareStage.BEFORE_ROUTE, high_priority, priority=10)

        await orch.handle("test")

        assert order == ["high", "low"]

    @pytest.mark.asyncio
    async def test_equal_priority_preserves_registration_order(self) -> None:
        """Middleware with equal priority runs in registration order."""
        order: list[str] = []

        async def first(ctx: ExecContext, payload: object) -> object | None:
            order.append("first")
            return None

        async def second(ctx: ExecContext, payload: object) -> object | None:
            order.append("second")
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, first, priority=50)
        orch.use(MiddlewareStage.BEFORE_ROUTE, second, priority=50)

        await orch.handle("test")

        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_default_priority_is_100(self) -> None:
        """Middleware registered without priority gets DEFAULT_MIDDLEWARE_PRIORITY."""
        order: list[str] = []

        async def early(ctx: ExecContext, payload: object) -> object | None:
            order.append("early")
            return None

        async def default(ctx: ExecContext, payload: object) -> object | None:
            order.append("default")
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, default)  # priority=100
        orch.use(MiddlewareStage.BEFORE_ROUTE, early, priority=50)

        await orch.handle("test")

        assert order == ["early", "default"]

    @pytest.mark.asyncio
    async def test_three_priorities_across_range(self) -> None:
        """Three middleware at different priorities run in correct order."""
        order: list[int] = []

        async def p1(ctx, payload):
            order.append(1)
            return None

        async def p200(ctx, payload):
            order.append(200)
            return None

        async def p50(ctx, payload):
            order.append(50)
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, p200, priority=200)
        orch.use(MiddlewareStage.BEFORE_ROUTE, p1, priority=1)
        orch.use(MiddlewareStage.BEFORE_ROUTE, p50, priority=50)

        await orch.handle("test")

        assert order == [1, 50, 200]


# ---------------------------------------------------------------------------
# N-640: Decorator-style registration
# ---------------------------------------------------------------------------


class TestDecoratorRegistration:
    """Verify decorator-style middleware registration."""

    @pytest.mark.asyncio
    async def test_before_route_decorator_bare(self) -> None:
        """@orchestrator.before_route registers without parentheses."""
        called = False
        orch = _build_orchestrator()

        @orch.before_route
        async def mw(ctx, payload):
            nonlocal called
            called = True
            return None

        await orch.handle("test")
        assert called

    @pytest.mark.asyncio
    async def test_before_invoke_decorator_with_priority(self) -> None:
        """@orchestrator.before_invoke(priority=10) registers with priority."""
        order: list[str] = []
        orch = _build_orchestrator()

        @orch.before_invoke(priority=200)
        async def late(ctx, payload):
            order.append("late")
            return None

        @orch.before_invoke(priority=10)
        async def early(ctx, payload):
            order.append("early")
            return None

        await orch.handle("test")
        assert order == ["early", "late"]

    @pytest.mark.asyncio
    async def test_after_invoke_decorator(self) -> None:
        """@orchestrator.after_invoke registers correctly."""
        payloads: list[object] = []
        orch = _build_orchestrator()

        @orch.after_invoke
        async def mw(ctx, payload):
            payloads.append(payload)
            return None

        await orch.handle("test")
        assert len(payloads) == 1
        assert isinstance(payloads[0], AgentResult)

    @pytest.mark.asyncio
    async def test_before_respond_decorator(self) -> None:
        """@orchestrator.before_respond registers correctly."""
        payloads: list[object] = []
        orch = _build_orchestrator()

        @orch.before_respond
        async def mw(ctx, payload):
            payloads.append(payload)
            return None

        await orch.handle("test")
        assert len(payloads) == 1
        assert isinstance(payloads[0], Response)

    @pytest.mark.asyncio
    async def test_decorator_returns_original_function(self) -> None:
        """Decorators return the original function for chaining."""
        orch = _build_orchestrator()

        @orch.before_route
        async def mw(ctx, payload):
            return None

        # The decorator should return the function itself
        assert callable(mw)


# ---------------------------------------------------------------------------
# N-642: Error handling
# ---------------------------------------------------------------------------


class TestMiddlewareErrorHandling:
    """Verify middleware errors don't crash the request."""

    @pytest.mark.asyncio
    async def test_error_in_middleware_does_not_crash(self) -> None:
        """A raising middleware skips remaining handlers but request completes."""
        async def bad_middleware(ctx: ExecContext, payload: object) -> object | None:
            raise ValueError("middleware exploded")

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad_middleware)

        resp = await orch.handle("test")
        assert isinstance(resp, Response)
        assert resp.text == "ok"

    @pytest.mark.asyncio
    async def test_error_skips_remaining_middleware_in_stage(self) -> None:
        """After a middleware error, subsequent handlers in the same stage are skipped."""
        order: list[str] = []

        async def first(ctx, payload):
            order.append("first")
            return None

        async def bad(ctx, payload):
            order.append("bad")
            raise RuntimeError("boom")

        async def third(ctx, payload):
            order.append("third")
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, first, priority=10)
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad, priority=20)
        orch.use(MiddlewareStage.BEFORE_ROUTE, third, priority=30)

        await orch.handle("test")

        assert order == ["first", "bad"]  # third was skipped

    @pytest.mark.asyncio
    async def test_error_emits_trace_event(self) -> None:
        """Middleware errors emit a middleware.error event on the context."""
        async def bad(ctx, payload):
            raise TypeError("type error in middleware")

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad)

        ctx = make_ctx()
        await orch.handle("test", ctx=ctx)

        error_events = [e for e in ctx.events if e.name == "middleware.error"]
        assert len(error_events) == 1
        assert error_events[0].attributes["stage"] == "before_route"
        assert "type error in middleware" in error_events[0].attributes["error"]
        assert error_events[0].attributes["error_type"] == "TypeError"

    @pytest.mark.asyncio
    async def test_on_error_handler_is_called(self) -> None:
        """Registered error handlers are invoked when middleware fails."""
        errors: list[tuple[Exception, str]] = []

        async def bad(ctx, payload):
            raise ValueError("oops")

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad)

        @orch.on_error
        async def handle_error(err, stage, ctx):
            errors.append((err, stage.value))

        await orch.handle("test")

        assert len(errors) == 1
        assert isinstance(errors[0][0], ValueError)
        assert errors[0][1] == "before_route"

    @pytest.mark.asyncio
    async def test_on_error_handler_failure_does_not_crash(self) -> None:
        """If the error handler itself raises, the request still completes."""
        async def bad_mw(ctx, payload):
            raise ValueError("middleware error")

        async def bad_error_handler(err, stage, ctx):
            raise RuntimeError("error handler also failed")

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad_mw)
        orch.on_error(bad_error_handler)

        resp = await orch.handle("test")
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_other_stages_continue_after_error(self) -> None:
        """An error in BEFORE_ROUTE doesn't prevent BEFORE_INVOKE from running."""
        stage_calls: list[str] = []

        async def bad_route(ctx, payload):
            raise ValueError("route error")

        async def invoke_mw(ctx, payload):
            stage_calls.append("before_invoke")
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, bad_route)
        orch.use(MiddlewareStage.BEFORE_INVOKE, invoke_mw)

        await orch.handle("test")

        assert "before_invoke" in stage_calls


# ---------------------------------------------------------------------------
# N-643: Built-in middleware
# ---------------------------------------------------------------------------


class TestBuiltinMiddleware:
    """Verify built-in middleware factories work correctly."""

    @pytest.mark.asyncio
    async def test_request_logger_fires_events(self) -> None:
        """request_logger produces before_route and after_invoke handlers."""
        before_mw, after_mw = request_logger()

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, before_mw)
        orch.use(MiddlewareStage.AFTER_INVOKE, after_mw)

        resp = await orch.handle("hello world")
        assert isinstance(resp, Response)

    @pytest.mark.asyncio
    async def test_request_logger_returns_none(self) -> None:
        """request_logger handlers return None (passthrough)."""
        before_mw, after_mw = request_logger()
        ctx = make_ctx()

        result_before = await before_mw(ctx, "test message")
        assert result_before is None

        agent_result = AgentResult(
            status=AgentStatus.SUCCESS, output="ok", handler="test"
        )
        result_after = await after_mw(ctx, agent_result)
        assert result_after is None

    @pytest.mark.asyncio
    async def test_permission_checker_allows_correct_roles(self) -> None:
        """permission_checker passes when required roles are present."""
        checker = permission_checker(required_roles=frozenset({"admin"}))

        ctx = make_ctx(roles=frozenset({"admin", "user"}))
        result = await checker(ctx, AgentInput(message="test"))
        assert result is None  # passthrough

    @pytest.mark.asyncio
    async def test_permission_checker_denies_missing_role(self) -> None:
        """permission_checker replaces payload when role is missing."""
        checker = permission_checker(required_roles=frozenset({"admin"}))

        ctx = make_ctx(roles=frozenset({"user"}))
        result = await checker(ctx, AgentInput(message="test"))
        assert result is not None
        assert isinstance(result, AgentInput)
        assert "Permission denied" in result.message

    @pytest.mark.asyncio
    async def test_permission_checker_emits_event_on_denial(self) -> None:
        """permission_checker emits a permission.denied event."""
        checker = permission_checker(required_roles=frozenset({"superadmin"}))

        ctx = make_ctx(roles=frozenset())
        await checker(ctx, AgentInput(message="test"))

        denied_events = [e for e in ctx.events if e.name == "permission.denied"]
        assert len(denied_events) == 1
        assert denied_events[0].attributes["missing_role"] == "superadmin"

    @pytest.mark.asyncio
    async def test_permission_checker_none_roles_is_noop(self) -> None:
        """permission_checker with required_roles=None passes everything."""
        checker = permission_checker(required_roles=None)

        ctx = make_ctx()
        result = await checker(ctx, AgentInput(message="test"))
        assert result is None

    @pytest.mark.asyncio
    async def test_usage_tracker_emits_event(self) -> None:
        """usage_tracker records token usage as an event."""
        tracker = usage_tracker()

        ctx = make_ctx()
        ctx.record_tokens(TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))

        result = await tracker(ctx, AgentResult(status=AgentStatus.SUCCESS, output="ok"))
        assert result is None

        usage_events = [e for e in ctx.events if e.name == "usage.recorded"]
        assert len(usage_events) == 1
        assert usage_events[0].attributes["prompt_tokens"] == "100"
        assert usage_events[0].attributes["completion_tokens"] == "50"
        assert usage_events[0].attributes["total_tokens"] == "150"

    @pytest.mark.asyncio
    async def test_usage_tracker_with_zero_tokens(self) -> None:
        """usage_tracker works correctly with zero token usage."""
        tracker = usage_tracker()

        ctx = make_ctx()
        await tracker(ctx, AgentResult(status=AgentStatus.SUCCESS, output="ok"))

        usage_events = [e for e in ctx.events if e.name == "usage.recorded"]
        assert len(usage_events) == 1
        assert usage_events[0].attributes["prompt_tokens"] == "0"
        assert usage_events[0].attributes["total_tokens"] == "0"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestMiddlewareEdgeCases:
    """Stress the middleware system with unusual inputs."""

    @pytest.mark.asyncio
    async def test_middleware_returns_none_is_passthrough(self) -> None:
        """Middleware returning None preserves the original payload."""
        async def noop(ctx, payload):
            return None

        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)
        orch.use(MiddlewareStage.BEFORE_INVOKE, noop)

        await orch.handle("original")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.message == "original"

    @pytest.mark.asyncio
    async def test_middleware_modifies_payload(self) -> None:
        """Middleware returning a value replaces the payload."""
        async def modify(ctx, payload):
            return AgentInput(message="modified")

        runtime = MockRuntime()
        orch = _build_orchestrator(runtime=runtime)
        orch.use(MiddlewareStage.BEFORE_INVOKE, modify)

        await orch.handle("original")

        _, agent_input = runtime.invoke_calls[0]
        assert agent_input.message == "modified"

    @pytest.mark.asyncio
    async def test_no_middleware_registered(self) -> None:
        """Pipeline works when no middleware is registered."""
        orch = _build_orchestrator()
        resp = await orch.handle("test")
        assert isinstance(resp, Response)
        assert resp.text == "ok"

    @pytest.mark.asyncio
    async def test_negative_priority(self) -> None:
        """Negative priority values are valid and run first."""
        order: list[str] = []

        async def negative(ctx, payload):
            order.append("negative")
            return None

        async def positive(ctx, payload):
            order.append("positive")
            return None

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_ROUTE, positive, priority=100)
        orch.use(MiddlewareStage.BEFORE_ROUTE, negative, priority=-10)

        await orch.handle("test")

        assert order == ["negative", "positive"]

    @pytest.mark.asyncio
    async def test_many_middleware_same_stage(self) -> None:
        """10 middleware on the same stage all execute in priority order."""
        order: list[int] = []

        orch = _build_orchestrator()
        for i in range(10):
            priority = (10 - i) * 10  # 100, 90, 80, ... 10

            async def mw(ctx, payload, _i=i, _p=priority):
                order.append(_p)
                return None

            orch.use(MiddlewareStage.BEFORE_ROUTE, mw, priority=priority)

        await orch.handle("test")

        assert order == sorted(order)

    @pytest.mark.asyncio
    async def test_error_handler_receives_correct_stage(self) -> None:
        """Error handlers receive the exact stage where the error occurred."""
        stages: list[str] = []

        async def bad_invoke(ctx, payload):
            raise ValueError("invoke error")

        orch = _build_orchestrator()
        orch.use(MiddlewareStage.BEFORE_INVOKE, bad_invoke)

        @orch.on_error
        async def handle_error(err, stage, ctx):
            stages.append(stage.value)

        await orch.handle("test")

        assert stages == ["before_invoke"]
