"""Tests for Phase 2 strategy modules (N-610 through N-615).

Covers LLMRouter, InProcessRuntime, ContainerRuntime (unit-level),
CompositeToolManager, ToneResponder, and MultimodalResponder.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from nerva.context import ExecContext, InMemoryStreamSink, Permissions
from nerva.responder import API_CHANNEL, WEBSOCKET_CHANNEL, Channel, Response
from nerva.responder.multimodal import (
    AudioBlock,
    ButtonBlock,
    CardBlock,
    ContentType,
    ImageBlock,
    MultimodalResponder,
    TextBlock,
)
from nerva.responder.tone import ToneConfig, ToneResponder
from nerva.router import HandlerCandidate, IntentResult
from nerva.router.llm import (
    DEFAULT_SYSTEM_PROMPT,
    LLM_INTENT,
    NO_MATCH_CONFIDENCE,
    NO_MATCH_INTENT,
    LLMRouter,
    LLMRouterConfig,
    _build_user_prompt,
    _extract_confidence,
    _try_parse_json,
    _regex_extract_json,
)
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.circuit_breaker import CircuitBreakerConfig
from nerva.runtime.container import (
    ContainerConfig,
    ContainerHandlerConfig,
    ContainerRuntime,
    _build_docker_command,
)
from nerva.runtime.inprocess import InProcessConfig, InProcessRuntime
from nerva.tools import ToolManager, ToolResult, ToolSpec, ToolStatus
from nerva.tools.composite import CompositeToolManager

from tests.conftest import make_ctx


# ===========================================================================
# Helpers
# ===========================================================================


def _fake_llm(response: str):
    """Return a fake LLM function that always returns *response*.

    Args:
        response: Fixed response string.

    Returns:
        Async callable matching the LLMFunc protocol.
    """
    async def _llm(system_prompt: str, user_prompt: str) -> str:
        """Return a fixed response."""
        return response
    return _llm


def _recording_llm(response: str):
    """Return a fake LLM that records its calls and returns *response*.

    Args:
        response: Fixed response string.

    Returns:
        Tuple of (llm_func, call_records_list).
    """
    calls: list[tuple[str, str]] = []

    async def _llm(system_prompt: str, user_prompt: str) -> str:
        """Record calls and return fixed response."""
        calls.append((system_prompt, user_prompt))
        return response
    return _llm, calls


def _raising_llm(exc: Exception):
    """Return a fake LLM that raises the given exception.

    Args:
        exc: Exception to raise.

    Returns:
        Async callable that always raises.
    """
    async def _llm(system_prompt: str, user_prompt: str) -> str:
        """Always raise."""
        raise exc
    return _llm


class FakeToolManager:
    """In-memory ToolManager for testing CompositeToolManager.

    Args:
        tools: List of ToolSpecs this manager owns.
        call_result: Result returned by call().
    """

    def __init__(
        self,
        tools: list[ToolSpec],
        call_result: ToolResult | None = None,
    ) -> None:
        self._tools = tools
        self._call_result = call_result or ToolResult(status=ToolStatus.SUCCESS, output="ok")

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Return preconfigured tools."""
        return list(self._tools)

    async def call(self, tool: str, args: dict[str, object], ctx: ExecContext) -> ToolResult:
        """Return preconfigured result."""
        return self._call_result


class FailingToolManager:
    """A ToolManager whose discover() always raises."""

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Always raise."""
        raise RuntimeError("discovery failed")

    async def call(self, tool: str, args: dict[str, object], ctx: ExecContext) -> ToolResult:
        """Always raise."""
        raise RuntimeError("call failed")


# ===========================================================================
# N-610: LLMRouter
# ===========================================================================


class TestLLMRouterRegistration:
    """Registration validation for LLMRouter."""

    @pytest.mark.asyncio
    async def test_register_empty_name_raises(self) -> None:
        """Empty handler name raises ValueError."""
        router = LLMRouter(_fake_llm("{}"))
        with pytest.raises(ValueError, match="name"):
            await router.register("", "some description")

    @pytest.mark.asyncio
    async def test_register_blank_description_raises(self) -> None:
        """Whitespace-only description raises ValueError."""
        router = LLMRouter(_fake_llm("{}"))
        with pytest.raises(ValueError, match="description"):
            await router.register("handler", "   ")

    @pytest.mark.asyncio
    async def test_register_duplicate_raises(self) -> None:
        """Registering the same name twice raises ValueError."""
        router = LLMRouter(_fake_llm("{}"))
        await router.register("h1", "handler one")
        with pytest.raises(ValueError, match="already registered"):
            await router.register("h1", "other description")

    @pytest.mark.asyncio
    async def test_register_none_description_raises(self) -> None:
        """None-like empty description raises ValueError."""
        router = LLMRouter(_fake_llm("{}"))
        with pytest.raises(ValueError, match="description"):
            await router.register("h1", "")


class TestLLMRouterClassify:
    """Classification tests for LLMRouter."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_empty_message_returns_unknown(self, ctx: ExecContext) -> None:
        """Empty message returns unknown intent."""
        router = LLMRouter(_fake_llm('{"handler": "h1", "confidence": 0.9}'))
        await router.register("h1", "handler one")
        result = await router.classify("", ctx)
        assert result.intent == NO_MATCH_INTENT
        assert result.confidence == NO_MATCH_CONFIDENCE

    @pytest.mark.asyncio
    async def test_whitespace_message_returns_unknown(self, ctx: ExecContext) -> None:
        """Whitespace-only message returns unknown intent."""
        router = LLMRouter(_fake_llm('{"handler": "h1", "confidence": 0.9}'))
        await router.register("h1", "handler one")
        result = await router.classify("   \t\n  ", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_no_handlers_returns_unknown(self, ctx: ExecContext) -> None:
        """No registered handlers returns unknown intent."""
        router = LLMRouter(_fake_llm('{"handler": "h1", "confidence": 0.9}'))
        result = await router.classify("hello", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_valid_llm_response(self, ctx: ExecContext) -> None:
        """Valid JSON response produces a correct IntentResult."""
        response = '{"handler": "search", "confidence": 0.95}'
        router = LLMRouter(_fake_llm(response))
        await router.register("search", "find documents")
        result = await router.classify("find cats", ctx)
        assert result.intent == LLM_INTENT
        assert result.confidence == pytest.approx(0.95)
        assert result.handlers[0].name == "search"

    @pytest.mark.asyncio
    async def test_llm_response_with_noise(self, ctx: ExecContext) -> None:
        """JSON embedded in noisy text is still parsed."""
        response = 'Sure! Here is my answer: {"handler": "calc", "confidence": 0.8} Hope that helps!'
        router = LLMRouter(_fake_llm(response))
        await router.register("calc", "do math")
        result = await router.classify("add 2+2", ctx)
        assert result.intent == LLM_INTENT
        assert result.handlers[0].name == "calc"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_unknown(self, ctx: ExecContext) -> None:
        """Completely unparseable LLM output returns unknown intent."""
        router = LLMRouter(_fake_llm("I don't know what to do"))
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_unknown_handler_in_response(self, ctx: ExecContext) -> None:
        """LLM selecting an unregistered handler returns unknown."""
        response = '{"handler": "nonexistent", "confidence": 0.9}'
        router = LLMRouter(_fake_llm(response))
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_empty_handler_in_response(self, ctx: ExecContext) -> None:
        """LLM returning empty handler name returns unknown."""
        response = '{"handler": "", "confidence": 0.0}'
        router = LLMRouter(_fake_llm(response))
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_missing_confidence_uses_fallback(self, ctx: ExecContext) -> None:
        """Missing confidence field uses the configured fallback."""
        response = '{"handler": "h1"}'
        config = LLMRouterConfig(fallback_confidence=0.75)
        router = LLMRouter(_fake_llm(response), config=config)
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.confidence == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_one(self, ctx: ExecContext) -> None:
        """Confidence > 1.0 is clamped to 1.0."""
        response = '{"handler": "h1", "confidence": 5.0}'
        router = LLMRouter(_fake_llm(response))
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.confidence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_negative_confidence_clamped_to_zero(self, ctx: ExecContext) -> None:
        """Negative confidence is clamped to 0.0."""
        response = '{"handler": "h1", "confidence": -0.5}'
        router = LLMRouter(_fake_llm(response))
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.confidence == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self, ctx: ExecContext) -> None:
        """Custom system prompt is forwarded to the LLM."""
        custom_prompt = "You are a test classifier."
        llm, calls = _recording_llm('{"handler": "h1", "confidence": 0.9}')
        config = LLMRouterConfig(system_prompt=custom_prompt)
        router = LLMRouter(llm, config=config)
        await router.register("h1", "handler one")
        await router.classify("hello", ctx)
        assert calls[0][0] == custom_prompt

    @pytest.mark.asyncio
    async def test_user_prompt_contains_catalog(self, ctx: ExecContext) -> None:
        """User prompt sent to LLM contains handler names and descriptions."""
        llm, calls = _recording_llm('{"handler": "search", "confidence": 0.9}')
        router = LLMRouter(llm)
        await router.register("search", "find documents")
        await router.register("calc", "do math")
        await router.classify("hello", ctx)
        user_prompt = calls[0][1]
        assert "search" in user_prompt
        assert "find documents" in user_prompt
        assert "calc" in user_prompt
        assert "do math" in user_prompt


class TestLLMRouterPureHelpers:
    """Tests for pure helper functions in the LLM router module."""

    def test_try_parse_json_valid(self) -> None:
        """Valid JSON string parses correctly."""
        assert _try_parse_json('{"key": "value"}') == {"key": "value"}

    def test_try_parse_json_invalid(self) -> None:
        """Invalid JSON returns None."""
        assert _try_parse_json("not json") is None

    def test_try_parse_json_empty(self) -> None:
        """Empty string returns None."""
        assert _try_parse_json("") is None

    def test_try_parse_json_array(self) -> None:
        """JSON array returns None (we need an object)."""
        assert _try_parse_json("[1, 2, 3]") is None

    def test_regex_extract_json_embedded(self) -> None:
        """Regex extracts JSON from surrounding text."""
        result = _regex_extract_json('prefix {"a": 1} suffix')
        assert result == {"a": 1}

    def test_regex_extract_json_no_json(self) -> None:
        """No JSON in text returns None."""
        assert _regex_extract_json("no json here") is None

    def test_extract_confidence_present(self) -> None:
        """Extract numeric confidence from parsed dict."""
        assert _extract_confidence({"confidence": 0.8}, 0.5) == pytest.approx(0.8)

    def test_extract_confidence_missing(self) -> None:
        """Missing confidence uses fallback."""
        assert _extract_confidence({}, 0.5) == pytest.approx(0.5)

    def test_extract_confidence_non_numeric(self) -> None:
        """Non-numeric confidence uses fallback."""
        assert _extract_confidence({"confidence": "high"}, 0.5) == pytest.approx(0.5)

    def test_extract_confidence_clamps_high(self) -> None:
        """Confidence > 1.0 is clamped."""
        assert _extract_confidence({"confidence": 99}, 0.5) == pytest.approx(1.0)


# ===========================================================================
# N-611: InProcessRuntime
# ===========================================================================


class TestInProcessRegistration:
    """Registration validation for InProcessRuntime."""

    def test_register_empty_name_raises(self) -> None:
        """Empty handler name raises ValueError."""
        runtime = InProcessRuntime()
        async def handler(inp: AgentInput, ctx: ExecContext) -> str:
            return "ok"
        with pytest.raises(ValueError, match="name"):
            runtime.register("", handler)

    def test_register_duplicate_raises(self) -> None:
        """Registering same name twice raises ValueError."""
        runtime = InProcessRuntime()
        async def handler(inp: AgentInput, ctx: ExecContext) -> str:
            return "ok"
        runtime.register("h1", handler)
        with pytest.raises(ValueError, match="already registered"):
            runtime.register("h1", handler)

    def test_register_sync_function_raises(self) -> None:
        """Sync function raises TypeError."""
        runtime = InProcessRuntime()
        def handler(inp: AgentInput, ctx: ExecContext) -> str:
            return "ok"
        with pytest.raises(TypeError, match="async"):
            runtime.register("h1", handler)


class TestInProcessInvoke:
    """Invocation tests for InProcessRuntime."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_invoke_success(self, ctx: ExecContext) -> None:
        """Successful handler returns SUCCESS status."""
        runtime = InProcessRuntime()

        async def handler(inp: AgentInput, ctx: ExecContext) -> str:
            return f"echo: {inp.message}"

        runtime.register("echo", handler)
        result = await runtime.invoke("echo", AgentInput(message="hello"), ctx)
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "echo: hello"
        assert result.handler == "echo"

    @pytest.mark.asyncio
    async def test_invoke_unknown_handler(self, ctx: ExecContext) -> None:
        """Invoking an unregistered handler returns ERROR."""
        runtime = InProcessRuntime()
        result = await runtime.invoke("nonexistent", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.ERROR
        assert "not registered" in result.error

    @pytest.mark.asyncio
    async def test_invoke_handler_raises(self, ctx: ExecContext) -> None:
        """Handler that raises returns ERROR status."""
        runtime = InProcessRuntime()

        async def handler(inp: AgentInput, ctx: ExecContext) -> str:
            raise ValueError("broken")

        runtime.register("broken", handler)
        result = await runtime.invoke("broken", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.ERROR
        assert "ValueError" in result.error

    @pytest.mark.asyncio
    async def test_invoke_timeout(self, ctx: ExecContext) -> None:
        """Handler exceeding timeout returns TIMEOUT status."""
        config = InProcessConfig(timeout_seconds=0.05)
        runtime = InProcessRuntime(config=config)

        async def slow_handler(inp: AgentInput, ctx: ExecContext) -> str:
            await asyncio.sleep(10)
            return "never"

        runtime.register("slow", slow_handler)
        result = await runtime.invoke("slow", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_invoke_returns_none(self, ctx: ExecContext) -> None:
        """Handler returning None produces empty output string."""
        runtime = InProcessRuntime()

        async def handler(inp: AgentInput, ctx: ExecContext) -> None:
            return None

        runtime.register("nil", handler)
        result = await runtime.invoke("nil", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.SUCCESS
        assert result.output == ""

    @pytest.mark.asyncio
    async def test_streaming_handler(self, ctx: ExecContext) -> None:
        """Async generator handler streams chunks to ctx.stream."""
        stream = InMemoryStreamSink()
        ctx.stream = stream
        runtime = InProcessRuntime()

        async def streamer(inp: AgentInput, ctx: ExecContext):
            yield "chunk1"
            yield "chunk2"
            yield "chunk3"

        runtime.register("stream", streamer)
        result = await runtime.invoke("stream", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "chunk1chunk2chunk3"
        assert stream.chunks == ["chunk1", "chunk2", "chunk3"]

    @pytest.mark.asyncio
    async def test_streaming_without_stream_sink(self, ctx: ExecContext) -> None:
        """Streaming handler works even without a stream sink."""
        runtime = InProcessRuntime()

        async def streamer(inp: AgentInput, ctx: ExecContext):
            yield "a"
            yield "b"

        runtime.register("stream", streamer)
        result = await runtime.invoke("stream", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "ab"

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens(self, ctx: ExecContext) -> None:
        """Circuit breaker opens after repeated failures."""
        config = InProcessConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2, recovery_seconds=300)
        )
        runtime = InProcessRuntime(config=config)

        async def failing(inp: AgentInput, ctx: ExecContext) -> str:
            raise RuntimeError("fail")

        runtime.register("fail", failing)

        # Trip the breaker
        await runtime.invoke("fail", AgentInput(message="1"), ctx)
        await runtime.invoke("fail", AgentInput(message="2"), ctx)

        # Third call should be rejected by the breaker
        result = await runtime.invoke("fail", AgentInput(message="3"), ctx)
        assert result.status == AgentStatus.ERROR
        assert "circuit open" in result.error

    @pytest.mark.asyncio
    async def test_invoke_chain(self, ctx: ExecContext) -> None:
        """invoke_chain pipes output through handlers in sequence."""
        runtime = InProcessRuntime()

        async def upper(inp: AgentInput, ctx: ExecContext) -> str:
            return inp.message.upper()

        async def exclaim(inp: AgentInput, ctx: ExecContext) -> str:
            return f"{inp.message}!"

        runtime.register("upper", upper)
        runtime.register("exclaim", exclaim)

        result = await runtime.invoke_chain(
            ["upper", "exclaim"], AgentInput(message="hello"), ctx
        )
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "HELLO!"

    @pytest.mark.asyncio
    async def test_invoke_chain_empty_raises(self, ctx: ExecContext) -> None:
        """Empty handler list raises ValueError."""
        runtime = InProcessRuntime()
        with pytest.raises(ValueError, match="empty"):
            await runtime.invoke_chain([], AgentInput(message="hi"), ctx)

    @pytest.mark.asyncio
    async def test_invoke_chain_stops_on_error(self, ctx: ExecContext) -> None:
        """Chain stops when a handler returns non-SUCCESS."""
        runtime = InProcessRuntime()

        async def failing(inp: AgentInput, ctx: ExecContext) -> str:
            raise RuntimeError("boom")

        async def never_called(inp: AgentInput, ctx: ExecContext) -> str:
            return "should not reach"

        runtime.register("fail", failing)
        runtime.register("never", never_called)

        result = await runtime.invoke_chain(
            ["fail", "never"], AgentInput(message="hi"), ctx
        )
        assert result.status == AgentStatus.ERROR

    @pytest.mark.asyncio
    async def test_delegate(self, ctx: ExecContext) -> None:
        """delegate creates a child context and invokes the handler."""
        runtime = InProcessRuntime()

        async def handler(inp: AgentInput, ctx: ExecContext) -> str:
            return f"delegated: {inp.message}"

        runtime.register("child", handler)
        result = await runtime.delegate("child", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.SUCCESS
        assert "delegated" in result.output


# ===========================================================================
# N-612: ContainerRuntime (unit-level, no Docker)
# ===========================================================================


class TestContainerRegistration:
    """Registration validation for ContainerRuntime."""

    def test_register_empty_name_raises(self) -> None:
        """Empty handler name raises ValueError."""
        runtime = ContainerRuntime()
        with pytest.raises(ValueError, match="name"):
            runtime.register("", ContainerHandlerConfig(image="test:latest"))

    def test_register_duplicate_raises(self) -> None:
        """Duplicate name raises ValueError."""
        runtime = ContainerRuntime()
        runtime.register("h1", ContainerHandlerConfig(image="test:latest"))
        with pytest.raises(ValueError, match="already registered"):
            runtime.register("h1", ContainerHandlerConfig(image="test:latest"))

    def test_register_empty_image_raises(self) -> None:
        """Empty image raises ValueError."""
        runtime = ContainerRuntime()
        with pytest.raises(ValueError, match="image"):
            runtime.register("h1", ContainerHandlerConfig(image=""))

    def test_register_blank_image_raises(self) -> None:
        """Whitespace-only image raises ValueError."""
        runtime = ContainerRuntime()
        with pytest.raises(ValueError, match="image"):
            runtime.register("h1", ContainerHandlerConfig(image="   "))


class TestContainerDockerCommand:
    """Tests for the docker command builder."""

    def test_default_command(self) -> None:
        """Default config produces expected docker run command."""
        cfg = ContainerHandlerConfig(image="myapp:latest")
        cmd = _build_docker_command("docker", cfg)
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd
        assert "-i" in cmd
        assert "--memory=256m" in cmd
        assert "--cpus=1.0" in cmd
        assert "--network=none" in cmd
        assert cmd[-1] == "myapp:latest"

    def test_custom_limits(self) -> None:
        """Custom resource limits appear in the command."""
        cfg = ContainerHandlerConfig(
            image="myapp:v2",
            memory_limit="512m",
            cpu_limit="2.0",
            network_mode="bridge",
        )
        cmd = _build_docker_command("docker", cfg)
        assert "--memory=512m" in cmd
        assert "--cpus=2.0" in cmd
        assert "--network=bridge" in cmd

    def test_env_vars(self) -> None:
        """Environment variables are passed as -e flags."""
        cfg = ContainerHandlerConfig(
            image="myapp:latest",
            env={"API_KEY": "secret", "MODE": "test"},
        )
        cmd = _build_docker_command("docker", cfg)
        assert "-e" in cmd
        assert "API_KEY=secret" in cmd
        assert "MODE=test" in cmd


class TestContainerInvoke:
    """Invocation tests for ContainerRuntime (without actual Docker)."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_invoke_unknown_handler(self, ctx: ExecContext) -> None:
        """Invoking an unregistered handler returns ERROR."""
        runtime = ContainerRuntime()
        result = await runtime.invoke("nonexistent", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.ERROR
        assert "not registered" in result.error

    @pytest.mark.asyncio
    async def test_invoke_chain_empty_raises(self, ctx: ExecContext) -> None:
        """Empty handler list raises ValueError."""
        runtime = ContainerRuntime()
        with pytest.raises(ValueError, match="empty"):
            await runtime.invoke_chain([], AgentInput(message="hi"), ctx)

    @pytest.mark.asyncio
    async def test_circuit_breaker_on_unregistered(self, ctx: ExecContext) -> None:
        """Unregistered handler returns error without touching breaker."""
        config = ContainerConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1)
        )
        runtime = ContainerRuntime(config=config)
        result = await runtime.invoke("missing", AgentInput(message="hi"), ctx)
        assert result.status == AgentStatus.ERROR


# ===========================================================================
# N-613: CompositeToolManager
# ===========================================================================


class TestCompositeToolManager:
    """Tests for CompositeToolManager."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_discover_merges_tools(self, ctx: ExecContext) -> None:
        """discover() merges tools from all managers."""
        mgr1 = FakeToolManager([ToolSpec(name="tool_a", description="A")])
        mgr2 = FakeToolManager([ToolSpec(name="tool_b", description="B")])
        composite = CompositeToolManager([mgr1, mgr2])
        specs = await composite.discover(ctx)
        names = {s.name for s in specs}
        assert names == {"tool_a", "tool_b"}

    @pytest.mark.asyncio
    async def test_discover_deduplicates_by_name(self, ctx: ExecContext) -> None:
        """First manager wins when tool names collide."""
        mgr1 = FakeToolManager(
            [ToolSpec(name="shared", description="from mgr1")],
            ToolResult(status=ToolStatus.SUCCESS, output="mgr1_result"),
        )
        mgr2 = FakeToolManager(
            [ToolSpec(name="shared", description="from mgr2")],
            ToolResult(status=ToolStatus.SUCCESS, output="mgr2_result"),
        )
        composite = CompositeToolManager([mgr1, mgr2])
        specs = await composite.discover(ctx)
        assert len(specs) == 1
        assert specs[0].description == "from mgr1"

    @pytest.mark.asyncio
    async def test_call_routes_to_owner(self, ctx: ExecContext) -> None:
        """call() routes to the correct owning manager."""
        result_a = ToolResult(status=ToolStatus.SUCCESS, output="from_a")
        result_b = ToolResult(status=ToolStatus.SUCCESS, output="from_b")
        mgr1 = FakeToolManager([ToolSpec(name="tool_a", description="A")], result_a)
        mgr2 = FakeToolManager([ToolSpec(name="tool_b", description="B")], result_b)
        composite = CompositeToolManager([mgr1, mgr2])
        await composite.discover(ctx)

        res_a = await composite.call("tool_a", {}, ctx)
        assert res_a.output == "from_a"
        res_b = await composite.call("tool_b", {}, ctx)
        assert res_b.output == "from_b"

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, ctx: ExecContext) -> None:
        """Calling an unknown tool returns NOT_FOUND."""
        composite = CompositeToolManager([])
        result = await composite.call("nope", {}, ctx)
        assert result.status == ToolStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_discover_empty_managers(self, ctx: ExecContext) -> None:
        """No managers produces empty tool list."""
        composite = CompositeToolManager([])
        specs = await composite.discover(ctx)
        assert specs == []

    @pytest.mark.asyncio
    async def test_discover_tolerates_failing_manager(self, ctx: ExecContext) -> None:
        """A failing manager is skipped, others still work."""
        good = FakeToolManager([ToolSpec(name="tool_ok", description="ok")])
        bad = FailingToolManager()
        composite = CompositeToolManager([bad, good])
        specs = await composite.discover(ctx)
        assert len(specs) == 1
        assert specs[0].name == "tool_ok"

    @pytest.mark.asyncio
    async def test_priority_order_first_wins(self, ctx: ExecContext) -> None:
        """First manager in the list has priority for duplicate names."""
        result_priority = ToolResult(status=ToolStatus.SUCCESS, output="priority")
        result_secondary = ToolResult(status=ToolStatus.SUCCESS, output="secondary")
        mgr1 = FakeToolManager(
            [ToolSpec(name="dupe", description="priority")], result_priority
        )
        mgr2 = FakeToolManager(
            [ToolSpec(name="dupe", description="secondary")], result_secondary
        )
        composite = CompositeToolManager([mgr1, mgr2])
        await composite.discover(ctx)
        result = await composite.call("dupe", {}, ctx)
        assert result.output == "priority"


# ===========================================================================
# N-614: ToneResponder
# ===========================================================================


class TestToneResponder:
    """Tests for ToneResponder."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_rewrites_text_output(self, ctx: ExecContext) -> None:
        """Successful output is rewritten through the LLM."""
        async def fake_llm(system: str, user: str) -> str:
            return f"[rewritten] {user}"

        responder = ToneResponder(fake_llm)
        output = AgentResult(status=AgentStatus.SUCCESS, output="hello world")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert "[rewritten]" in response.text
        assert "hello world" in response.text

    @pytest.mark.asyncio
    async def test_passthrough_on_error(self, ctx: ExecContext) -> None:
        """Error status passes through without LLM call."""
        llm, calls = _recording_llm("should not be called")
        responder = ToneResponder(llm)
        output = AgentResult(
            status=AgentStatus.ERROR, output="", error="something broke"
        )
        response = await responder.format(output, API_CHANNEL, ctx)
        assert len(calls) == 0
        assert response.text == "something broke"

    @pytest.mark.asyncio
    async def test_passthrough_on_empty_output(self, ctx: ExecContext) -> None:
        """Empty output passes through without LLM call."""
        llm, calls = _recording_llm("should not be called")
        responder = ToneResponder(llm)
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_custom_tone(self, ctx: ExecContext) -> None:
        """Custom tone is included in the system prompt."""
        llm, calls = _recording_llm("rewritten")
        config = ToneConfig(tone="casual")
        responder = ToneResponder(llm, config=config)
        output = AgentResult(status=AgentStatus.SUCCESS, output="hello")
        await responder.format(output, API_CHANNEL, ctx)
        assert "casual" in calls[0][0]

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self, ctx: ExecContext) -> None:
        """LLM exception falls back to original text."""
        responder = ToneResponder(_raising_llm(RuntimeError("LLM down")))
        output = AgentResult(status=AgentStatus.SUCCESS, output="original text")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert response.text == "original text"

    @pytest.mark.asyncio
    async def test_llm_returns_empty_falls_back(self, ctx: ExecContext) -> None:
        """LLM returning empty string falls back to original text."""
        responder = ToneResponder(_fake_llm(""))
        output = AgentResult(status=AgentStatus.SUCCESS, output="original text")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert response.text == "original text"

    @pytest.mark.asyncio
    async def test_max_length_truncation(self, ctx: ExecContext) -> None:
        """Response is truncated to channel.max_length."""
        channel = Channel(name="sms", max_length=5)
        responder = ToneResponder(_fake_llm("this is a long rewritten response"))
        output = AgentResult(status=AgentStatus.SUCCESS, output="hello")
        response = await responder.format(output, channel, ctx)
        assert len(response.text) <= 5

    @pytest.mark.asyncio
    async def test_whitespace_only_output_passes_through(self, ctx: ExecContext) -> None:
        """Whitespace-only output is treated as empty and passes through."""
        llm, calls = _recording_llm("should not be called")
        responder = ToneResponder(llm)
        output = AgentResult(status=AgentStatus.SUCCESS, output="   \t\n  ")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert len(calls) == 0


# ===========================================================================
# N-615: MultimodalResponder
# ===========================================================================


class TestMultimodalContentBlocks:
    """Tests for content block dataclasses."""

    def test_text_block_type(self) -> None:
        """TextBlock has ContentType.TEXT."""
        block = TextBlock(content="hello")
        assert block.type == ContentType.TEXT

    def test_image_block_type(self) -> None:
        """ImageBlock has ContentType.IMAGE."""
        block = ImageBlock(url="https://img.example.com/pic.png")
        assert block.type == ContentType.IMAGE

    def test_card_block_type(self) -> None:
        """CardBlock has ContentType.CARD."""
        block = CardBlock(title="Title", body="Body")
        assert block.type == ContentType.CARD

    def test_button_block_type(self) -> None:
        """ButtonBlock has ContentType.BUTTON."""
        block = ButtonBlock(label="Click", action_url="https://example.com")
        assert block.type == ContentType.BUTTON

    def test_audio_block_type(self) -> None:
        """AudioBlock has ContentType.AUDIO."""
        block = AudioBlock(url="https://audio.example.com/clip.mp3")
        assert block.type == ContentType.AUDIO

    def test_blocks_are_frozen(self) -> None:
        """Content blocks are immutable."""
        block = TextBlock(content="hello")
        with pytest.raises(AttributeError):
            block.content = "changed"  # type: ignore[misc]


class TestMultimodalResponder:
    """Tests for MultimodalResponder."""

    @pytest.fixture
    def ctx(self) -> ExecContext:
        """Default execution context."""
        return make_ctx()

    @pytest.mark.asyncio
    async def test_default_text_from_output(self, ctx: ExecContext) -> None:
        """Without set_blocks, wraps agent output in a TextBlock."""
        responder = MultimodalResponder()
        output = AgentResult(status=AgentStatus.SUCCESS, output="hello world")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert response.text == "hello world"

    @pytest.mark.asyncio
    async def test_empty_output(self, ctx: ExecContext) -> None:
        """Empty output produces empty response."""
        responder = MultimodalResponder()
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert response.text == ""

    @pytest.mark.asyncio
    async def test_custom_blocks(self, ctx: ExecContext) -> None:
        """Custom blocks are rendered into text."""
        responder = MultimodalResponder()
        responder.set_blocks([
            TextBlock(content="Hello"),
            TextBlock(content="World"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="ignored")
        response = await responder.format(output, API_CHANNEL, ctx)
        assert "Hello" in response.text
        assert "World" in response.text

    @pytest.mark.asyncio
    async def test_image_block_media_channel(self, ctx: ExecContext) -> None:
        """Image blocks provide media URLs on media-capable channels."""
        responder = MultimodalResponder()
        responder.set_blocks([
            ImageBlock(url="https://img.example.com/pic.png", alt_text="A picture"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        # WEBSOCKET_CHANNEL supports media
        response = await responder.format(output, WEBSOCKET_CHANNEL, ctx)
        assert "https://img.example.com/pic.png" in response.media

    @pytest.mark.asyncio
    async def test_image_block_degrades_on_no_media(self, ctx: ExecContext) -> None:
        """Image blocks degrade to alt text on channels without media."""
        no_media_channel = Channel(name="cli", supports_media=False)
        responder = MultimodalResponder()
        responder.set_blocks([
            ImageBlock(url="https://img.example.com/pic.png", alt_text="A picture"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, no_media_channel, ctx)
        assert "A picture" in response.text
        assert response.media == []

    @pytest.mark.asyncio
    async def test_button_degrades_to_text_link(self, ctx: ExecContext) -> None:
        """Buttons degrade to text links on non-media channels."""
        no_media_channel = Channel(name="cli", supports_media=False)
        responder = MultimodalResponder()
        responder.set_blocks([
            ButtonBlock(label="Click me", action_url="https://example.com"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, no_media_channel, ctx)
        assert "Click me" in response.text
        assert "https://example.com" in response.text

    @pytest.mark.asyncio
    async def test_card_degrades_to_text(self, ctx: ExecContext) -> None:
        """Cards degrade to text on non-media channels."""
        no_media_channel = Channel(name="cli", supports_media=False)
        responder = MultimodalResponder()
        responder.set_blocks([
            CardBlock(title="News", body="Something happened"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, no_media_channel, ctx)
        assert "News" in response.text
        assert "Something happened" in response.text

    @pytest.mark.asyncio
    async def test_audio_degrades_on_no_media(self, ctx: ExecContext) -> None:
        """Audio blocks degrade to fallback label on non-media channels."""
        no_media_channel = Channel(name="cli", supports_media=False)
        responder = MultimodalResponder()
        responder.set_blocks([
            AudioBlock(url="https://audio.example.com/clip.mp3"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, no_media_channel, ctx)
        assert "[audio]" in response.text

    @pytest.mark.asyncio
    async def test_metadata_contains_block_info(self, ctx: ExecContext) -> None:
        """Metadata includes block count and types."""
        responder = MultimodalResponder()
        responder.set_blocks([
            TextBlock(content="Hello"),
            ImageBlock(url="https://img.example.com/pic.png"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, WEBSOCKET_CHANNEL, ctx)
        assert response.metadata["block_count"] == "2"
        assert "text" in response.metadata["block_types"]
        assert "image" in response.metadata["block_types"]

    @pytest.mark.asyncio
    async def test_max_length_truncation(self, ctx: ExecContext) -> None:
        """Response text is truncated to channel.max_length."""
        channel = Channel(name="sms", max_length=5)
        responder = MultimodalResponder()
        responder.set_blocks([TextBlock(content="this is a very long message")])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, channel, ctx)
        assert len(response.text) <= 5

    @pytest.mark.asyncio
    async def test_mixed_blocks(self, ctx: ExecContext) -> None:
        """Mixed block types are all rendered."""
        responder = MultimodalResponder()
        responder.set_blocks([
            TextBlock(content="Hello"),
            ImageBlock(url="https://img.example.com/pic.png", alt_text="pic"),
            ButtonBlock(label="Go", action_url="https://example.com"),
            CardBlock(title="Card", body="Content"),
            AudioBlock(url="https://audio.example.com/clip.mp3"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, WEBSOCKET_CHANNEL, ctx)
        assert "Hello" in response.text
        assert response.metadata["block_count"] == "5"

    @pytest.mark.asyncio
    async def test_card_with_image_extracts_media(self, ctx: ExecContext) -> None:
        """CardBlock with image_url includes it in media list."""
        responder = MultimodalResponder()
        responder.set_blocks([
            CardBlock(title="Card", body="Content", image_url="https://img.example.com/card.png"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, WEBSOCKET_CHANNEL, ctx)
        assert "https://img.example.com/card.png" in response.media

    @pytest.mark.asyncio
    async def test_error_output_fallback(self, ctx: ExecContext) -> None:
        """Error agent result renders the error text."""
        responder = MultimodalResponder()
        output = AgentResult(
            status=AgentStatus.ERROR, output="", error="something broke"
        )
        response = await responder.format(output, API_CHANNEL, ctx)
        assert "something broke" in response.text

    @pytest.mark.asyncio
    async def test_image_no_alt_text_uses_label(self, ctx: ExecContext) -> None:
        """Image without alt_text uses fallback label on degraded channel."""
        no_media_channel = Channel(name="cli", supports_media=False)
        responder = MultimodalResponder()
        responder.set_blocks([
            ImageBlock(url="https://img.example.com/pic.png"),
        ])
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        response = await responder.format(output, no_media_channel, ctx)
        assert "[image]" in response.text
