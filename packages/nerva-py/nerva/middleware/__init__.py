"""Middleware — pipeline hooks for cross-cutting concerns.

Defines the stage enum and handler signature used by the Nerva runtime
to inject middleware at well-defined points in the request lifecycle.
Middleware can inspect, transform, or short-circuit payloads flowing
through the pipeline.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Callable, Awaitable

from nerva.context import ExecContext


class MiddlewareStage(StrEnum):
    """Pipeline stages where middleware can hook in.

    Each stage fires at a specific point in the request lifecycle:

    - ``BEFORE_ROUTE``: After context creation, before the router picks a handler.
    - ``BEFORE_INVOKE``: After routing, before the selected handler executes.
    - ``AFTER_INVOKE``: After the handler returns, before response formatting.
    - ``BEFORE_RESPOND``: After formatting, before the response is sent to the caller.
    """

    BEFORE_ROUTE = "before_route"
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_RESPOND = "before_respond"


# Middleware signature: receives context and the current payload, returns
# the (possibly transformed) payload or None to leave it unchanged.
MiddlewareHandler = Callable[[ExecContext, object], Awaitable[object | None]]
"""Async callable that intercepts a pipeline stage.

Args:
    ctx: The current execution context.
    payload: Stage-specific data (e.g. the routed request, handler result).

Returns:
    A replacement payload to forward downstream, or ``None`` to keep
    the original payload unchanged.
"""

from nerva.middleware.builtins import (  # noqa: E402
    permission_checker,
    request_logger,
    usage_tracker,
)

__all__ = [
    "MiddlewareStage",
    "MiddlewareHandler",
    "permission_checker",
    "request_logger",
    "usage_tracker",
]
