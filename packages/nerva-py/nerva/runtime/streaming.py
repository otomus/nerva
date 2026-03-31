"""Streaming runtime wrapper — push tokens to ctx.stream as the LLM produces them.

Wraps any AgentRuntime and intercepts output to push structured chunks
to the context's stream sink. Supports two modes:

1. Handler returns an async generator -- each yielded value is pushed as a token chunk.
2. Handler returns a string -- the entire result is pushed as a single complete chunk.

Each chunk is a JSON-serialisable dict with type, content, and timestamp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from nerva.runtime import AgentInput, AgentResult, AgentStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.runtime import AgentRuntime

__all__ = [
    "StreamChunkType",
    "StreamChunk",
    "StreamingRuntime",
]


# ---------------------------------------------------------------------------
# Constants & types
# ---------------------------------------------------------------------------


class StreamChunkType(StrEnum):
    """Kind of streaming chunk pushed to the sink.

    Members:
        TOKEN: An incremental text token from the LLM.
        PROGRESS: A progress indicator (e.g. percentage or status message).
        COMPLETE: Final chunk signalling the stream is done.
        ERROR: An error occurred during streaming.
    """

    TOKEN = "token"
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass(frozen=True)
class StreamChunk:
    """A single streaming chunk pushed to the context's stream sink.

    Attributes:
        type: The kind of chunk.
        content: Text payload of the chunk.
        timestamp: Unix timestamp when the chunk was created.
    """

    type: StreamChunkType
    content: str
    timestamp: float


# ---------------------------------------------------------------------------
# StreamingRuntime
# ---------------------------------------------------------------------------


class StreamingRuntime:
    """Wraps any AgentRuntime to push structured stream chunks to ``ctx.stream``.

    When a handler is invoked, this wrapper delegates to the inner runtime
    and pushes a COMPLETE chunk with the full output. If the handler fails,
    an ERROR chunk is pushed instead.

    For async-generator handlers (streaming at the InProcessRuntime level),
    the inner runtime already pushes raw text to ``ctx.stream``. This wrapper
    adds structured framing on top.

    Args:
        inner: The underlying runtime to delegate execution to.
    """

    def __init__(self, inner: AgentRuntime) -> None:
        self._inner = inner

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Invoke a handler and push structured chunks to ``ctx.stream``.

        Args:
            handler: Handler name to invoke.
            input: Structured input for the handler.
            ctx: Execution context with optional stream sink.

        Returns:
            AgentResult from the underlying runtime.
        """
        result = await self._inner.invoke(handler, input, ctx)
        await _push_result_chunk(result, ctx)
        return result

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run handlers in sequence, pushing a final chunk for the last result.

        Args:
            handlers: Ordered list of handler names.
            input: Initial input for the first handler.
            ctx: Execution context shared across the chain.

        Returns:
            AgentResult from the last successfully executed handler.
        """
        result = await self._inner.invoke_chain(handlers, input, ctx)
        await _push_result_chunk(result, ctx)
        return result

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Delegate to another handler, pushing a chunk for the result.

        Args:
            handler: Handler name to delegate to.
            input: Input for the delegated handler.
            parent_ctx: Parent's execution context.

        Returns:
            AgentResult from the delegated handler.
        """
        result = await self._inner.delegate(handler, input, parent_ctx)
        await _push_result_chunk(result, parent_ctx)
        return result


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_chunk(chunk_type: StreamChunkType, content: str) -> StreamChunk:
    """Create a StreamChunk with the current timestamp.

    Args:
        chunk_type: Kind of chunk.
        content: Text payload.

    Returns:
        A frozen StreamChunk instance.
    """
    return StreamChunk(type=chunk_type, content=content, timestamp=time.time())


def serialize_chunk(chunk: StreamChunk) -> str:
    """Serialize a StreamChunk to a JSON-compatible string for the stream sink.

    Args:
        chunk: The chunk to serialize.

    Returns:
        JSON string representation of the chunk.
    """
    import json

    return json.dumps({
        "type": chunk.type.value,
        "content": chunk.content,
        "timestamp": chunk.timestamp,
    })


async def _push_result_chunk(result: AgentResult, ctx: ExecContext) -> None:
    """Push a structured chunk to the stream sink based on the result status.

    Args:
        result: The agent result to convert to a chunk.
        ctx: Execution context with optional stream sink.
    """
    if ctx.stream is None:
        return

    if result.status == AgentStatus.SUCCESS:
        chunk = build_chunk(StreamChunkType.COMPLETE, result.output)
    else:
        error_msg = result.error or f"handler failed with status {result.status}"
        chunk = build_chunk(StreamChunkType.ERROR, error_msg)

    await ctx.stream.push(serialize_chunk(chunk))
