"""Passthrough responder — returns raw output without formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.responder import Channel, Response

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.runtime import AgentResult


class PassthroughResponder:
    """Returns agent output as-is, without any transformation.

    Use for API consumers and programmatic access where the caller
    handles its own formatting. Truncates to ``channel.max_length``
    when set.
    """

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Pass output through without transformation.

        Truncates ``output.output`` to ``channel.max_length`` if the channel
        defines a positive limit. Media and metadata are left empty.

        Args:
            output: Raw agent result from the runtime.
            channel: Target delivery channel (used only for max_length).
            ctx: Execution context (unused by passthrough).

        Returns:
            ``Response`` containing the raw output text.
        """
        text = output.output
        if channel.max_length > 0:
            text = text[: channel.max_length]

        return Response(text=text, channel=channel)
