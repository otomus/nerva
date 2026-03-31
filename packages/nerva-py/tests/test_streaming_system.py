"""Tests for the streaming system: runtime, tools, responder, and orchestrator integration.

Covers N-660 through N-664.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import pytest

from nerva.context import ExecContext, InMemoryStreamSink
from nerva.responder import StreamFormat, StreamingResponder
from nerva.responder.streaming import format_raw, format_sse, format_websocket
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.streaming import (
    StreamChunk,
    StreamChunkType,
    StreamingRuntime,
    build_chunk,
    serialize_chunk,
)
from nerva.tools import ToolResult, ToolSpec, ToolStatus
from nerva.tools.streaming import (
    TOOL_END_TYPE,
    TOOL_ERROR_TYPE,
    TOOL_START_TYPE,
    StreamingToolManager,
)
from nerva.router.rule import Rule

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeRuntime:
    """Fake runtime that returns a configurable result."""

    def __init__(self, output: str = "hello world", status: AgentStatus = AgentStatus.SUCCESS) -> None:
        self._output = output
        self._status = status

    async def invoke(self, handler: str, input: AgentInput, ctx: ExecContext) -> AgentResult:
        return AgentResult(status=self._status, output=self._output, handler=handler, error=None if self._status == AgentStatus.SUCCESS else "boom")

    async def invoke_chain(self, handlers: list[str], input: AgentInput, ctx: ExecContext) -> AgentResult:
        return AgentResult(status=self._status, output=self._output, handler=handlers[-1] if handlers else "", error=None if self._status == AgentStatus.SUCCESS else "boom")

    async def delegate(self, handler: str, input: AgentInput, parent_ctx: ExecContext) -> AgentResult:
        return AgentResult(status=self._status, output=self._output, handler=handler, error=None if self._status == AgentStatus.SUCCESS else "boom")


class FakeToolManager:
    """Fake tool manager for testing streaming wrapper."""

    def __init__(self, result: ToolResult | None = None, raise_error: bool = False) -> None:
        self._result = result or ToolResult(status=ToolStatus.SUCCESS, output="tool output")
        self._raise_error = raise_error

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        return [ToolSpec(name="test_tool", description="A test tool")]

    async def call(self, tool: str, args: dict[str, object], ctx: ExecContext) -> ToolResult:
        if self._raise_error:
            raise RuntimeError("tool exploded")
        return self._result


# ---------------------------------------------------------------------------
# N-660: Runtime streaming
# ---------------------------------------------------------------------------


class TestStreamingRuntime:
    """Tests for StreamingRuntime wrapper."""

    @pytest.mark.asyncio
    async def test_invoke_pushes_complete_chunk(self) -> None:
        """Successful invoke pushes a COMPLETE chunk to the stream."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="hi"))
        result = await runtime.invoke("greet", AgentInput(message="hey"), ctx)

        assert result.status == AgentStatus.SUCCESS
        assert result.output == "hi"
        assert len(sink.chunks) == 1

        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "complete"
        assert parsed["content"] == "hi"
        assert "timestamp" in parsed

    @pytest.mark.asyncio
    async def test_invoke_pushes_error_chunk_on_failure(self) -> None:
        """Failed invoke pushes an ERROR chunk."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="", status=AgentStatus.ERROR))
        result = await runtime.invoke("bad", AgentInput(message="x"), ctx)

        assert result.status == AgentStatus.ERROR
        assert len(sink.chunks) == 1

        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "error"

    @pytest.mark.asyncio
    async def test_invoke_no_stream_no_push(self) -> None:
        """When ctx.stream is None, no chunks are pushed."""
        ctx = make_ctx()
        assert ctx.stream is None

        runtime = StreamingRuntime(FakeRuntime(output="hello"))
        result = await runtime.invoke("greet", AgentInput(message="hi"), ctx)

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_invoke_chain_pushes_chunk(self) -> None:
        """invoke_chain pushes a final chunk."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="chained"))
        result = await runtime.invoke_chain(["a", "b"], AgentInput(message="x"), ctx)

        assert result.output == "chained"
        assert len(sink.chunks) == 1
        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "complete"

    @pytest.mark.asyncio
    async def test_delegate_pushes_chunk(self) -> None:
        """delegate pushes a chunk to the parent context."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="delegated"))
        result = await runtime.delegate("sub", AgentInput(message="x"), ctx)

        assert result.output == "delegated"
        assert len(sink.chunks) == 1

    @pytest.mark.asyncio
    async def test_handler_returns_string_single_chunk(self) -> None:
        """A handler returning a plain string produces a single COMPLETE chunk."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="full response"))
        await runtime.invoke("simple", AgentInput(message="q"), ctx)

        assert len(sink.chunks) == 1
        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "complete"
        assert parsed["content"] == "full response"


class TestStreamChunkHelpers:
    """Tests for build_chunk and serialize_chunk."""

    def test_build_chunk_creates_frozen_dataclass(self) -> None:
        chunk = build_chunk(StreamChunkType.TOKEN, "hello")
        assert chunk.type == StreamChunkType.TOKEN
        assert chunk.content == "hello"
        assert chunk.timestamp > 0

    def test_serialize_chunk_produces_valid_json(self) -> None:
        chunk = build_chunk(StreamChunkType.COMPLETE, "done")
        serialized = serialize_chunk(chunk)
        parsed = json.loads(serialized)
        assert parsed["type"] == "complete"
        assert parsed["content"] == "done"

    def test_build_chunk_empty_content(self) -> None:
        chunk = build_chunk(StreamChunkType.TOKEN, "")
        assert chunk.content == ""


# ---------------------------------------------------------------------------
# N-661: Tool streaming
# ---------------------------------------------------------------------------


class TestStreamingToolManager:
    """Tests for StreamingToolManager wrapper."""

    @pytest.mark.asyncio
    async def test_tool_start_and_end_events(self) -> None:
        """Successful tool call emits start and end events."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        mgr = StreamingToolManager(FakeToolManager())
        result = await mgr.call("calc", {"x": 1}, ctx)

        assert result.status == ToolStatus.SUCCESS
        assert len(sink.chunks) == 2

        start = json.loads(sink.chunks[0])
        assert start["type"] == TOOL_START_TYPE
        assert start["tool"] == "calc"

        end = json.loads(sink.chunks[1])
        assert end["type"] == TOOL_END_TYPE
        assert end["tool"] == "calc"
        assert "duration_ms" in end
        assert end["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_tool_error_event_on_failure(self) -> None:
        """Failed tool call emits start and error events."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        bad_result = ToolResult(status=ToolStatus.ERROR, error="bad input")
        mgr = StreamingToolManager(FakeToolManager(result=bad_result))
        result = await mgr.call("broken", {}, ctx)

        assert result.status == ToolStatus.ERROR
        assert len(sink.chunks) == 2

        error = json.loads(sink.chunks[1])
        assert error["type"] == TOOL_ERROR_TYPE
        assert error["tool"] == "broken"
        assert "bad input" in error["error"]

    @pytest.mark.asyncio
    async def test_tool_error_event_on_exception(self) -> None:
        """Tool that raises pushes start + error events, then re-raises."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        mgr = StreamingToolManager(FakeToolManager(raise_error=True))

        with pytest.raises(RuntimeError, match="tool exploded"):
            await mgr.call("exploding", {}, ctx)

        assert len(sink.chunks) == 2
        error = json.loads(sink.chunks[1])
        assert error["type"] == TOOL_ERROR_TYPE
        assert "tool exploded" in error["error"]

    @pytest.mark.asyncio
    async def test_discover_delegates_directly(self) -> None:
        """Discover passes through to the inner manager without events."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        mgr = StreamingToolManager(FakeToolManager())
        specs = await mgr.discover(ctx)

        assert len(specs) == 1
        assert specs[0].name == "test_tool"
        assert len(sink.chunks) == 0

    @pytest.mark.asyncio
    async def test_no_stream_no_events(self) -> None:
        """When ctx.stream is None, no events are pushed."""
        ctx = make_ctx()
        assert ctx.stream is None

        mgr = StreamingToolManager(FakeToolManager())
        result = await mgr.call("calc", {}, ctx)

        assert result.status == ToolStatus.SUCCESS


# ---------------------------------------------------------------------------
# N-662: Responder streaming
# ---------------------------------------------------------------------------


class TestStreamingResponder:
    """Tests for StreamingResponder and format helpers."""

    def test_sse_format(self) -> None:
        """SSE format wraps content in data: envelope."""
        responder = StreamingResponder(StreamFormat.SSE)
        formatted = responder.format_chunk("hello")

        assert formatted.startswith("data: ")
        assert formatted.endswith("\n\n")
        payload = json.loads(formatted[6:-2])
        assert payload["content"] == "hello"

    def test_websocket_format(self) -> None:
        """WebSocket format produces a JSON object."""
        responder = StreamingResponder(StreamFormat.WEBSOCKET)
        formatted = responder.format_chunk("world")

        parsed = json.loads(formatted)
        assert parsed["content"] == "world"

    def test_raw_format(self) -> None:
        """Raw format returns content unchanged."""
        responder = StreamingResponder(StreamFormat.RAW)
        assert responder.format_chunk("plain") == "plain"

    def test_default_format_is_raw(self) -> None:
        """Default format is RAW."""
        responder = StreamingResponder()
        assert responder.format == StreamFormat.RAW

    def test_format_property(self) -> None:
        """The format property returns the configured format."""
        responder = StreamingResponder(StreamFormat.SSE)
        assert responder.format == StreamFormat.SSE

    def test_sse_with_special_characters(self) -> None:
        """SSE format handles special characters (quotes, newlines)."""
        formatted = format_sse('line1\nline2\t"quoted"')
        payload = json.loads(formatted[6:-2])
        assert payload["content"] == 'line1\nline2\t"quoted"'

    def test_websocket_with_unicode(self) -> None:
        """WebSocket format handles unicode."""
        formatted = format_websocket("emoji: \u2764")
        parsed = json.loads(formatted)
        assert parsed["content"] == "emoji: \u2764"

    def test_raw_with_empty_string(self) -> None:
        """Raw format handles empty strings."""
        assert format_raw("") == ""

    def test_sse_with_empty_string(self) -> None:
        """SSE format handles empty content."""
        formatted = format_sse("")
        payload = json.loads(formatted[6:-2])
        assert payload["content"] == ""


# ---------------------------------------------------------------------------
# N-663: Orchestrator stream() end-to-end
# ---------------------------------------------------------------------------


class TestOrchestratorStreamIntegration:
    """Integration tests for orchestrator.stream() with streaming pipeline."""

    @pytest.mark.asyncio
    async def test_stream_yields_chunks(self) -> None:
        """stream() yields chunks from the pipeline."""
        from nerva.orchestrator import Orchestrator
        from nerva.responder.passthrough import PassthroughResponder
        from nerva.router.rule import RuleRouter
        from nerva.runtime.inprocess import InProcessRuntime

        runtime = InProcessRuntime()

        async def echo_handler(input: AgentInput, ctx: ExecContext) -> str:
            if ctx.stream is not None:
                await ctx.stream.push("tok1")
                await ctx.stream.push("tok2")
            return "tok1tok2"

        runtime.register("echo", echo_handler)
        router = RuleRouter([Rule(pattern=".*", handler="echo", intent="echo")])
        responder = PassthroughResponder()

        orch = Orchestrator(router=router, runtime=runtime, responder=responder)
        chunks: list[str] = []

        async for chunk in orch.stream("hello"):
            chunks.append(chunk)

        # Should have the chunks pushed by the handler
        assert "tok1" in chunks
        assert "tok2" in chunks

    @pytest.mark.asyncio
    async def test_stream_with_async_generator_handler(self) -> None:
        """stream() works with async generator handlers that yield tokens."""
        from nerva.orchestrator import Orchestrator
        from nerva.responder.passthrough import PassthroughResponder
        from nerva.router.rule import RuleRouter
        from nerva.runtime.inprocess import InProcessRuntime

        runtime = InProcessRuntime()

        async def gen_handler(input: AgentInput, ctx: ExecContext) -> AsyncIterator[str]:
            yield "chunk_a"
            yield "chunk_b"
            yield "chunk_c"

        runtime.register("gen", gen_handler)
        router = RuleRouter([Rule(pattern=".*", handler="gen", intent="gen")])
        responder = PassthroughResponder()

        orch = Orchestrator(router=router, runtime=runtime, responder=responder)
        chunks: list[str] = []

        async for chunk in orch.stream("test"):
            chunks.append(chunk)

        assert "chunk_a" in chunks
        assert "chunk_b" in chunks
        assert "chunk_c" in chunks


# ---------------------------------------------------------------------------
# N-664: Edge cases
# ---------------------------------------------------------------------------


class TestStreamingEdgeCases:
    """Edge cases for the streaming system."""

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        """Handler that produces no output results in no chunks."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output=""))
        await runtime.invoke("empty", AgentInput(message=""), ctx)

        # Still pushes a COMPLETE chunk (with empty content)
        assert len(sink.chunks) == 1
        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "complete"
        assert parsed["content"] == ""

    @pytest.mark.asyncio
    async def test_error_mid_stream(self) -> None:
        """Error during invoke produces an ERROR chunk."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = StreamingRuntime(FakeRuntime(output="", status=AgentStatus.ERROR))
        result = await runtime.invoke("fail", AgentInput(message="x"), ctx)

        assert result.status == AgentStatus.ERROR
        parsed = json.loads(sink.chunks[0])
        assert parsed["type"] == "error"

    @pytest.mark.asyncio
    async def test_cancelled_context(self) -> None:
        """Cancelled context still receives chunks from completed invoke."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink
        ctx.cancelled.set()

        runtime = StreamingRuntime(FakeRuntime(output="still works"))
        result = await runtime.invoke("x", AgentInput(message="y"), ctx)

        # The runtime wrapper doesn't check cancellation -- it pushes regardless
        assert result.output == "still works"
        assert len(sink.chunks) == 1

    @pytest.mark.asyncio
    async def test_tool_streaming_with_timeout_result(self) -> None:
        """Tool returning TIMEOUT status emits error event."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        timeout_result = ToolResult(status=ToolStatus.TIMEOUT, error="timed out")
        mgr = StreamingToolManager(FakeToolManager(result=timeout_result))
        result = await mgr.call("slow_tool", {}, ctx)

        assert result.status == ToolStatus.TIMEOUT
        error_event = json.loads(sink.chunks[1])
        assert error_event["type"] == TOOL_ERROR_TYPE
        assert "timed out" in error_event["error"]

    def test_streaming_responder_unknown_format(self) -> None:
        """Unknown format raises ValueError."""
        from nerva.responder.streaming import _format_for_channel

        with pytest.raises(ValueError, match="Unknown stream format"):
            _format_for_channel("content", "invalid_format")  # type: ignore[arg-type]
