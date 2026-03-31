"""Composite tool manager — combine multiple ToolManagers into one (N-613).

Merges discovery results from all inner managers (deduplicated by name)
and routes tool calls to the manager that owns each tool. First manager
to claim a tool name wins (priority order).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from nerva.tools import ToolManager, ToolResult, ToolSpec, ToolStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = ["CompositeToolManager"]

_log = logging.getLogger(__name__)


class CompositeToolManager:
    """Combine multiple ToolManagers into a single unified interface.

    Discovery merges results from all managers, deduplicating by tool name
    (first manager to claim a name wins). Tool calls are routed to the
    owning manager.

    Args:
        managers: Ordered list of ToolManagers. Priority is first-to-last.
    """

    def __init__(self, managers: list[ToolManager]) -> None:
        self._managers = list(managers)
        self._tool_owner: dict[str, ToolManager] = {}

    # -- Public API ----------------------------------------------------------

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Discover tools from all inner managers, deduplicated by name.

        First manager to register a tool name wins. Subsequent managers
        with the same tool name are silently ignored.

        Args:
            ctx: Execution context carrying identity and permissions.

        Returns:
            Merged, deduplicated list of tool specs.
        """
        seen_names: set[str] = set()
        merged: list[ToolSpec] = []

        for manager in self._managers:
            specs = await _safe_discover(manager, ctx)
            for spec in specs:
                if spec.name in seen_names:
                    continue
                seen_names.add(spec.name)
                merged.append(spec)
                self._tool_owner[spec.name] = manager

        return merged

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Route a tool call to the manager that owns it.

        Args:
            tool: Tool name to invoke.
            args: Arguments matching the tool's parameter schema.
            ctx: Execution context carrying identity and permissions.

        Returns:
            ToolResult from the owning manager, or NOT_FOUND if unowned.
        """
        owner = self._tool_owner.get(tool)
        if owner is None:
            return ToolResult(
                status=ToolStatus.NOT_FOUND,
                error=f"Tool '{tool}' not found in any manager",
            )

        return await owner.call(tool, args, ctx)


# -- Helpers -----------------------------------------------------------------


async def _safe_discover(manager: ToolManager, ctx: ExecContext) -> list[ToolSpec]:
    """Discover tools from a manager, suppressing errors.

    Args:
        manager: The ToolManager to query.
        ctx: Execution context.

    Returns:
        Tool specs from the manager, or empty list on failure.
    """
    try:
        return await manager.discover(ctx)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Tool discovery failed for %s: %s", type(manager).__name__, exc)
        return []
