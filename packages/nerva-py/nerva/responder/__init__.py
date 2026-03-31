"""Responder — format agent output for target channels."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.runtime import AgentResult


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    """Target channel for a response.

    Attributes:
        name: Channel identifier (e.g. ``"slack"``, ``"api"``, ``"websocket"``).
        supports_markdown: Whether the channel renders markdown.
        supports_media: Whether the channel can display images/files.
        max_length: Maximum response length in characters (0 = unlimited).
    """

    name: str
    supports_markdown: bool = True
    supports_media: bool = False
    max_length: int = 0


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass
class Response:
    """Formatted response ready for delivery.

    Attributes:
        text: The formatted response text.
        channel: Target channel this response was formatted for.
        media: Optional media attachments (URLs or base64 strings).
        metadata: Extra key-value metadata for the channel.
    """

    text: str
    channel: Channel
    media: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Default channels
# ---------------------------------------------------------------------------

API_CHANNEL = Channel(name="api", supports_markdown=False, supports_media=True)
"""Default channel for programmatic API consumers."""

WEBSOCKET_CHANNEL = Channel(
    name="websocket", supports_markdown=True, supports_media=True
)
"""Default channel for WebSocket connections."""


# ---------------------------------------------------------------------------
# Responder protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Responder(Protocol):
    """Format agent output for a target channel.

    Implementations adapt the raw ``AgentResult`` into a ``Response``
    appropriate for the delivery channel (truncation, markdown stripping,
    media attachment, etc.).
    """

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Format agent output for the target channel.

        Args:
            output: Raw agent result from the runtime.
            channel: Target delivery channel.
            ctx: Execution context carrying identity and permissions.

        Returns:
            Formatted ``Response`` ready for delivery.
        """
        ...


__all__ = [
    "Channel",
    "Response",
    "Responder",
    "API_CHANNEL",
    "WEBSOCKET_CHANNEL",
    "StreamFormat",
    "StreamingResponder",
]

from nerva.responder.streaming import StreamFormat, StreamingResponder  # noqa: E402
