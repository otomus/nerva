"""Tiered memory — orchestrates hot, warm, and cold tiers with scope isolation.

Assembles ``MemoryContext`` by querying each tier independently and
merging results under a token budget. Each tier is optional; missing
tiers produce empty results.
"""

from __future__ import annotations

from typing import Protocol

from nerva.context import ExecContext
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.memory.hot import InMemoryHotMemory

DEFAULT_TOKEN_BUDGET = 4000
"""Maximum estimated tokens for recalled context."""

CHARS_PER_TOKEN = 4
"""Rough character-to-token ratio for budget estimation."""


# ---------------------------------------------------------------------------
# Tier protocols — minimal interfaces for warm and cold backends
# ---------------------------------------------------------------------------


class WarmTier(Protocol):
    """Key-value store for episodes and facts."""

    async def get_episodes(self, query: str, session_id: str) -> list[str]:
        """Retrieve relevant episodes for a query.

        Args:
            query: Search query.
            session_id: Session scope.

        Returns:
            List of episode strings, most relevant first.
        """
        ...

    async def get_facts(self, query: str, session_id: str) -> list[str]:
        """Retrieve relevant facts for a query.

        Args:
            query: Search query.
            session_id: Session scope.

        Returns:
            List of fact strings, most relevant first.
        """
        ...

    async def store(self, content: str, session_id: str) -> None:
        """Store content in the warm tier.

        Args:
            content: Content to persist.
            session_id: Session scope.
        """
        ...


class ColdTier(Protocol):
    """Vector search store for long-term knowledge."""

    async def search(self, query: str, scope: str) -> list[str]:
        """Search for relevant knowledge entries.

        Args:
            query: Semantic search query.
            scope: Memory scope string for filtering.

        Returns:
            List of knowledge strings, most relevant first.
        """
        ...

    async def store(self, content: str, scope: str) -> None:
        """Store content in the cold tier.

        Args:
            content: Content to persist.
            scope: Memory scope string for filtering.
        """
        ...


# ---------------------------------------------------------------------------
# TieredMemory
# ---------------------------------------------------------------------------


class TieredMemory:
    """Memory implementation that orchestrates three tiers.

    Hot: session conversation (in-memory or external store).
    Warm: episodes and facts (key-value store).
    Cold: long-term knowledge (vector search).

    Each tier is optional. If a tier is not provided, that part
    of ``recall`` returns empty results and ``store`` is a no-op
    for that tier.

    Args:
        hot: Hot tier implementation (session state).
        warm: Warm tier implementation (episodes/facts).
        cold: Cold tier implementation (vector search).
        token_budget: Maximum estimated tokens for recalled context.
    """

    def __init__(
        self,
        hot: InMemoryHotMemory | None = None,
        warm: WarmTier | None = None,
        cold: ColdTier | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> None:
        self._hot = hot
        self._warm = warm
        self._cold = cold
        self._token_budget = token_budget

    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        """Retrieve relevant context from all available tiers.

        Queries each tier independently, then assembles and truncates
        results to fit within the token budget.

        Args:
            query: Search query for relevant memories.
            ctx: Execution context with session identity and memory scope.

        Returns:
            MemoryContext assembled from all available tiers.
        """
        session_id = ctx.session_id or ctx.request_id
        scope_value = ctx.memory_scope.value

        conversation = await self._recall_hot(session_id)
        episodes = await self._recall_warm_episodes(query, session_id)
        facts = await self._recall_warm_facts(query, session_id)
        knowledge = await self._recall_cold(query, scope_value)

        return self._assemble_within_budget(
            conversation, episodes, facts, knowledge,
        )

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        """Route an event to the appropriate tier based on ``event.tier``.

        Args:
            event: Memory event to store.
            ctx: Execution context providing scope and identity.
        """
        session_id = ctx.session_id or ctx.request_id
        scope_value = (event.scope or ctx.memory_scope).value

        if event.tier == MemoryTier.HOT:
            await self._store_hot(event, session_id)
        elif event.tier == MemoryTier.WARM:
            await self._store_warm(event, session_id)
        elif event.tier == MemoryTier.COLD:
            await self._store_cold(event, scope_value)

    async def consolidate(self, ctx: ExecContext) -> None:
        """Promote, merge, or expire memories across tiers.

        Currently a no-op placeholder. Future implementations will
        move hot conversations into warm episodes and warm facts
        into cold knowledge based on age and relevance signals.

        Args:
            ctx: Execution context.
        """

    # -- Hot tier helpers ---------------------------------------------------

    async def _recall_hot(self, session_id: str) -> list[dict[str, str]]:
        """Retrieve conversation from the hot tier.

        Args:
            session_id: Session to retrieve.

        Returns:
            List of message dicts, or empty if no hot tier.
        """
        if self._hot is None:
            return []
        return await self._hot.get_conversation(session_id)

    async def _store_hot(self, event: MemoryEvent, session_id: str) -> None:
        """Store a message in the hot tier.

        Args:
            event: Memory event with content to store.
            session_id: Target session.
        """
        if self._hot is None:
            return
        await self._hot.add_message(
            role=event.source or "system",
            content=event.content,
            session_id=session_id,
        )

    # -- Warm tier helpers --------------------------------------------------

    async def _recall_warm_episodes(
        self, query: str, session_id: str,
    ) -> list[str]:
        """Retrieve episodes from the warm tier.

        Args:
            query: Search query.
            session_id: Session scope.

        Returns:
            List of episode strings, or empty if no warm tier.
        """
        if self._warm is None:
            return []
        return await self._warm.get_episodes(query, session_id)

    async def _recall_warm_facts(
        self, query: str, session_id: str,
    ) -> list[str]:
        """Retrieve facts from the warm tier.

        Args:
            query: Search query.
            session_id: Session scope.

        Returns:
            List of fact strings, or empty if no warm tier.
        """
        if self._warm is None:
            return []
        return await self._warm.get_facts(query, session_id)

    async def _store_warm(self, event: MemoryEvent, session_id: str) -> None:
        """Store content in the warm tier.

        Args:
            event: Memory event with content to store.
            session_id: Target session.
        """
        if self._warm is None:
            return
        await self._warm.store(event.content, session_id)

    # -- Cold tier helpers --------------------------------------------------

    async def _recall_cold(self, query: str, scope: str) -> list[str]:
        """Retrieve knowledge from the cold tier.

        Args:
            query: Semantic search query.
            scope: Memory scope for filtering.

        Returns:
            List of knowledge strings, or empty if no cold tier.
        """
        if self._cold is None:
            return []
        return await self._cold.search(query, scope)

    async def _store_cold(self, event: MemoryEvent, scope: str) -> None:
        """Store content in the cold tier.

        Args:
            event: Memory event with content to store.
            scope: Memory scope for filtering.
        """
        if self._cold is None:
            return
        await self._cold.store(event.content, scope)

    # -- Budget management --------------------------------------------------

    def _assemble_within_budget(
        self,
        conversation: list[dict[str, str]],
        episodes: list[str],
        facts: list[str],
        knowledge: list[str],
    ) -> MemoryContext:
        """Assemble a MemoryContext, truncating to fit the token budget.

        Priority order: conversation > facts > episodes > knowledge.
        Each category is trimmed from the end (oldest/least relevant)
        until the total fits within the budget.

        Args:
            conversation: Conversation messages.
            episodes: Episode strings.
            facts: Fact strings.
            knowledge: Knowledge strings.

        Returns:
            A MemoryContext that fits within the configured token budget.
        """
        budget_remaining = self._token_budget

        kept_conversation = self._fit_messages(conversation, budget_remaining)
        budget_remaining -= _estimate_messages_tokens(kept_conversation)

        kept_facts = self._fit_strings(facts, budget_remaining)
        budget_remaining -= _estimate_strings_tokens(kept_facts)

        kept_episodes = self._fit_strings(episodes, budget_remaining)
        budget_remaining -= _estimate_strings_tokens(kept_episodes)

        kept_knowledge = self._fit_strings(knowledge, budget_remaining)

        total_tokens = (
            _estimate_messages_tokens(kept_conversation)
            + _estimate_strings_tokens(kept_facts)
            + _estimate_strings_tokens(kept_episodes)
            + _estimate_strings_tokens(kept_knowledge)
        )

        return MemoryContext(
            conversation=kept_conversation,
            episodes=kept_episodes,
            facts=kept_facts,
            knowledge=kept_knowledge,
            token_count=total_tokens,
        )

    def _fit_messages(
        self,
        messages: list[dict[str, str]],
        budget: int,
    ) -> list[dict[str, str]]:
        """Keep as many recent messages as fit in the budget.

        Drops oldest messages first to preserve recency.

        Args:
            messages: Conversation messages, oldest first.
            budget: Available token budget.

        Returns:
            Suffix of messages that fits within the budget.
        """
        if budget <= 0:
            return []

        # Walk backwards from newest, accumulating until budget exhausted
        kept: list[dict[str, str]] = []
        used = 0
        for msg in reversed(messages):
            cost = _estimate_string_tokens(msg.get("content", ""))
            if used + cost > budget:
                break
            kept.append(msg)
            used += cost

        kept.reverse()
        return kept

    def _fit_strings(self, items: list[str], budget: int) -> list[str]:
        """Keep as many items as fit within the token budget.

        Items are kept in order; excess items at the end are dropped.

        Args:
            items: Strings to fit, in priority order.
            budget: Available token budget.

        Returns:
            Prefix of items that fits within the budget.
        """
        if budget <= 0:
            return []

        kept: list[str] = []
        used = 0
        for item in items:
            cost = _estimate_string_tokens(item)
            if used + cost > budget:
                break
            kept.append(item)
            used += cost

        return kept


# ---------------------------------------------------------------------------
# Token estimation helpers
# ---------------------------------------------------------------------------


def _estimate_string_tokens(text: str) -> int:
    """Estimate token count for a string using character-based heuristic.

    Args:
        text: Input text.

    Returns:
        Estimated token count (at least 1 for non-empty text).
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def _estimate_strings_tokens(items: list[str]) -> int:
    """Estimate total tokens for a list of strings.

    Args:
        items: List of text strings.

    Returns:
        Sum of estimated token counts.
    """
    return sum(_estimate_string_tokens(item) for item in items)


def _estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate total tokens for conversation messages.

    Args:
        messages: List of role/content dicts.

    Returns:
        Sum of estimated token counts for all message contents.
    """
    return sum(
        _estimate_string_tokens(msg.get("content", ""))
        for msg in messages
    )
