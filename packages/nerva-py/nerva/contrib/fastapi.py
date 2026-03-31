"""FastAPI / Starlette integration bridge for Nerva.

Provides ASGI middleware, FastAPI dependencies, JWT-to-Permissions mapping,
and SSE streaming helpers. All FastAPI/Starlette imports are conditional —
a clear error message is raised if the dependency is missing.

Usage::

    from nerva.contrib.fastapi import NervaMiddleware, get_nerva_ctx

    app = FastAPI()
    app.add_middleware(NervaMiddleware)

    @app.post("/chat")
    async def chat(request: Request, ctx: ExecContext = Depends(get_nerva_ctx)):
        response = await orchestrator.handle(body.message, ctx=ctx)
        return {"text": response.text}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, AsyncIterator, TYPE_CHECKING

from nerva.context import ExecContext, Permissions, Scope

if TYPE_CHECKING:
    from nerva.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional imports — fail fast with a helpful message
# ---------------------------------------------------------------------------

_MISSING_FASTAPI_MSG = (
    "FastAPI integration requires 'fastapi' and 'starlette'. "
    "Install with: pip install fastapi"
)

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import StreamingResponse
    from starlette.types import ASGIApp, Receive, Send, Scope as ASGIScope
except ImportError:
    raise ImportError(_MISSING_FASTAPI_MSG)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUEST_ID_HEADER = "X-Request-Id"
"""HTTP header used to propagate an external request ID."""

AUTHORIZATION_HEADER = "Authorization"
"""HTTP header carrying the Bearer token."""

NERVA_CTX_STATE_KEY = "nerva_ctx"
"""Key used to store the ExecContext on Starlette request state."""

SSE_CONTENT_TYPE = "text/event-stream"
"""MIME type for Server-Sent Events responses."""

BEARER_PREFIX = "Bearer "
"""Prefix stripped from Authorization header values."""


# ---------------------------------------------------------------------------
# NervaMiddleware — ASGI middleware that creates ExecContext from headers
# ---------------------------------------------------------------------------


class NervaMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that creates an ``ExecContext`` from request headers.

    Reads ``X-Request-Id`` and ``Authorization`` headers to populate the
    context's ``request_id`` and ``user_id``. The context is stored on
    ``request.state.nerva_ctx`` for downstream handlers.

    Args:
        app: The ASGI application to wrap.
        default_scope: Default memory scope when none is inferred.
    """

    def __init__(
        self,
        app: ASGIApp,
        default_scope: Scope = Scope.SESSION,
    ) -> None:
        super().__init__(app)
        self._default_scope = default_scope

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        """Create ExecContext from headers and attach to request state.

        Args:
            request: The incoming HTTP request.
            call_next: Callback to invoke the next middleware/handler.

        Returns:
            The HTTP response from downstream.
        """
        ctx = _build_ctx_from_request(request, self._default_scope)
        request.state.nerva_ctx = ctx
        return await call_next(request)


# ---------------------------------------------------------------------------
# get_nerva_ctx — FastAPI dependency
# ---------------------------------------------------------------------------


def get_nerva_ctx(request: Request) -> ExecContext:
    """FastAPI dependency that extracts the ``ExecContext`` from request state.

    Requires ``NervaMiddleware`` to be installed. Raises ``RuntimeError``
    if the context has not been set.

    Args:
        request: The FastAPI/Starlette Request object.

    Returns:
        The ``ExecContext`` attached by ``NervaMiddleware``.

    Raises:
        RuntimeError: If ``NervaMiddleware`` has not populated the context.
    """
    ctx = getattr(request.state, NERVA_CTX_STATE_KEY, None)
    if ctx is None:
        raise RuntimeError(
            "ExecContext not found on request.state. "
            "Ensure NervaMiddleware is installed."
        )
    return ctx


# ---------------------------------------------------------------------------
# permissions_from_bearer — JWT-to-Permissions mapper
# ---------------------------------------------------------------------------


def permissions_from_bearer(
    token: str,
    decode_fn: Callable[[str], dict[str, Any]],
) -> Permissions:
    """Map a JWT bearer token to a Nerva ``Permissions`` object.

    Delegates decoding to the caller-supplied ``decode_fn`` (e.g. ``jwt.decode``).
    Expects the decoded payload to contain optional ``roles``, ``allowed_tools``,
    and ``allowed_agents`` fields.

    Args:
        token: Raw JWT string (without the ``Bearer `` prefix).
        decode_fn: Callable that decodes the token and returns a claims dict.

    Returns:
        A ``Permissions`` instance populated from the JWT claims.

    Raises:
        ValueError: If *token* is empty or whitespace-only.
        Exception: Any exception from *decode_fn* propagates unchanged.
    """
    if not token or not token.strip():
        raise ValueError("Token must be a non-empty string")

    claims = decode_fn(token)
    roles = frozenset(claims.get("roles", []))
    allowed_tools = _optional_frozenset(claims.get("allowed_tools"))
    allowed_agents = _optional_frozenset(claims.get("allowed_agents"))

    return Permissions(
        roles=roles,
        allowed_tools=allowed_tools,
        allowed_agents=allowed_agents,
    )


# ---------------------------------------------------------------------------
# streaming_response — SSE streaming helper
# ---------------------------------------------------------------------------


async def streaming_response(
    orchestrator: Orchestrator,
    message: str,
    ctx: ExecContext,
) -> StreamingResponse:
    """Return a ``StreamingResponse`` that streams orchestrator output as SSE events.

    Each chunk is sent as a ``data:`` line followed by a blank line.
    When the stream ends, a ``data: [DONE]`` sentinel is sent.

    Args:
        orchestrator: The Nerva orchestrator instance.
        message: User message to process.
        ctx: Execution context for this request.

    Returns:
        A Starlette ``StreamingResponse`` with ``text/event-stream`` content type.
    """

    async def _event_generator() -> AsyncIterator[str]:
        async for chunk in orchestrator.stream(message, ctx=ctx):
            yield _format_sse_event(chunk)
        yield _format_sse_event("[DONE]")

    return StreamingResponse(
        _event_generator(),
        media_type=SSE_CONTENT_TYPE,
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_ctx_from_request(
    request: Request,
    default_scope: Scope,
) -> ExecContext:
    """Extract request headers and build an ExecContext.

    Args:
        request: The Starlette request.
        default_scope: Fallback memory scope.

    Returns:
        A populated ``ExecContext``.
    """
    request_id = request.headers.get(REQUEST_ID_HEADER.lower())
    auth_header = request.headers.get(AUTHORIZATION_HEADER.lower())
    user_id = _extract_user_id_from_auth(auth_header)

    ctx = ExecContext.create(
        user_id=user_id,
        memory_scope=default_scope,
    )

    if request_id:
        # Override the auto-generated request_id with the external one
        object.__setattr__(ctx, "request_id", request_id)

    return ctx


def _extract_user_id_from_auth(auth_header: str | None) -> str | None:
    """Pull a user identifier from the Authorization header.

    For Bearer tokens, returns the raw token as a user identifier placeholder.
    Real applications should decode the JWT to extract the subject claim.

    Args:
        auth_header: Raw Authorization header value, or ``None``.

    Returns:
        A user identifier string, or ``None`` if no auth header is present.
    """
    if not auth_header:
        return None
    if auth_header.startswith(BEARER_PREFIX):
        return auth_header[len(BEARER_PREFIX):]
    return auth_header


def _optional_frozenset(value: Any) -> frozenset[str] | None:
    """Convert a list to a frozenset, or return None if the value is None.

    Args:
        value: A list of strings, or ``None``.

    Returns:
        A ``frozenset[str]`` or ``None``.
    """
    if value is None:
        return None
    return frozenset(value)


def _format_sse_event(data: str) -> str:
    """Format a string as an SSE data event.

    Args:
        data: The event payload.

    Returns:
        SSE-formatted string with ``data:`` prefix and trailing newlines.
    """
    return f"data: {data}\n\n"


__all__ = [
    "NervaMiddleware",
    "get_nerva_ctx",
    "permissions_from_bearer",
    "streaming_response",
    "REQUEST_ID_HEADER",
    "AUTHORIZATION_HEADER",
    "NERVA_CTX_STATE_KEY",
    "SSE_CONTENT_TYPE",
    "BEARER_PREFIX",
]
