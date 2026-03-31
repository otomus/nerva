"""Embedding-based router — cosine similarity against handler descriptions.

Routes messages by embedding the input text and comparing it against
pre-computed embeddings of handler descriptions.  Top-k handlers above
the confidence threshold are returned.  No LLM call needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nerva.router import HandlerCandidate, IntentResult

try:
    from nerva_core import cosine_similarity as _native_cosine
    _USE_NATIVE = True
except ImportError:
    _USE_NATIVE = False

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "EmbeddingFunc",
    "EmbeddingRouter",
    "HandlerDescriptor",
]

# ── Types ───────────────────────────────────────────────────────────

Embedding = list[float]
"""A dense vector representation of text."""


# ── Constants ───────────────────────────────────────────────────────

MINIMUM_THRESHOLD: float = 0.0
MAXIMUM_THRESHOLD: float = 1.0
DEFAULT_THRESHOLD: float = 0.3
DEFAULT_TOP_K: int = 5
NO_MATCH_CONFIDENCE: float = 0.0
NO_MATCH_INTENT: str = "unknown"
MATCH_INTENT: str = "semantic"


# ── Protocols ───────────────────────────────────────────────────────


class EmbeddingFunc(Protocol):
    """Async callable that converts text into an embedding vector.

    Implementations may call an external API (OpenAI, Cohere) or run
    a local model.  The returned vector length must be consistent
    across calls.
    """

    async def __call__(self, text: str) -> Embedding:
        """Embed a single text string.

        Args:
            text: The input text to embed.

        Returns:
            A dense float vector representing the text.
        """
        ...


# ── Value objects ───────────────────────────────────────────────────


@dataclass(frozen=True)
class HandlerDescriptor:
    """A handler registered with the embedding router.

    Attributes:
        name: Handler name matching a registry entry.
        description: Human-readable description that was embedded.
        embedding: Pre-computed embedding vector (set at register time).
    """

    name: str
    description: str
    embedding: Embedding


# ── Router ──────────────────────────────────────────────────────────


class EmbeddingRouter:
    """Router using cosine similarity between message and handler descriptions.

    Handlers are registered with descriptions.  At classify time the message
    is embedded and compared against all handler embeddings.  Top-k handlers
    above the confidence threshold are returned, best first.

    Args:
        embed: Async function that converts text to an embedding vector.
        threshold: Minimum cosine similarity to consider a match (0.0-1.0).
        top_k: Maximum number of candidates to return.

    Raises:
        ValueError: If *threshold* is outside [0.0, 1.0] or *top_k* < 1.
    """

    def __init__(
        self,
        embed: EmbeddingFunc,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        if not (MINIMUM_THRESHOLD <= threshold <= MAXIMUM_THRESHOLD):
            raise ValueError(
                f"threshold must be between {MINIMUM_THRESHOLD} and "
                f"{MAXIMUM_THRESHOLD}, got {threshold}"
            )
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")

        self._embed = embed
        self._threshold = threshold
        self._top_k = top_k
        self._handlers: list[HandlerDescriptor] = []

    async def register(self, name: str, description: str) -> None:
        """Register a handler by embedding its description.

        The description is embedded immediately via the injected embed
        function and stored for later comparison.

        Args:
            name: Handler name (must match a registry entry).
            description: Human-readable description of what the handler does.

        Raises:
            ValueError: If *name* is empty or *description* is blank.
        """
        if not name:
            raise ValueError("Handler name must not be empty")
        if not description.strip():
            raise ValueError("Handler description must not be blank")

        embedding = await self._embed(description)
        descriptor = HandlerDescriptor(
            name=name,
            description=description,
            embedding=embedding,
        )
        self._handlers.append(descriptor)

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify a message by cosine similarity against handler descriptions.

        Embeds the message, computes cosine similarity against every registered
        handler, and returns the top-k handlers above the threshold.

        Args:
            message: Raw user message text.
            ctx: Execution context (carried for protocol conformance and
                observability; not used for routing decisions).

        Returns:
            :class:`~nerva.router.IntentResult` with ranked candidates.
            Returns an empty result if no handlers are registered or
            none exceed the threshold.
        """
        if not message.strip() or not self._handlers:
            return _empty_result()

        query_embedding = await self._embed(message)
        candidates = _rank_handlers(
            query_embedding, self._handlers, self._threshold, self._top_k
        )

        if not candidates:
            return _empty_result()

        return _build_result(candidates)


# ── Pure helpers ────────────────────────────────────────────────────


def _cosine_similarity(a: Embedding, b: Embedding) -> float:
    """Compute cosine similarity between two vectors.

    Delegates to the native Rust implementation when available for
    better performance on large vectors.  Falls back to pure Python
    otherwise.

    Returns 0.0 for zero-length vectors or mismatched dimensions
    rather than raising, so callers never hit division-by-zero.

    Args:
        a: First embedding vector.
        b: Second embedding vector.

    Returns:
        Cosine similarity in the range [-1.0, 1.0], or 0.0 for
        degenerate inputs.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    if _USE_NATIVE:
        return _native_cosine(a, b)

    return _cosine_similarity_pure(a, b)


def _cosine_similarity_pure(a: Embedding, b: Embedding) -> float:
    """Pure-Python cosine similarity (fallback when native is unavailable).

    Args:
        a: First embedding vector.
        b: Second embedding vector.

    Returns:
        Cosine similarity in the range [-1.0, 1.0], or 0.0 for
        zero-magnitude vectors.
    """
    dot_product = sum(ai * bi for ai, bi in zip(a, b))
    norm_a = math.sqrt(sum(ai * ai for ai in a))
    norm_b = math.sqrt(sum(bi * bi for bi in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def _rank_handlers(
    query_embedding: Embedding,
    handlers: list[HandlerDescriptor],
    threshold: float,
    top_k: int,
) -> list[HandlerCandidate]:
    """Score all handlers and return the top-k above the threshold.

    Args:
        query_embedding: The embedded user message.
        handlers: All registered handler descriptors.
        threshold: Minimum similarity to include.
        top_k: Maximum number of candidates to return.

    Returns:
        Up to *top_k* :class:`~nerva.router.HandlerCandidate` objects,
        sorted by score descending.
    """
    scored: list[tuple[float, HandlerDescriptor]] = []

    for handler in handlers:
        similarity = _cosine_similarity(query_embedding, handler.embedding)
        # Clamp to [0.0, 1.0] — negative cosine similarity means
        # the vectors point in opposite directions, treat as no match.
        clamped = max(0.0, min(1.0, similarity))
        if clamped >= threshold:
            scored.append((clamped, handler))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        HandlerCandidate(
            name=descriptor.name,
            score=score,
            reason=f"Cosine similarity {score:.4f} with '{descriptor.description}'",
        )
        for score, descriptor in scored[:top_k]
    ]


def _build_result(candidates: list[HandlerCandidate]) -> IntentResult:
    """Build an IntentResult from ranked candidates.

    Confidence is set to the score of the best candidate.

    Args:
        candidates: Non-empty list of ranked handler candidates.

    Returns:
        IntentResult with semantic intent and the given candidates.
    """
    raw_scores = {c.name: c.score for c in candidates}
    return IntentResult(
        intent=MATCH_INTENT,
        confidence=candidates[0].score,
        handlers=candidates,
        raw_scores=raw_scores,
    )


def _empty_result() -> IntentResult:
    """Build an empty IntentResult when nothing matched.

    Returns:
        IntentResult with zero confidence and no handlers.
    """
    return IntentResult(
        intent=NO_MATCH_INTENT,
        confidence=NO_MATCH_CONFIDENCE,
        handlers=[],
    )
