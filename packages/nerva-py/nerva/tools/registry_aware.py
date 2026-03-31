"""Registry-aware tool manager wrapper — filters tools against registry entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.registry import HealthStatus
from nerva.tools import ToolResult, ToolSpec, ToolStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.registry import Registry
    from nerva.tools import ToolManager


class RegistryAwareToolManager:
    """Wraps any ``ToolManager`` and filters discovered tools against the registry.

    Tools that are unavailable or disabled in the registry are excluded from
    discovery results. Calls to unavailable tools are rejected with a
    ``NOT_FOUND`` status.

    Attributes:
        _inner: The wrapped tool manager that handles execution.
        _registry: Registry used to check tool health and availability.
    """

    def __init__(self, inner: ToolManager, registry: Registry) -> None:
        self._inner = inner
        self._registry = registry

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Return available tools filtered by both the inner manager and registry.

        Removes any tool whose registry entry is unavailable. Tools without
        a registry entry pass through (they are not registry-managed).

        Args:
            ctx: Execution context carrying identity and permission set.

        Returns:
            List of tool specs the current user is allowed to access.
        """
        all_tools = await self._inner.discover(ctx)
        return await self._filter_available_tools(all_tools, ctx)

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Execute a tool call, rejecting unavailable tools.

        Checks registry health before delegating to the inner manager.
        Returns a ``NOT_FOUND`` result if the tool is registered but
        unavailable.

        Args:
            tool: Tool name to invoke.
            args: Arguments matching the tool's parameter schema.
            ctx: Execution context carrying identity and permission set.

        Returns:
            ToolResult with status and output.
        """
        if not await self._is_tool_available(tool, ctx):
            return ToolResult(
                status=ToolStatus.NOT_FOUND,
                error=f"Tool {tool!r} is unavailable in the registry",
            )
        return await self._inner.call(tool, args, ctx)

    async def _filter_available_tools(
        self, tools: list[ToolSpec], ctx: ExecContext
    ) -> list[ToolSpec]:
        """Keep only tools that are available in the registry.

        Args:
            tools: Full list from the inner tool manager.
            ctx: Execution context for registry lookups.

        Returns:
            Filtered list preserving original order.
        """
        available: list[ToolSpec] = []
        for tool in tools:
            if await self._is_tool_available(tool.name, ctx):
                available.append(tool)
        return available

    async def _is_tool_available(self, name: str, ctx: ExecContext) -> bool:
        """Check whether a tool is available in the registry.

        Returns ``True`` for tools not found in the registry (they are
        not registry-managed, so we don't block them).

        Args:
            name: Tool name to look up.
            ctx: Execution context for registry resolve.

        Returns:
            ``True`` if the tool is available or unregistered.
        """
        entry = await self._registry.resolve(name, ctx)
        if entry is None:
            return True
        return entry.health != HealthStatus.UNAVAILABLE
