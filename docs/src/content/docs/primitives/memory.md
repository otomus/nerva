---
title: Memory
description: Tiered context storage with token budgeting and scope isolation.
---

The Memory primitive provides tiered storage that agents read from and write to. Context is automatically scoped by user, session, or agent.

## Protocol

```python
class Memory(Protocol):
    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        ...

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        ...

    async def consolidate(self, ctx: ExecContext) -> None:
        ...
```

### Value types

```python
class MemoryTier(StrEnum):
    HOT = "hot"    # session state
    WARM = "warm"  # episodes and facts
    COLD = "cold"  # long-term knowledge

@dataclass(frozen=True)
class MemoryEvent:
    content: str
    tier: MemoryTier = MemoryTier.HOT
    scope: Scope | None = None    # None = inherit from ctx
    tags: frozenset[str] = frozenset()
    source: str = ""

@dataclass
class MemoryContext:
    conversation: list[dict[str, str]]  # recent messages
    episodes: list[str]                  # warm tier
    facts: list[str]                     # warm tier
    knowledge: list[str]                 # cold tier
    token_count: int                     # estimated tokens consumed
```

## Tiers

### Hot -- session state

In-memory conversation history. Fast, ephemeral. Cleared when the session ends.

```python
from nerva.memory.hot import InMemoryHotMemory

hot = InMemoryHotMemory()
await hot.add_message("user", "What's the weather?", session_id="sess_1")
await hot.add_message("assistant", "22°C in Berlin", session_id="sess_1")

messages = await hot.get_conversation("sess_1")
# [{"role": "user", "content": "What's the weather?"}, {"role": "assistant", ...}]
```

### Warm -- episodes and facts

Persisted key-value store for extracted conversation episodes and factual knowledge. Survives session boundaries.

Implement the `WarmTier` protocol with any key-value backend:

```python
class WarmTier(Protocol):
    async def get_episodes(self, query: str, session_id: str) -> list[str]: ...
    async def get_facts(self, query: str, session_id: str) -> list[str]: ...
    async def store(self, content: str, session_id: str) -> None: ...
```

### Cold -- long-term knowledge

Vector database for semantic search over long-term knowledge. Use any embedding + vector store.

Implement the `ColdTier` protocol:

```python
class ColdTier(Protocol):
    async def search(self, query: str, scope: str) -> list[str]: ...
    async def store(self, content: str, scope: str) -> None: ...
```

## TieredMemory

Orchestrates all three tiers with automatic token budgeting:

```python
from nerva.memory.tiered import TieredMemory
from nerva.memory.hot import InMemoryHotMemory

memory = TieredMemory(
    hot=InMemoryHotMemory(),
    warm=my_warm_backend,      # optional
    cold=my_vector_store,      # optional
    token_budget=4000,         # max tokens in recalled context
)

# Store
await memory.store(
    MemoryEvent(content="User prefers metric units", tier=MemoryTier.WARM, source="preference_extractor"),
    ctx,
)

# Recall -- queries all tiers, assembles within budget
context = await memory.recall("weather preferences", ctx)
print(context.facts)        # ["User prefers metric units"]
print(context.token_count)  # 12
```

## Token budgeting

`TieredMemory.recall()` assembles context within a configurable token budget. Priority order:

1. **Conversation** (hot) -- most recent messages kept first
2. **Facts** (warm) -- highest relevance first
3. **Episodes** (warm) -- highest relevance first
4. **Knowledge** (cold) -- highest relevance first

Each category is trimmed from the tail (oldest/least relevant) until everything fits. Token estimation uses a 4-characters-per-token heuristic.

## Scope isolation

Memory operations are scoped by `ctx.memory_scope`:

| Scope | Visibility |
|-------|-----------|
| `user` | All sessions for this user |
| `session` | Current session only |
| `agent` | Shared across all users for this agent |
| `global` | Visible to everyone |

User A's memories never leak to user B. Each `MemoryEvent` can override the scope or inherit it from the context.

## Configuration

```yaml
# nerva.yaml
memory:
  hot:
    backend: inmemory       # or redis
    max_messages: 50
  warm:
    backend: sqlite         # or redis, postgres
  cold:
    backend: qdrant         # or pinecone, chromadb
    embedding_model: text-embedding-3-small
  token_budget: 4000
```
