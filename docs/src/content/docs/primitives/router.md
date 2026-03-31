---
title: Router
description: Classify intent and select the right handler.
---

The Router takes a raw user message and returns a structured `IntentResult` with a confidence score and ranked handler candidates.

## Protocol

```python
class IntentRouter(Protocol):
    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        ...
```

### IntentResult

```python
@dataclass(frozen=True)
class IntentResult:
    intent: str                       # classified intent label
    confidence: float                 # 0.0 - 1.0
    handlers: list[HandlerCandidate]  # ranked, best first
    raw_scores: dict[str, float]      # per-handler scores (debugging)

@dataclass(frozen=True)
class HandlerCandidate:
    name: str       # must match a registry entry
    score: float    # 0.0 - 1.0
    reason: str     # why this handler was selected
```

## Strategies

### RuleRouter

Deterministic regex matching. First match wins. Zero LLM cost.

```python
from nerva.router.rule import RuleRouter, Rule

router = RuleRouter(
    rules=[
        Rule(pattern=r"weather", handler="weather_agent", intent="weather"),
        Rule(pattern=r"calendar|schedule", handler="calendar_agent", intent="calendar"),
    ],
    default_handler="general_agent",
)

result = await router.classify("What's the weather?", ctx)
# intent="weather", confidence=1.0, handler="weather_agent"
```

**When to use:** < 10 agents, keywords are unambiguous, you want deterministic routing.

### EmbeddingRouter

Cosine similarity against handler descriptions. No LLM call needed -- just an embedding model.

```python
from nerva.router.embedding import EmbeddingRouter

router = EmbeddingRouter(embed=my_embed_func, threshold=0.3, top_k=5)

await router.register("weather_agent", "Answer weather and forecast questions")
await router.register("calendar_agent", "Manage calendar events and scheduling")

result = await router.classify("Will it rain tomorrow?", ctx)
# intent="semantic", confidence=0.87, handler="weather_agent"
```

**When to use:** 10+ agents, natural language overlap between domains, you want fast sub-50ms routing without LLM costs.

### LLMRouter

Sends the handler catalog to an LLM and asks for structured output.

```python
from nerva.router.llm import LLMRouter

router = LLMRouter(llm=my_llm_client, registry=my_registry)

result = await router.classify("Book me a flight to Berlin next Tuesday", ctx)
# intent="travel", confidence=0.95, handler="flight_agent"
```

**When to use:** Complex queries that need reasoning, multi-intent messages, high accuracy requirements.

### HybridRouter

Embedding pre-filter then LLM re-rank. Best of both worlds.

```python
from nerva.router.hybrid import HybridRouter

router = HybridRouter(
    embedding_router=embedding_router,
    llm_router=llm_router,
    embedding_top_k=5,     # pre-filter to top 5
    confidence_threshold=0.8,  # skip LLM if embedding confidence is high enough
)
```

**When to use:** Large handler catalogs (50+) where LLM routing over the full catalog is too slow or expensive.

## Choosing a strategy

| Criteria | Rule | Embedding | LLM | Hybrid |
|----------|------|-----------|-----|--------|
| Latency | ~0ms | ~10ms | ~500ms | ~50ms (cache hit) / ~550ms |
| Cost | Free | Embedding model | LLM tokens | Both |
| Accuracy | Exact match only | Good | Best | Best |
| Setup effort | Manual rules | Handler descriptions | Handler descriptions | Both routers |
| Scales to | ~20 handlers | ~200 handlers | ~50 handlers | ~500 handlers |

Start with `RuleRouter`. Switch to `EmbeddingRouter` when keyword overlap causes misroutes. Add `HybridRouter` when you need both speed and accuracy at scale.
