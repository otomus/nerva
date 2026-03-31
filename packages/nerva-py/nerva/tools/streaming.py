"""Streaming tool manager — push progress events during tool execution.

Wraps any ToolManager and emits structured streaming events before, after,
and on error of each tool call. These events flow through ``ctx.stream``
so consumers can show real-time tool progress.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from nerva.tools import ToolResult, ToolSpec, ToolStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.tools import ToolManager

__all__ = [
    "StreamingToolManager",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_START_TYPE = "tool_start"
"""Event type emitted before a tool call begins."""

TOOL_END_TYPE = "tool_end"
"""Event type emitted after a tool call completes successfully."""

TOOL_ERROR_TYPE = "tool_error"
"""Event type emitted when a tool call fails."""


# ---------------------------------------------------------------------------
# StreamingToolManager
# ---------------------------------------------------------------------------


class StreamingToolManager:
    """Wraps a ToolManager to push progress events during tool execution.

    Before each call, a ``tool_start`` event is pushed. After completion,
    either ``tool_end`` (with duration) or ``tool_error`` is pushed.

    Args:
        inner: The underlying tool manager to delegate to.
    """

    def __init__(self, inner: ToolManager) -> None:
        self._inner = inner

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Discover available tools — delegates directly to the inner manager.

        Args:
            ctx: Execution context carrying identity and permission set.

        Returns:
            List of tool specs.
        """
        return await self._inner.discover(ctx)

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Execute a tool call, emitting streaming progress events.

        Pushes a ``tool_start`` event before execution, then either
        ``tool_end`` (on success/non-error) or ``tool_error`` (on failure).

        Args:
            tool: Tool name to invoke.
            args: Arguments matching the tool's parameter schema.
            ctx: Execution context carrying identity and permission set.

        Returns:
            ToolResult from the underlying tool manager.
        """
        await _push_event(ctx, _build_start_event(tool))
        started_at = time.monotonic()

        try:
            result = await self._inner.call(tool, args, ctx)
        except Exception as exc:
            duration_ms = _elapsed_ms(started_at)
            await _push_event(ctx, _build_error_event(tool, str(exc), duration_ms))
            raise

        duration_ms = _elapsed_ms(started_at)

        if result.status == ToolStatus.SUCCESS:
            await _push_event(ctx, _build_end_event(tool, duration_ms))
        else:
            error_msg = result.error or f"tool failed with status {result.status}"
            await _push_event(ctx, _build_error_event(tool, error_msg, duration_ms))

        return result


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _elapsed_ms(started_at: float) -> float:
    """Calculate elapsed milliseconds since a monotonic start time.

    Args:
        started_at: Monotonic timestamp from ``time.monotonic()``.

    Returns:
        Elapsed time in milliseconds.
    """
    return (time.monotonic() - started_at) * 1000


def _build_start_event(tool: str) -> dict[str, object]:
    """Build a tool_start event payload.

    Args:
        tool: Tool name.

    Returns:
        Event dict with type and tool name.
    """
    return {"type": TOOL_START_TYPE, "tool": tool}


def _build_end_event(tool: str, duration_ms: float) -> dict[str, object]:
    """Build a tool_end event payload.

    Args:
        tool: Tool name.
        duration_ms: Execution duration in milliseconds.

    Returns:
        Event dict with type, tool name, and duration.
    """
    return {"type": TOOL_END_TYPE, "tool": tool, "duration_ms": round(duration_ms, 2)}


def _build_error_event(
    tool: str, error: str, duration_ms: float
) -> dict[str, object]:
    """Build a tool_error event payload.

    Args:
        tool: Tool name.
        error: Error message.
        duration_ms: Execution duration in milliseconds.

    Returns:
        Event dict with type, tool name, error, and duration.
    """
    return {
        "type": TOOL_ERROR_TYPE,
        "tool": tool,
        "error": error,
        "duration_ms": round(duration_ms, 2),
    }


async def _push_event(ctx: ExecContext, event: dict[str, object]) -> None:
    """Push a JSON-serialised event to the context's stream sink.

    No-ops if the context has no stream attached.

    Args:
        ctx: Execution context with optional stream sink.
        event: Event payload to serialize and push.
    """
    if ctx.stream is None:
        return
    await ctx.stream.push(json.dumps(event))
