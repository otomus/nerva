"""Built-in middleware — reusable cross-cutting concerns.

Provides factory functions that return ready-to-register middleware handlers
for common needs: request logging, permission checking, and usage tracking.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from nerva.context import ExecContext
from nerva.runtime import AgentInput, AgentResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request logger
# ---------------------------------------------------------------------------


def request_logger(
    log_level: int = logging.INFO,
) -> tuple:
    """Create before_route and after_invoke middleware that log request lifecycle.

    Logs the incoming message and handler at ``BEFORE_ROUTE``, and the
    handler name plus wall-clock duration at ``AFTER_INVOKE``.

    Args:
        log_level: Python logging level for the messages. Defaults to INFO.

    Returns:
        A ``(before_route_handler, after_invoke_handler)`` tuple ready for
        registration on the orchestrator.
    """
    _start_times: dict[str, float] = {}

    async def log_before_route(ctx: ExecContext, payload: object) -> object | None:
        """Record the request start time and log the incoming message."""
        _start_times[ctx.request_id] = time.monotonic()
        message = payload if isinstance(payload, str) else str(payload)
        truncated = message[:200] + "..." if len(message) > 200 else message
        logger.log(
            log_level,
            "[%s] Incoming request: %s",
            ctx.request_id[:8],
            truncated,
        )
        return None

    async def log_after_invoke(ctx: ExecContext, payload: object) -> object | None:
        """Log the handler result and elapsed duration."""
        start = _start_times.pop(ctx.request_id, None)
        duration_ms = (time.monotonic() - start) * 1000 if start is not None else -1
        handler_name = payload.handler if isinstance(payload, AgentResult) else "unknown"
        status = payload.status if isinstance(payload, AgentResult) else "unknown"
        logger.log(
            log_level,
            "[%s] Handler=%s status=%s duration=%.1fms",
            ctx.request_id[:8],
            handler_name,
            status,
            duration_ms,
        )
        return None

    return log_before_route, log_after_invoke


# ---------------------------------------------------------------------------
# Permission checker
# ---------------------------------------------------------------------------


def permission_checker(
    required_roles: frozenset[str] | None = None,
) -> object:
    """Create a before_invoke middleware that verifies context permissions.

    Checks that the execution context carries the required roles before
    allowing handler invocation. If a role is missing, the handler name
    is replaced with an error ``AgentInput`` that signals denial.

    Args:
        required_roles: Set of role names that must be present. If ``None``,
            the middleware is a no-op (all requests pass).

    Returns:
        An async middleware handler for the ``BEFORE_INVOKE`` stage.
    """

    async def check_permissions(ctx: ExecContext, payload: object) -> object | None:
        """Verify required roles are present in the context permissions."""
        if required_roles is None:
            return None

        for role in required_roles:
            if not ctx.permissions.has_role(role):
                ctx.add_event(
                    "permission.denied",
                    missing_role=role,
                    user_id=ctx.user_id or "anonymous",
                )
                logger.warning(
                    "[%s] Permission denied: missing role '%s'",
                    ctx.request_id[:8],
                    role,
                )
                return AgentInput(message=f"Permission denied: missing role '{role}'")

        return None

    return check_permissions


# ---------------------------------------------------------------------------
# Usage tracker
# ---------------------------------------------------------------------------


def usage_tracker() -> object:
    """Create an after_invoke middleware that records token usage as events.

    Reads ``ctx.token_usage`` after handler invocation and emits a
    ``usage.recorded`` event with prompt, completion, and total token counts.

    Returns:
        An async middleware handler for the ``AFTER_INVOKE`` stage.
    """

    async def track_usage(ctx: ExecContext, payload: object) -> object | None:
        """Record token usage from the execution context."""
        usage = ctx.token_usage
        ctx.add_event(
            "usage.recorded",
            prompt_tokens=str(usage.prompt_tokens),
            completion_tokens=str(usage.completion_tokens),
            total_tokens=str(usage.total_tokens),
            cost_usd=f"{usage.cost_usd:.6f}",
        )
        return None

    return track_usage
