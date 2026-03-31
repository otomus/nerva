"""Streaming responder — format chunks per channel capabilities.

Provides SSE, WebSocket, and raw text formatting for streamed chunks.
Each channel format wraps the content differently:

- SSE: ``data: {"content": "..."}\n\n``
- WebSocket: JSON message ``{"content": "..."}``
- Raw: plain text, no framing
"""

from __future__ import annotations

import json
from enum import StrEnum

__all__ = [
    "StreamFormat",
    "StreamingResponder",
]


# ---------------------------------------------------------------------------
# Format enum
# ---------------------------------------------------------------------------


class StreamFormat(StrEnum):
    """Output format for streaming chunks.

    Members:
        SSE: Server-Sent Events format (``data: ...\n\n``).
        WEBSOCKET: JSON message format.
        RAW: Plain text, no framing.
    """

    SSE = "sse"
    WEBSOCKET = "websocket"
    RAW = "raw"


# ---------------------------------------------------------------------------
# StreamingResponder
# ---------------------------------------------------------------------------


class StreamingResponder:
    """Formats streamed chunks based on the target channel format.

    The responder is configured with a format and converts raw content
    strings into framed output appropriate for the transport layer.

    Args:
        fmt: The streaming format to use. Defaults to RAW.
    """

    def __init__(self, fmt: StreamFormat = StreamFormat.RAW) -> None:
        self._format = fmt

    @property
    def format(self) -> StreamFormat:
        """The configured streaming format.

        Returns:
            The current StreamFormat.
        """
        return self._format

    def format_chunk(self, content: str) -> str:
        """Format a single content string for the configured channel.

        Args:
            content: Raw text content to format.

        Returns:
            The formatted chunk string.
        """
        return _format_for_channel(content, self._format)


# ---------------------------------------------------------------------------
# Pure formatting functions
# ---------------------------------------------------------------------------


def format_sse(content: str) -> str:
    """Format content as a Server-Sent Event.

    Args:
        content: Raw text content.

    Returns:
        SSE-formatted string: ``data: {"content": "..."}\n\n``
    """
    payload = json.dumps({"content": content})
    return f"data: {payload}\n\n"


def format_websocket(content: str) -> str:
    """Format content as a WebSocket JSON message.

    Args:
        content: Raw text content.

    Returns:
        JSON string: ``{"content": "..."}``
    """
    return json.dumps({"content": content})


def format_raw(content: str) -> str:
    """Return content unchanged (raw text).

    Args:
        content: Raw text content.

    Returns:
        The content string unchanged.
    """
    return content


def _format_for_channel(content: str, fmt: StreamFormat) -> str:
    """Route content through the appropriate formatter.

    Args:
        content: Raw text content.
        fmt: Target format.

    Returns:
        Formatted chunk string.

    Raises:
        ValueError: If the format is not recognised.
    """
    if fmt == StreamFormat.SSE:
        return format_sse(content)
    if fmt == StreamFormat.WEBSOCKET:
        return format_websocket(content)
    if fmt == StreamFormat.RAW:
        return format_raw(content)
    raise ValueError(f"Unknown stream format: {fmt}")
