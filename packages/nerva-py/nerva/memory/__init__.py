"""Memory — tiered context storage with scope isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext, Scope


class MemoryTier(StrEnum):
    """Storage tier for memory events.

    Each tier trades off speed for capacity:
    - HOT: current session state, in-memory, fast but ephemeral.
    - WARM: recent episodes and facts, persisted in a key-value store.
    - COLD: long-term knowledge, stored in a vector database for semantic search.
    """

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass(frozen=True)
class MemoryEvent:
    """An event to be stored in memory.

    Immutable once created. The ``scope`` field controls visibility:
    ``None`` means "inherit from the execution context".

    Attributes:
        content: The content to store.
        tier: Target storage tier.
        scope: Access scope for this memory. ``None`` inherits from ctx.
        tags: Metadata tags for filtering and retrieval.
        source: Origin of this memory (agent name, tool, user).
    """

    content: str
    tier: MemoryTier = MemoryTier.HOT
    scope: Scope | None = None
    tags: frozenset[str] = field(default_factory=frozenset)
    source: str = ""


@dataclass
class MemoryContext:
    """Retrieved memory context for an agent.

    Assembled by the memory system from one or more tiers. Consumers
    use the fields directly to build LLM prompts or agent state.

    Attributes:
        conversation: Recent conversation messages (role/content dicts).
        episodes: Relevant past episodes from the warm tier.
        facts: Extracted facts from the warm tier.
        knowledge: Long-term knowledge entries from the cold tier.
        token_count: Estimated tokens consumed by this context.
    """

    conversation: list[dict[str, str]] = field(default_factory=list)
    episodes: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    token_count: int = 0


@runtime_checkable
class Memory(Protocol):
    """Tiered context storage that agents read from and write to.

    Implementations must provide three operations: recall (read),
    store (write), and consolidate (maintenance). All operations
    are scoped by the ``ExecContext``.
    """

    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        """Retrieve relevant context, scoped by ``ctx.memory_scope``.

        Args:
            query: Search query for relevant memories.
            ctx: Execution context with memory scope and session identity.

        Returns:
            MemoryContext with relevant conversation, episodes, facts, knowledge.
        """
        ...

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        """Store an event in the appropriate tier and scope.

        Args:
            event: Memory event to store.
            ctx: Execution context providing scope and identity.
        """
        ...

    async def consolidate(self, ctx: ExecContext) -> None:
        """Promote, merge, or expire memories across tiers.

        Called periodically to move hot memories to warm, warm to cold,
        and expire stale entries. The exact policy is implementation-defined.

        Args:
            ctx: Execution context.
        """
        ...
