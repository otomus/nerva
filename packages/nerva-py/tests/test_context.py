"""Tests for ExecContext, Permissions, TokenUsage, and InMemoryStreamSink (N-170)."""

from __future__ import annotations

import time

import pytest

from nerva.context import (
    ExecContext,
    InMemoryStreamSink,
    Permissions,
    Scope,
    TokenUsage,
)
from tests.conftest import make_ctx


# ===================================================================
# ExecContext.create
# ===================================================================


class TestExecContextCreate:
    """ExecContext.create() factory method."""

    def test_produces_unique_request_and_trace_ids(self):
        ctx_a = make_ctx()
        ctx_b = make_ctx()
        assert ctx_a.request_id != ctx_b.request_id
        assert ctx_a.trace_id != ctx_b.trace_id

    def test_ids_are_32_char_hex(self):
        ctx = make_ctx()
        assert len(ctx.request_id) == 32
        assert all(c in "0123456789abcdef" for c in ctx.request_id)
        assert len(ctx.trace_id) == 32

    def test_created_at_is_recent(self):
        before = time.time()
        ctx = make_ctx()
        after = time.time()
        assert before <= ctx.created_at <= after

    def test_defaults_to_unrestricted_permissions(self):
        ctx = ExecContext.create()
        assert ctx.permissions.can_use_tool("anything")
        assert ctx.permissions.can_use_agent("anything")

    def test_defaults_to_session_scope(self):
        ctx = make_ctx()
        assert ctx.memory_scope == Scope.SESSION

    def test_timeout_at_calculated_from_seconds(self):
        ctx = make_ctx(timeout_seconds=10.0)
        assert ctx.timeout_at is not None
        assert ctx.timeout_at > ctx.created_at

    def test_no_timeout_when_none(self):
        ctx = make_ctx(timeout_seconds=None)
        assert ctx.timeout_at is None

    def test_empty_spans_events_and_zero_tokens(self):
        ctx = make_ctx()
        assert ctx.spans == []
        assert ctx.events == []
        assert ctx.token_usage.prompt_tokens == 0
        assert ctx.token_usage.total_tokens == 0

    def test_metadata_starts_empty(self):
        ctx = ExecContext.create()
        assert ctx.metadata == {}

    def test_stream_defaults_to_none(self):
        ctx = make_ctx()
        assert ctx.stream is None


# ===================================================================
# ExecContext.child
# ===================================================================


class TestExecContextChild:
    """ExecContext.child() delegation."""

    def test_child_gets_new_request_id(self):
        parent = make_ctx()
        child = parent.child("sub_handler")
        assert child.request_id != parent.request_id

    def test_child_inherits_trace_id(self):
        parent = make_ctx()
        child = parent.child("sub_handler")
        assert child.trace_id == parent.trace_id

    def test_child_inherits_permissions(self):
        parent = make_ctx(allowed_tools=frozenset({"toolA"}))
        child = parent.child("sub_handler")
        assert child.permissions is parent.permissions

    def test_child_shares_cancellation_event(self):
        parent = make_ctx()
        child = parent.child("sub_handler")
        parent.cancelled.set()
        assert child.is_cancelled()

    def test_child_inherits_timeout(self):
        parent = make_ctx(timeout_seconds=30.0)
        child = parent.child("sub_handler")
        assert child.timeout_at == parent.timeout_at

    def test_child_has_root_span(self):
        parent = make_ctx()
        child = parent.child("my_handler")
        assert len(child.spans) == 1
        assert child.spans[0].name == "my_handler"
        assert child.spans[0].parent_id == parent.request_id

    def test_child_has_independent_events(self):
        parent = make_ctx()
        child = parent.child("sub")
        child.add_event("child_event")
        assert len(parent.events) == 0
        assert len(child.events) == 1

    def test_child_metadata_is_a_copy(self):
        parent = make_ctx(metadata={"key": "parent_val"})
        child = parent.child("sub")
        child.metadata["key"] = "child_val"
        assert parent.metadata["key"] == "parent_val"


# ===================================================================
# Permissions
# ===================================================================


class TestPermissions:
    """Permissions allowlist behaviour."""

    def test_none_allows_all_tools(self):
        perms = Permissions(allowed_tools=None)
        assert perms.can_use_tool("any_tool") is True

    def test_empty_frozenset_denies_all_tools(self):
        perms = Permissions(allowed_tools=frozenset())
        assert perms.can_use_tool("any_tool") is False

    def test_explicit_tool_allowed(self):
        perms = Permissions(allowed_tools=frozenset({"calc"}))
        assert perms.can_use_tool("calc") is True
        assert perms.can_use_tool("other") is False

    def test_none_allows_all_agents(self):
        perms = Permissions(allowed_agents=None)
        assert perms.can_use_agent("any_agent") is True

    def test_empty_frozenset_denies_all_agents(self):
        perms = Permissions(allowed_agents=frozenset())
        assert perms.can_use_agent("any_agent") is False

    def test_explicit_agent_allowed(self):
        perms = Permissions(allowed_agents=frozenset({"planner"}))
        assert perms.can_use_agent("planner") is True
        assert perms.can_use_agent("executor") is False

    def test_has_role_present(self):
        perms = Permissions(roles=frozenset({"admin"}))
        assert perms.has_role("admin") is True

    def test_has_role_absent(self):
        perms = Permissions(roles=frozenset())
        assert perms.has_role("admin") is False

    def test_frozen_immutability(self):
        perms = Permissions()
        with pytest.raises(AttributeError):
            perms.roles = frozenset({"hacker"})  # type: ignore[misc]


# ===================================================================
# TokenUsage
# ===================================================================


class TestTokenUsage:
    """TokenUsage.add() immutability and correctness."""

    def test_add_returns_new_instance(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.01)
        b = TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5, cost_usd=0.005)
        result = a.add(b)
        assert result is not a
        assert result is not b

    def test_add_sums_all_fields(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.01)
        b = TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5, cost_usd=0.005)
        result = a.add(b)
        assert result.prompt_tokens == 13
        assert result.completion_tokens == 7
        assert result.total_tokens == 20
        assert abs(result.cost_usd - 0.015) < 1e-9

    def test_add_does_not_mutate_operands(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.01)
        b = TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5, cost_usd=0.005)
        a.add(b)
        assert a.prompt_tokens == 10
        assert b.prompt_tokens == 3

    def test_add_zero_usage(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.01)
        zero = TokenUsage()
        result = a.add(zero)
        assert result.prompt_tokens == 10
        assert result.total_tokens == 15


# ===================================================================
# InMemoryStreamSink
# ===================================================================


class TestInMemoryStreamSink:
    """InMemoryStreamSink push/close lifecycle."""

    @pytest.mark.asyncio
    async def test_push_collects_chunks(self):
        sink = InMemoryStreamSink()
        await sink.push("hello ")
        await sink.push("world")
        assert sink.chunks == ["hello ", "world"]

    @pytest.mark.asyncio
    async def test_close_marks_closed(self):
        sink = InMemoryStreamSink()
        await sink.close()
        assert sink.closed is True

    @pytest.mark.asyncio
    async def test_push_after_close_raises(self):
        sink = InMemoryStreamSink()
        await sink.close()
        with pytest.raises(RuntimeError, match="Cannot push to a closed"):
            await sink.push("late")

    @pytest.mark.asyncio
    async def test_double_close_raises(self):
        sink = InMemoryStreamSink()
        await sink.close()
        with pytest.raises(RuntimeError, match="already closed"):
            await sink.close()

    @pytest.mark.asyncio
    async def test_empty_string_push(self):
        sink = InMemoryStreamSink()
        await sink.push("")
        assert sink.chunks == [""]

    @pytest.mark.asyncio
    async def test_unicode_chunks(self):
        sink = InMemoryStreamSink()
        await sink.push("\U0001f600")
        await sink.push("\u00e9\u00e8\u00ea")
        assert len(sink.chunks) == 2


# ===================================================================
# Timeout and cancellation
# ===================================================================


class TestTimeoutAndCancellation:
    """is_timed_out() and is_cancelled() helpers."""

    def test_is_timed_out_false_when_no_timeout(self):
        ctx = make_ctx(timeout_seconds=None)
        assert ctx.is_timed_out() is False

    def test_is_timed_out_false_when_in_future(self):
        ctx = make_ctx(timeout_seconds=9999)
        assert ctx.is_timed_out() is False

    def test_is_timed_out_true_when_expired(self):
        ctx = ExecContext.create(timeout_seconds=0.0)
        # timeout_at == created_at, time.time() > that immediately
        time.sleep(0.001)
        assert ctx.is_timed_out() is True

    def test_is_cancelled_false_initially(self):
        ctx = make_ctx()
        assert ctx.is_cancelled() is False

    def test_is_cancelled_true_after_set(self):
        ctx = make_ctx()
        ctx.cancelled.set()
        assert ctx.is_cancelled() is True


# ===================================================================
# Edge cases
# ===================================================================


class TestExecContextEdgeCases:
    """Boundary and degenerate inputs."""

    def test_very_long_user_id(self):
        long_id = "u" * 10_000
        ctx = make_ctx(user_id=long_id)
        assert ctx.user_id == long_id

    def test_empty_string_user_id(self):
        ctx = make_ctx(user_id="")
        assert ctx.user_id == ""

    def test_special_characters_in_metadata(self):
        ctx = make_ctx()
        ctx.metadata["key\n\t\0"] = "val\"'\\{}"
        assert "key\n\t\0" in ctx.metadata

    def test_add_span_and_event(self):
        ctx = make_ctx()
        span = ctx.add_span("test_span")
        assert span.name == "test_span"
        assert span.ended_at is None
        event = ctx.add_event("test_event", foo="bar")
        assert event.name == "test_event"
        assert event.attributes == {"foo": "bar"}

    def test_record_tokens(self):
        ctx = make_ctx()
        ctx.record_tokens(TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8))
        assert ctx.token_usage.prompt_tokens == 5
        ctx.record_tokens(TokenUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3))
        assert ctx.token_usage.prompt_tokens == 7

    def test_elapsed_seconds_is_non_negative(self):
        ctx = make_ctx()
        assert ctx.elapsed_seconds() >= 0
