"""Function-based tools — register Python functions as tools with a ``@tool`` decorator.

Usage::

    mgr = FunctionToolManager()

    @mgr.tool("add", "Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    specs = await mgr.discover(ctx)
    result = await mgr.call("add", {"a": 1, "b": 2}, ctx)
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import get_type_hints

from nerva.context import ExecContext
from nerva.tools import ToolResult, ToolSpec, ToolStatus


# ---------------------------------------------------------------------------
# Python type → JSON Schema type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}
"""Maps Python built-in types to their JSON Schema equivalents."""


# ---------------------------------------------------------------------------
# RegisteredTool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredTool:
    """Internal record for a function registered as a tool.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable description.
        function: The underlying callable.
        is_async: Whether the function is a coroutine function.
        parameters: JSON Schema extracted from the function's type hints.
        required_permissions: Roles required to use this tool.
    """

    name: str
    description: str
    function: Callable[..., object]
    is_async: bool
    parameters: dict[str, object]
    required_permissions: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------


def _extract_parameters(func: Callable[..., object]) -> dict[str, object]:
    """Build a basic JSON Schema ``object`` from a function's type annotations.

    Inspects the function signature and type hints to produce a schema with
    ``type``, ``properties``, and ``required`` fields. Parameters without a
    default value are marked as required.

    Args:
        func: The function to introspect.

    Returns:
        A JSON Schema dict describing the function's parameters.
    """
    sig = inspect.signature(func)
    hints = get_type_hints(func)

    properties: dict[str, object] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        prop: dict[str, str] = {}
        annotation = hints.get(param_name)

        if annotation is not None and annotation in _TYPE_MAP:
            prop["type"] = _TYPE_MAP[annotation]
        else:
            prop["type"] = "string"

        properties[param_name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


# ---------------------------------------------------------------------------
# FunctionToolManager
# ---------------------------------------------------------------------------


class FunctionToolManager:
    """Tool manager that wraps plain Python functions as tools.

    Register functions with the ``@tool`` decorator, then use ``discover()``
    and ``call()`` to interact with them through the standard ``ToolManager``
    protocol.
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    # -- Registration -------------------------------------------------------

    def tool(
        self,
        name: str,
        description: str,
        *,
        required_permissions: frozenset[str] | None = None,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Decorator that registers a function as a tool.

        Args:
            name: Unique tool identifier.
            description: Human-readable description shown to the LLM.
            required_permissions: Roles the caller must possess. Defaults to
                no restrictions (empty frozenset).

        Returns:
            A decorator that registers the function and returns it unchanged.

        Raises:
            ValueError: If a tool with the same *name* is already registered.
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            registered = RegisteredTool(
                name=name,
                description=description,
                function=func,
                is_async=inspect.iscoroutinefunction(func),
                parameters=_extract_parameters(func),
                required_permissions=required_permissions or frozenset(),
            )
            self._tools[name] = registered
            return func

        return decorator

    # -- Protocol implementation --------------------------------------------

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Return tool specs the caller is permitted to use.

        Filters the registered tools by the context's ``permissions.can_use_tool``
        check and by matching the caller's roles against the tool's
        ``required_permissions``.

        Args:
            ctx: Execution context carrying identity and permission set.

        Returns:
            List of ``ToolSpec`` instances for accessible tools.
        """
        specs: list[ToolSpec] = []
        for registered in self._tools.values():
            if not ctx.permissions.can_use_tool(registered.name):
                continue
            if not _has_required_roles(ctx, registered.required_permissions):
                continue
            specs.append(
                ToolSpec(
                    name=registered.name,
                    description=registered.description,
                    parameters=registered.parameters,
                    required_permissions=registered.required_permissions,
                )
            )
        return specs

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Execute a registered tool by name.

        Validates existence and permissions before invoking the function.
        Sync functions are offloaded to a thread via ``asyncio.to_thread``.

        Args:
            tool: Tool name to invoke.
            args: Keyword arguments forwarded to the underlying function.
            ctx: Execution context carrying identity and permission set.

        Returns:
            ``ToolResult`` with the outcome status, output, and timing.
        """
        registered = self._tools.get(tool)
        if registered is None:
            return ToolResult(status=ToolStatus.NOT_FOUND, error=f"Tool '{tool}' not found")

        if not ctx.permissions.can_use_tool(tool):
            return ToolResult(
                status=ToolStatus.PERMISSION_DENIED,
                error=f"Permission denied for tool '{tool}'",
            )

        if not _has_required_roles(ctx, registered.required_permissions):
            return ToolResult(
                status=ToolStatus.PERMISSION_DENIED,
                error=f"Missing required role for tool '{tool}'",
            )

        return await _execute(registered, args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_required_roles(ctx: ExecContext, required: frozenset[str]) -> bool:
    """Check whether the context carries all required roles.

    An empty *required* set means the tool is unrestricted.

    Args:
        ctx: Execution context to inspect.
        required: Role names that must all be present.

    Returns:
        ``True`` if every required role is present (or none are required).
    """
    if not required:
        return True
    return all(ctx.permissions.has_role(role) for role in required)


async def _execute(registered: RegisteredTool, args: dict[str, object]) -> ToolResult:
    """Invoke the underlying function and wrap the outcome in a ``ToolResult``.

    Async functions are awaited directly; sync functions are run via
    ``asyncio.to_thread`` to avoid blocking the event loop.

    Args:
        registered: The registered tool record.
        args: Keyword arguments to forward to the function.

    Returns:
        ``ToolResult`` with status, output, and execution duration.
    """
    start = time.monotonic()
    try:
        if registered.is_async:
            raw_output = await registered.function(**args)
        else:
            raw_output = await asyncio.to_thread(registered.function, **args)
    except Exception as exc:  # noqa: BLE001 — catch-all is intentional here
        elapsed = (time.monotonic() - start) * 1000
        return ToolResult(
            status=ToolStatus.ERROR,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=elapsed,
        )

    elapsed = (time.monotonic() - start) * 1000
    return ToolResult(
        status=ToolStatus.SUCCESS,
        output=str(raw_output),
        duration_ms=elapsed,
    )
