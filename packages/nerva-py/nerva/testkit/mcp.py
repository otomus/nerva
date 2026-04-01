"""MCP Armor testkit integration.

Wraps the mcparmor ArmorTestHarness to provide Nerva-typed tool results
and expectation-setting for MCP tool calls.

This module is a placeholder — the full integration depends on the
``mcparmor`` package being available. When ``mcparmor`` is not installed,
importing this module will work but ``MCPTestHarness`` will raise
``ImportError`` on instantiation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class MCPTestHarness:
    """Wrapper around mcparmor's ArmorTestHarness for Nerva-style assertions.

    Provides Nerva ``ToolResult`` objects and expectation-setting that
    delegates to Armor's mock server internally.

    Raises:
        ImportError: If mcparmor is not installed.
    """

    def __init__(self, *, armor: str = "./armor.json") -> None:
        try:
            import mcparmor  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "mcparmor is required for MCP testkit integration. "
                "Install it with: pip install mcparmor"
            ) from exc
        self._armor_config = armor
        self._harness: object = None

    async def __aenter__(self) -> MCPTestHarness:
        """Start the MCP Armor test harness.

        Returns:
            Self for use as an async context manager.
        """
        import mcparmor

        self._harness = mcparmor.ArmorTestHarness(armor=self._armor_config)
        await self._harness.__aenter__()  # type: ignore[union-attr]
        return self

    async def __aexit__(self, *args: object) -> None:
        """Stop the MCP Armor test harness."""
        if self._harness is not None:
            await self._harness.__aexit__(*args)  # type: ignore[union-attr]

    async def call_tool(
        self, tool_name: str, args: dict[str, object]
    ) -> object:
        """Call a tool through the MCP Armor harness.

        Args:
            tool_name: Name of the tool to call.
            args: Arguments for the tool.

        Returns:
            A Nerva-compatible ToolResult (shape depends on mcparmor output).
        """
        if self._harness is None:
            raise RuntimeError("MCPTestHarness must be used as an async context manager")
        return await self._harness.call_tool(tool_name, args)  # type: ignore[union-attr]

    def expect_tool_response(self, tool_name: str, response: str) -> None:
        """Set an expected response for a tool via Armor's mock server.

        Args:
            tool_name: Name of the tool.
            response: The canned response to return.
        """
        if self._harness is None:
            raise RuntimeError("MCPTestHarness must be used as an async context manager")
        self._harness.mock_tool_response(tool_name, response)  # type: ignore[union-attr]
