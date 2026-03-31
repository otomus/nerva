"""Tests for InMemoryHotMemory and TieredMemory (N-175)."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.memory.hot import InMemoryHotMemory
from nerva.memory.tiered import TieredMemory
from tests.conftest import make_ctx


# ===================================================================
# InMemoryHotMemory
# ===================================================================


class TestInMemoryHotMemory:
    """Hot tier: add, get, clear, pruning."""

    @pytest.mark.asyncio
    async def test_add_and_get(self):
        hot = InMemoryHotMemory()
        await hot.add_message("user", "hello", "s1")
        await hot.add_message("assistant", "hi", "s1")
        msgs = await hot.get_conversation("s1")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "hello"}
        assert msgs[1] == {"role": "assistant", "content": "hi"}

    @pytest.mark.asyncio
    async def test_get_returns_copy(self):
        hot = InMemoryHotMemory()
        await hot.add_message("user", "msg", "s1")
        msgs = await hot.get_conversation("s1")
        msgs.clear()
        assert len(await hot.get_conversation("s1")) == 1

    @pytest.mark.asyncio
    async def test_clear_removes_session(self):
        hot = InMemoryHotMemory()
        await hot.add_message("user", "msg", "s1")
        await hot.clear("s1")
        assert await hot.get_conversation("s1") == []

    @pytest.mark.asyncio
    async def test_clear_nonexistent_session_is_noop(self):
        hot = InMemoryHotMemory()
        await hot.clear("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_pruning_at_max_messages(self):
        hot = InMemoryHotMemory(max_messages=3)
        for i in range(5):
            await hot.add_message("user", f"msg{i}", "s1")
        msgs = await hot.get_conversation("s1")
        assert len(msgs) == 3
        # Oldest messages pruned — should have msg2, msg3, msg4
        assert msgs[0]["content"] == "msg2"
        assert msgs[2]["content"] == "msg4"

    @pytest.mark.asyncio
    async def test_separate_sessions_dont_leak(self):
        hot = InMemoryHotMemory()
        await hot.add_message("user", "session A", "sa")
        await hot.add_message("user", "session B", "sb")
        assert len(await hot.get_conversation("sa")) == 1
        assert len(await hot.get_conversation("sb")) == 1
        assert (await hot.get_conversation("sa"))[0]["content"] == "session A"

    @pytest.mark.asyncio
    async def test_empty_role_raises(self):
        hot = InMemoryHotMemory()
        with pytest.raises(ValueError, match="role"):
            await hot.add_message("", "content", "s1")

    @pytest.mark.asyncio
    async def test_whitespace_only_role_raises(self):
        hot = InMemoryHotMemory()
        with pytest.raises(ValueError, match="role"):
            await hot.add_message("   ", "content", "s1")

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        hot = InMemoryHotMemory()
        with pytest.raises(ValueError, match="content"):
            await hot.add_message("user", "", "s1")

    @pytest.mark.asyncio
    async def test_whitespace_only_content_raises(self):
        hot = InMemoryHotMemory()
        with pytest.raises(ValueError, match="content"):
            await hot.add_message("user", "  \t  ", "s1")


# ===================================================================
# Fake warm/cold tiers for TieredMemory tests
# ===================================================================


class FakeWarmTier:
    """In-memory warm tier for testing."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []

    async def get_episodes(self, query: str, session_id: str) -> list[str]:
        return [f"episode about {query}"]

    async def get_facts(self, query: str, session_id: str) -> list[str]:
        return [f"fact about {query}"]

    async def store(self, content: str, session_id: str) -> None:
        self.stored.append((content, session_id))


class FakeColdTier:
    """In-memory cold tier for testing."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []

    async def search(self, query: str, scope: str) -> list[str]:
        return [f"knowledge about {query}"]

    async def store(self, content: str, scope: str) -> None:
        self.stored.append((content, scope))


# ===================================================================
# TieredMemory
# ===================================================================


class TestTieredMemory:
    """Tiered memory orchestration across hot/warm/cold."""

    @pytest.mark.asyncio
    async def test_all_tiers_none_returns_empty_context(self):
        mem = TieredMemory()
        ctx = make_ctx(session_id="s1")
        result = await mem.recall("query", ctx)
        assert result.conversation == []
        assert result.episodes == []
        assert result.facts == []
        assert result.knowledge == []
        assert result.token_count == 0

    @pytest.mark.asyncio
    async def test_recall_with_all_tiers(self):
        hot = InMemoryHotMemory()
        warm = FakeWarmTier()
        cold = FakeColdTier()
        mem = TieredMemory(hot=hot, warm=warm, cold=cold)
        ctx = make_ctx(session_id="s1")

        await hot.add_message("user", "hi", "s1")
        result = await mem.recall("test query", ctx)

        assert len(result.conversation) == 1
        assert result.conversation[0]["content"] == "hi"
        assert any("episode" in e for e in result.episodes)
        assert any("fact" in f for f in result.facts)
        assert any("knowledge" in k for k in result.knowledge)

    @pytest.mark.asyncio
    async def test_store_routes_to_hot_tier(self):
        hot = InMemoryHotMemory()
        mem = TieredMemory(hot=hot)
        ctx = make_ctx(session_id="s1")

        event = MemoryEvent(content="hello", tier=MemoryTier.HOT, source="user")
        await mem.store(event, ctx)

        msgs = await hot.get_conversation("s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_store_routes_to_warm_tier(self):
        warm = FakeWarmTier()
        mem = TieredMemory(warm=warm)
        ctx = make_ctx(session_id="s1")

        event = MemoryEvent(content="warm data", tier=MemoryTier.WARM)
        await mem.store(event, ctx)

        assert len(warm.stored) == 1
        assert warm.stored[0][0] == "warm data"

    @pytest.mark.asyncio
    async def test_store_routes_to_cold_tier(self):
        cold = FakeColdTier()
        mem = TieredMemory(cold=cold)
        ctx = make_ctx(session_id="s1")

        event = MemoryEvent(content="cold data", tier=MemoryTier.COLD)
        await mem.store(event, ctx)

        assert len(cold.stored) == 1
        assert cold.stored[0][0] == "cold data"

    @pytest.mark.asyncio
    async def test_store_to_missing_tier_is_noop(self):
        mem = TieredMemory()
        ctx = make_ctx(session_id="s1")
        event = MemoryEvent(content="lost", tier=MemoryTier.WARM)
        await mem.store(event, ctx)  # should not raise

    @pytest.mark.asyncio
    async def test_recall_uses_request_id_when_no_session(self):
        hot = InMemoryHotMemory()
        mem = TieredMemory(hot=hot)
        ctx = make_ctx(session_id=None)

        # Store under request_id since session_id is None
        await hot.add_message("user", "test", ctx.request_id)
        result = await mem.recall("q", ctx)
        assert len(result.conversation) == 1

    @pytest.mark.asyncio
    async def test_token_budget_truncation(self):
        """With a tiny budget, not all content fits."""
        hot = InMemoryHotMemory()
        mem = TieredMemory(hot=hot, token_budget=1)
        ctx = make_ctx(session_id="s1")

        # Add many messages
        for i in range(10):
            await hot.add_message("user", f"message number {i} " * 20, "s1")

        result = await mem.recall("query", ctx)
        # With budget=1 token, very few (possibly zero) messages fit
        assert result.token_count <= 1

    @pytest.mark.asyncio
    async def test_consolidate_is_noop(self):
        mem = TieredMemory()
        ctx = make_ctx(session_id="s1")
        await mem.consolidate(ctx)  # should not raise


# ===================================================================
# Edge cases
# ===================================================================


class TestTieredMemoryEdgeCases:
    """Boundary conditions for tiered memory."""

    @pytest.mark.asyncio
    async def test_empty_query(self):
        warm = FakeWarmTier()
        mem = TieredMemory(warm=warm)
        ctx = make_ctx(session_id="s1")
        result = await mem.recall("", ctx)
        assert isinstance(result, MemoryContext)

    @pytest.mark.asyncio
    async def test_very_long_content_storage(self):
        hot = InMemoryHotMemory()
        mem = TieredMemory(hot=hot)
        ctx = make_ctx(session_id="s1")
        long_content = "x" * 100_000
        event = MemoryEvent(content=long_content, tier=MemoryTier.HOT, source="user")
        await mem.store(event, ctx)
        msgs = await hot.get_conversation("s1")
        assert msgs[0]["content"] == long_content

    @pytest.mark.asyncio
    async def test_zero_token_budget_returns_empty(self):
        hot = InMemoryHotMemory()
        mem = TieredMemory(hot=hot, token_budget=0)
        ctx = make_ctx(session_id="s1")
        await hot.add_message("user", "hello", "s1")
        result = await mem.recall("q", ctx)
        assert result.conversation == []
        assert result.token_count == 0
