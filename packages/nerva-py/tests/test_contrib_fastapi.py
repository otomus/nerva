"""Tests for the FastAPI / Starlette integration bridge."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nerva.context import ExecContext, Permissions, Scope


# ---------------------------------------------------------------------------
# Starlette/FastAPI mocks — lightweight stand-ins for the real objects
# ---------------------------------------------------------------------------


class _FakeState:
    """Mimics Starlette request.state — an arbitrary attribute bag."""

    pass


class _FakeRequest:
    """Mimics a Starlette Request with headers and state."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.state = _FakeState()


class _FakeStreamingResponse:
    """Captures what StreamingResponse would do."""

    def __init__(self, generator: Any, media_type: str, headers: dict[str, str]) -> None:
        self.generator = generator
        self.media_type = media_type
        self.headers = headers


# Patch starlette so the import succeeds without installing it
import sys
import types

_starlette_mod = types.ModuleType("starlette")
_starlette_requests = types.ModuleType("starlette.requests")
_starlette_responses = types.ModuleType("starlette.responses")
_starlette_types = types.ModuleType("starlette.types")
_starlette_middleware = types.ModuleType("starlette.middleware")
_starlette_middleware_base = types.ModuleType("starlette.middleware.base")


class _BaseHTTPMiddleware:
    """Minimal stand-in for Starlette's BaseHTTPMiddleware."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        raise NotImplementedError


_starlette_middleware_base.BaseHTTPMiddleware = _BaseHTTPMiddleware  # type: ignore[attr-defined]
_starlette_requests.Request = _FakeRequest  # type: ignore[attr-defined]
_starlette_responses.StreamingResponse = _FakeStreamingResponse  # type: ignore[attr-defined]
_starlette_types.ASGIApp = Any  # type: ignore[attr-defined]
_starlette_types.Receive = Any  # type: ignore[attr-defined]
_starlette_types.Send = Any  # type: ignore[attr-defined]
_starlette_types.Scope = Any  # type: ignore[attr-defined]

sys.modules.setdefault("starlette", _starlette_mod)
sys.modules.setdefault("starlette.requests", _starlette_requests)
sys.modules.setdefault("starlette.responses", _starlette_responses)
sys.modules.setdefault("starlette.types", _starlette_types)
sys.modules.setdefault("starlette.middleware", _starlette_middleware)
sys.modules.setdefault("starlette.middleware.base", _starlette_middleware_base)

# Now import the module under test
from nerva.contrib.fastapi import (
    BEARER_PREFIX,
    NERVA_CTX_STATE_KEY,
    REQUEST_ID_HEADER,
    SSE_CONTENT_TYPE,
    NervaMiddleware,
    get_nerva_ctx,
    permissions_from_bearer,
    streaming_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_app() -> MagicMock:
    """A dummy ASGI app."""
    return MagicMock()


# ---------------------------------------------------------------------------
# NervaMiddleware tests
# ---------------------------------------------------------------------------


class TestNervaMiddleware:
    """Tests for the ASGI middleware that creates ExecContext."""

    @pytest.mark.asyncio
    async def test_creates_ctx_from_headers(self, fake_app: MagicMock) -> None:
        """Middleware populates ExecContext on request state."""
        mw = NervaMiddleware(fake_app)
        request = _FakeRequest({"X-Request-Id": "req-123", "Authorization": "Bearer tok-abc"})
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        assert isinstance(ctx, ExecContext)
        assert ctx.request_id == "req-123"
        assert ctx.user_id == "tok-abc"
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_headers_yields_anonymous_ctx(self, fake_app: MagicMock) -> None:
        """No headers results in an anonymous context with auto-generated IDs."""
        mw = NervaMiddleware(fake_app)
        request = _FakeRequest()
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        assert isinstance(ctx, ExecContext)
        assert ctx.user_id is None
        assert ctx.request_id  # auto-generated, not empty

    @pytest.mark.asyncio
    async def test_custom_default_scope(self, fake_app: MagicMock) -> None:
        """Custom default_scope is applied to the created context."""
        mw = NervaMiddleware(fake_app, default_scope=Scope.USER)
        request = _FakeRequest()
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        assert ctx.memory_scope == Scope.USER

    @pytest.mark.asyncio
    async def test_non_bearer_auth_used_as_user_id(self, fake_app: MagicMock) -> None:
        """Non-Bearer auth header value is used directly as user_id."""
        mw = NervaMiddleware(fake_app)
        request = _FakeRequest({"Authorization": "api-key-xyz"})
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        assert ctx.user_id == "api-key-xyz"


# ---------------------------------------------------------------------------
# get_nerva_ctx tests
# ---------------------------------------------------------------------------


class TestGetNervaCtx:
    """Tests for the FastAPI dependency."""

    def test_extracts_ctx_from_state(self) -> None:
        """Returns the ExecContext when present on request state."""
        request = _FakeRequest()
        expected_ctx = ExecContext.create(user_id="user-1")
        request.state.nerva_ctx = expected_ctx

        result = get_nerva_ctx(request)
        assert result is expected_ctx

    def test_raises_when_ctx_missing(self) -> None:
        """Raises RuntimeError when middleware was not installed."""
        request = _FakeRequest()

        with pytest.raises(RuntimeError, match="ExecContext not found"):
            get_nerva_ctx(request)


# ---------------------------------------------------------------------------
# permissions_from_bearer tests
# ---------------------------------------------------------------------------


class TestPermissionsFromBearer:
    """Tests for JWT-to-Permissions mapping."""

    def test_maps_full_claims(self) -> None:
        """All JWT claim fields are mapped to Permissions."""
        claims = {
            "roles": ["admin", "user"],
            "allowed_tools": ["tool_a", "tool_b"],
            "allowed_agents": ["agent_x"],
        }
        perms = permissions_from_bearer("valid-token", lambda _t: claims)

        assert perms.has_role("admin")
        assert perms.has_role("user")
        assert perms.can_use_tool("tool_a")
        assert not perms.can_use_tool("tool_c")
        assert perms.can_use_agent("agent_x")
        assert not perms.can_use_agent("agent_y")

    def test_empty_claims_yields_unrestricted(self) -> None:
        """Missing claim fields default to unrestricted."""
        perms = permissions_from_bearer("tok", lambda _t: {})

        assert perms.roles == frozenset()
        assert perms.allowed_tools is None
        assert perms.allowed_agents is None

    def test_empty_token_raises(self) -> None:
        """Empty token raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            permissions_from_bearer("", lambda _t: {})

    def test_whitespace_token_raises(self) -> None:
        """Whitespace-only token raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            permissions_from_bearer("   ", lambda _t: {})

    def test_decode_fn_exception_propagates(self) -> None:
        """Exceptions from decode_fn are not swallowed."""
        def bad_decode(_t: str) -> dict[str, Any]:
            raise RuntimeError("invalid signature")

        with pytest.raises(RuntimeError, match="invalid signature"):
            permissions_from_bearer("tok", bad_decode)

    def test_roles_with_non_string_values(self) -> None:
        """Non-string items in roles list are included via frozenset (type coercion)."""
        claims = {"roles": [123, "admin"]}
        perms = permissions_from_bearer("tok", lambda _t: claims)
        # frozenset accepts any iterable — 123 becomes part of it
        assert "admin" in perms.roles


# ---------------------------------------------------------------------------
# streaming_response tests
# ---------------------------------------------------------------------------


class TestStreamingResponse:
    """Tests for the SSE streaming helper."""

    @pytest.mark.asyncio
    async def test_streams_chunks_as_sse(self) -> None:
        """Chunks are formatted as SSE data events."""
        chunks = ["Hello", " world"]

        orchestrator = MagicMock()

        async def fake_stream(message: str, *, ctx: ExecContext) -> Any:
            for chunk in chunks:
                yield chunk

        orchestrator.stream = fake_stream

        ctx = ExecContext.create()
        response = await streaming_response(orchestrator, "hi", ctx)

        assert response.media_type == SSE_CONTENT_TYPE
        assert response.headers["Cache-Control"] == "no-cache"

        # Consume the generator
        collected = []
        async for event in response.generator:
            collected.append(event)

        assert collected == [
            "data: Hello\n\n",
            "data:  world\n\n",
            "data: [DONE]\n\n",
        ]

    @pytest.mark.asyncio
    async def test_empty_stream_sends_done_sentinel(self) -> None:
        """An empty stream still sends the [DONE] sentinel."""
        orchestrator = MagicMock()

        async def empty_stream(message: str, *, ctx: ExecContext) -> Any:
            return
            yield  # noqa: makes this an async generator

        orchestrator.stream = empty_stream

        ctx = ExecContext.create()
        response = await streaming_response(orchestrator, "hi", ctx)

        collected = []
        async for event in response.generator:
            collected.append(event)

        assert collected == ["data: [DONE]\n\n"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for the FastAPI integration."""

    @pytest.mark.asyncio
    async def test_bearer_prefix_case_sensitive(self, fake_app: MagicMock) -> None:
        """Only 'Bearer ' prefix (capital B) is stripped."""
        mw = NervaMiddleware(fake_app)
        request = _FakeRequest({"Authorization": "bearer lowercase-tok"})
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        # "bearer" (lowercase) doesn't match "Bearer " so full string is user_id
        assert ctx.user_id == "bearer lowercase-tok"

    @pytest.mark.asyncio
    async def test_empty_authorization_header(self, fake_app: MagicMock) -> None:
        """Empty Authorization header yields None user_id."""
        mw = NervaMiddleware(fake_app)
        request = _FakeRequest({"Authorization": ""})
        call_next = AsyncMock(return_value="response")

        await mw.dispatch(request, call_next)

        ctx = request.state.nerva_ctx
        assert ctx.user_id is None

    def test_get_nerva_ctx_with_wrong_type_on_state(self) -> None:
        """If something else is stored under the key, it still returns it."""
        request = _FakeRequest()
        request.state.nerva_ctx = "not-a-context"

        # The function returns whatever is there — type checking is caller's job
        result = get_nerva_ctx(request)
        assert result == "not-a-context"
