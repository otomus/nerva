"""Shared fixtures for Nerva tests."""

from __future__ import annotations

import asyncio

import pytest

from nerva.context import ExecContext, Permissions, Scope


@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test (needed for asyncio.Event in ExecContext)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def make_ctx(
    *,
    user_id: str | None = "test-user",
    session_id: str | None = "test-session",
    roles: frozenset[str] | None = None,
    allowed_tools: frozenset[str] | None = None,
    allowed_agents: frozenset[str] | None = None,
    memory_scope: Scope = Scope.SESSION,
    timeout_seconds: float | None = None,
    metadata: dict[str, str] | None = None,
) -> ExecContext:
    """Build an ExecContext with sensible test defaults.

    Args:
        user_id: Authenticated user, or None for anonymous.
        session_id: Session identifier.
        roles: Role names for permissions.
        allowed_tools: Tool allowlist, None = unrestricted.
        allowed_agents: Agent allowlist, None = unrestricted.
        memory_scope: Memory isolation boundary.
        timeout_seconds: Optional timeout.
        metadata: Arbitrary string tags.

    Returns:
        A fully initialised ExecContext.
    """
    permissions = Permissions(
        roles=roles or frozenset(),
        allowed_tools=allowed_tools,
        allowed_agents=allowed_agents,
    )
    ctx = ExecContext.create(
        user_id=user_id,
        session_id=session_id,
        permissions=permissions,
        memory_scope=memory_scope,
        timeout_seconds=timeout_seconds,
    )
    if metadata:
        ctx.metadata.update(metadata)
    return ctx
