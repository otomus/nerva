"""Tool layer — discover, sandbox, and execute tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nerva.context import ExecContext


class ToolStatus(StrEnum):
    """Outcome status of a tool call.

    Each value maps to a distinct failure mode so callers can branch
    on status without inspecting error messages.
    """

    SUCCESS = "success"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ToolSpec:
    """Specification for a discoverable tool.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable description (used by LLM for selection).
        parameters: JSON Schema for tool input parameters.
        required_permissions: Roles required to use this tool.
    """

    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=dict)
    required_permissions: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ToolResult:
    """Result from executing a tool.

    Attributes:
        status: Outcome status.
        output: Tool output (string or structured).
        error: Error message if status is not SUCCESS.
        duration_ms: Execution time in milliseconds.
    """

    status: ToolStatus
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0


@runtime_checkable
class ToolManager(Protocol):
    """Discover and execute tools within sandbox constraints.

    Implementations must filter discovery results by the caller's
    permissions and enforce sandboxing during execution.
    """

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Return available tools filtered by the context's permissions.

        Args:
            ctx: Execution context carrying identity and permission set.

        Returns:
            List of tool specs the current user/agent is allowed to access.
        """
        ...

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Execute a tool call within sandbox constraints.

        Args:
            tool: Tool name to invoke.
            args: Arguments matching the tool's parameter schema.
            ctx: Execution context carrying identity and permission set.

        Returns:
            ToolResult with status and output.
        """
        ...


__all__ = [
    "ToolStatus",
    "ToolSpec",
    "ToolResult",
    "ToolManager",
    "StreamingToolManager",
]

from nerva.tools.streaming import StreamingToolManager  # noqa: E402
