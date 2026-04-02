"""MCP Armor testkit integration.

Wraps ``mcparmor.ArmorTestHarness`` to provide Nerva-typed ``ToolResult``
objects and expectation-setting for MCP tool calls. The harness runs
the real mcparmor broker with a mock tool behind it, so Layer 1 policy
enforcement is exercised in every test.

Requires ``otomus-mcp-armor>=0.3.1`` (``import mcparmor``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.tools import ToolResult, ToolStatus
from nerva.tools.mcp import ARMOR_ERROR_CODES

if TYPE_CHECKING:
    from mcparmor import ArmorTestHarness as _ArmorTestHarness, ToolCallResult


def _to_nerva_result(tcr: ToolCallResult) -> ToolResult:
    """Convert a mcparmor ``ToolCallResult`` into a Nerva ``ToolResult``.

    Args:
        tcr: The raw result from ``ArmorTestHarness.call_tool()``.

    Returns:
        A Nerva ``ToolResult`` with the appropriate status.
    """
    if tcr.blocked:
        is_armor = tcr.error_code in ARMOR_ERROR_CODES
        return ToolResult(
            status=ToolStatus.PERMISSION_DENIED if is_armor else ToolStatus.ERROR,
            error=tcr.error_message or "Blocked by armor policy",
        )

    text = tcr.text
    return ToolResult(
        status=ToolStatus.SUCCESS,
        output=text or "",
    )


def _mcp_text_response(text: str) -> dict[str, object]:
    """Build an MCP text content response payload.

    Args:
        text: The text string to wrap.

    Returns:
        MCP-formatted response with a single text content block.
    """
    return {"content": [{"type": "text", "text": text}]}


class MCPTestHarness:
    """Nerva wrapper around ``mcparmor.ArmorTestHarness``.

    Runs the real mcparmor broker with a mock tool server behind it.
    Every ``call_tool`` exercises Layer 1 manifest enforcement, and
    returns a Nerva ``ToolResult`` instead of a raw ``ToolCallResult``.

    Usage::

        async with MCPTestHarness(armor="./armor.json") as h:
            h.set_mock_response("read_file", "file contents")
            result = await h.call_tool("read_file", {"path": "/ok.txt"})
            assert result.status == ToolStatus.SUCCESS

    Args:
        armor: Path to the ``armor.json`` manifest under test.
        profile: Optional profile name override.
        timeout: Read timeout in seconds for individual tool calls.

    Raises:
        ImportError: If ``otomus-mcp-armor`` is not installed.
    """

    def __init__(
        self,
        *,
        armor: str = "./armor.json",
        profile: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        try:
            import mcparmor  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "mcparmor is required for MCP testkit integration. "
                "Install it with: pip install otomus-mcp-armor"
            ) from exc
        self._armor = armor
        self._profile = profile
        self._timeout = timeout
        self._harness: _ArmorTestHarness | None = None

    async def __aenter__(self) -> MCPTestHarness:
        """Start the broker and mock tool server.

        Returns:
            Self for use as an async context manager.
        """
        from mcparmor import ArmorTestHarness

        self._harness = ArmorTestHarness(
            armor=self._armor,
            profile=self._profile,
            timeout=self._timeout,
        )
        await self._harness.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Stop the broker and mock tool server."""
        if self._harness is not None:
            await self._harness.__aexit__(*args)
            self._harness = None

    async def call_tool(
        self, name: str, args: dict[str, object] | None = None
    ) -> ToolResult:
        """Call a tool through the armored broker and return a Nerva result.

        Args:
            name: Tool name to invoke.
            args: Optional arguments for the tool.

        Returns:
            A Nerva ``ToolResult`` — ``SUCCESS`` if the broker allowed the
            call, ``PERMISSION_DENIED`` if an armor policy blocked it.

        Raises:
            RuntimeError: If the harness is not started.
        """
        harness = self._require_harness()
        tcr = await harness.call_tool(name, args)
        return _to_nerva_result(tcr)

    def set_mock_response(
        self, tool_name: str, text: str
    ) -> None:
        """Set a canned text response for a specific tool.

        The mock server returns this as an MCP text content block
        whenever the named tool is called.

        Args:
            tool_name: Tool name to match.
            text: Text content to return.
        """
        harness = self._require_harness()
        harness.mock_tool_response(
            _mcp_text_response(text),
            tool_name=tool_name,
        )

    def set_default_response(self, text: str) -> None:
        """Set a default response for all tools that lack a specific mock.

        Args:
            text: Text content to return.
        """
        harness = self._require_harness()
        harness.mock_tool_response(_mcp_text_response(text))

    def set_tools(self, tools: list[dict[str, object]]) -> None:
        """Set the tool definitions returned by ``tools/list``.

        Args:
            tools: MCP tool definition objects (name, description, inputSchema).
        """
        harness = self._require_harness()
        harness.set_tools(tools)

    # -- Private ------------------------------------------------------------

    def _require_harness(self) -> _ArmorTestHarness:
        """Return the active harness or raise if not started.

        Returns:
            The running ``ArmorTestHarness``.

        Raises:
            RuntimeError: If the harness is not started.
        """
        if self._harness is None:
            raise RuntimeError(
                "MCPTestHarness must be used as an async context manager"
            )
        return self._harness


__all__ = ["MCPTestHarness"]
