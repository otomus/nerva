"""Hybrid router -- embedding pre-filter followed by LLM re-ranking (N-115).

Two-stage routing: first narrow the candidate set via cosine similarity
on embeddings, then re-rank the survivors with an LLM call for
semantic precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
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
    "EmbedFunc",
    "RerankFunc",
    "HybridRouter",
]

# ── Types ───────────────────────────────────────────────────────────

Embedding = list[float]
"""Dense vector representation of a text string."""


class EmbedFunc(Protocol):
    """Async function that converts text to an embedding vector.

    Args:
        text: Input text to embed.

    Returns:
        Dense float vector representing the text.
    """

    async def __call__(self, text: str) -> Embedding: ...


class RerankFunc(Protocol):
    """Async function that re-ranks candidates using an LLM.

    Takes the original message and pre-filtered candidates,
    returns re-ranked candidates with updated scores.

    Args:
        message: Original user message.
        candidates: Pre-filtered handler candidates from embedding stage.

    Returns:
        Re-ranked candidates with updated scores, best first.
    """

    async def __call__(
        self, message: str, candidates: list[HandlerCandidate]
    ) -> list[HandlerCandidate]: ...


# ── Constants ───────────────────────────────────────────────────────

NO_MATCH_INTENT: str = "unknown"
NO_MATCH_CONFIDENCE: float = 0.0
HYBRID_INTENT: str = "hybrid"


# ── Internal value object ───────────────────────────────────────────


@dataclass(frozen=True)
class _RegisteredHandler:
    """A handler with its pre-computed description embedding.

    Attributes:
        name: Handler identifier.
        description: Human-readable description used for embedding.
        embedding: Pre-computed embedding vector for *description*.
    """

    name: str
    description: str
    embedding: Embedding


# ── Router ──────────────────────────────────────────────────────────


class HybridRouter:
    """Two-stage router: embedding pre-filter followed by LLM re-rank.

    Stage 1: Embed the incoming message, compute cosine similarity against
    all registered handlers, and keep the top *pre_filter_k* candidates
    that exceed *embedding_threshold*.

    Stage 2: Pass surviving candidates to the *rerank* function (typically
    an LLM call) which returns re-scored candidates.

    If no handlers are registered, or all fall below the embedding
    threshold after stage 1, stage 2 is skipped and an empty result is
    returned.  If the reranker returns an empty list, the router falls
    back to embedding-only results.

    Args:
        embed: Async embedding function.
        rerank: Async re-ranking function (LLM-based).
        embedding_threshold: Minimum cosine similarity to survive pre-filter.
        pre_filter_k: Maximum candidates forwarded to the reranker.
        final_k: Maximum candidates in the final result.
    """

    DEFAULT_EMBEDDING_THRESHOLD: float = 0.2
    DEFAULT_PRE_FILTER_K: int = 10
    DEFAULT_FINAL_K: int = 5

    def __init__(
        self,
        embed: EmbedFunc,
        rerank: RerankFunc,
        *,
        embedding_threshold: float = DEFAULT_EMBEDDING_THRESHOLD,
        pre_filter_k: int = DEFAULT_PRE_FILTER_K,
        final_k: int = DEFAULT_FINAL_K,
    ) -> None:
        self._embed = embed
        self._rerank = rerank
        self._embedding_threshold = embedding_threshold
        self._pre_filter_k = pre_filter_k
        self._final_k = final_k
        self._handlers: list[_RegisteredHandler] = []

    # ── Public API ──────────────────────────────────────────────────

    async def register(self, name: str, description: str) -> None:
        """Register a handler by embedding its description.

        Args:
            name: Unique handler identifier.
            description: Human-readable description to embed for matching.

        Raises:
            ValueError: If *name* is empty or already registered.
            ValueError: If *description* is empty or whitespace-only.
        """
        if not name:
            raise ValueError("Handler name must not be empty")
        if not description or not description.strip():
            raise ValueError("Handler description must not be empty")
        if _find_handler(self._handlers, name) is not None:
            raise ValueError(f"Handler '{name}' is already registered")

        embedding = await self._embed(description)
        handler = _RegisteredHandler(
            name=name, description=description, embedding=embedding
        )
        self._handlers.append(handler)

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Two-stage classification: embedding pre-filter followed by LLM re-rank.

        Args:
            message: Raw user message text.
            ctx: Execution context (forwarded for observability; not used
                directly by the router).

        Returns:
            :class:`~nerva.router.IntentResult` with ranked handler candidates.
            Returns an empty result if no handlers match or the message is blank.
        """
        if not message or not message.strip():
            return _empty_result()
        if not self._handlers:
            return _empty_result()

        # Stage 1: embedding pre-filter
        embedding_candidates = await self._embedding_prefilter(message)
        if not embedding_candidates:
            return _empty_result()

        # Stage 2: LLM re-rank
        reranked = await self._llm_rerank(message, embedding_candidates)

        # Fall back to embedding results if reranker returns nothing
        final_candidates = reranked if reranked else embedding_candidates
        trimmed = final_candidates[: self._final_k]

        return _build_result(trimmed)

    # ── Private stages ──────────────────────────────────────────────

    async def _embedding_prefilter(
        self, message: str
    ) -> list[HandlerCandidate]:
        """Stage 1: score all handlers by cosine similarity, keep top-k above threshold.

        Args:
            message: Raw user message text.

        Returns:
            Sorted candidates (best first) that passed the embedding threshold.
        """
        message_embedding = await self._embed(message)
        scored = _score_handlers(
            message_embedding, self._handlers, self._embedding_threshold
        )
        sorted_candidates = sorted(scored, key=lambda c: c.score, reverse=True)
        return sorted_candidates[: self._pre_filter_k]

    async def _llm_rerank(
        self, message: str, candidates: list[HandlerCandidate]
    ) -> list[HandlerCandidate]:
        """Stage 2: re-rank pre-filtered candidates via the LLM reranker.

        Args:
            message: Raw user message text.
            candidates: Pre-filtered candidates from the embedding stage.

        Returns:
            Re-ranked candidates sorted by score (best first), or empty
            list if the reranker produces no results.
        """
        reranked = await self._rerank(message, candidates)
        return sorted(reranked, key=lambda c: c.score, reverse=True)


# ── Pure helpers ────────────────────────────────────────────────────


def _cosine_similarity(vec_a: Embedding, vec_b: Embedding) -> float:
    """Compute cosine similarity between two vectors.

    Delegates to the native Rust implementation when available for
    better performance on large vectors.  Falls back to pure Python
    otherwise.

    Returns 0.0 for zero-length or mismatched vectors rather than
    raising, so callers do not need extra guard clauses.

    Args:
        vec_a: First embedding vector.
        vec_b: Second embedding vector.

    Returns:
        Cosine similarity in the range [-1.0, 1.0], or 0.0 if either
        vector has zero magnitude or the lengths differ.
    """
    if len(vec_a) != len(vec_b) or len(vec_a) == 0:
        return 0.0

    if _USE_NATIVE:
        return _native_cosine(vec_a, vec_b)

    return _cosine_similarity_pure(vec_a, vec_b)


def _cosine_similarity_pure(vec_a: Embedding, vec_b: Embedding) -> float:
    """Pure-Python cosine similarity (fallback when native is unavailable).

    Args:
        vec_a: First embedding vector.
        vec_b: Second embedding vector.

    Returns:
        Cosine similarity in the range [-1.0, 1.0], or 0.0 for
        zero-magnitude vectors.
    """
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (mag_a * mag_b)


def _score_handlers(
    message_embedding: Embedding,
    handlers: list[_RegisteredHandler],
    threshold: float,
) -> list[HandlerCandidate]:
    """Score each handler against the message embedding and filter by threshold.

    Args:
        message_embedding: Embedding vector of the user message.
        handlers: All registered handlers with their embeddings.
        threshold: Minimum cosine similarity to include a candidate.

    Returns:
        Candidates whose similarity exceeds *threshold* (unordered).
    """
    candidates: list[HandlerCandidate] = []
    for handler in handlers:
        similarity = _cosine_similarity(message_embedding, handler.embedding)
        if similarity < threshold:
            continue
        # Clamp similarity into valid [0.0, 1.0] score range
        score = max(0.0, min(1.0, similarity))
        candidates.append(
            HandlerCandidate(
                name=handler.name,
                score=score,
                reason=f"embedding similarity: {similarity:.4f}",
            )
        )
    return candidates


def _find_handler(
    handlers: list[_RegisteredHandler], name: str
) -> _RegisteredHandler | None:
    """Look up a registered handler by name.

    Args:
        handlers: List of registered handlers.
        name: Handler name to search for.

    Returns:
        The matching handler, or ``None`` if not found.
    """
    for handler in handlers:
        if handler.name == name:
            return handler
    return None


def _build_result(candidates: list[HandlerCandidate]) -> IntentResult:
    """Build an IntentResult from a non-empty candidate list.

    Confidence is derived from the top candidate's score.

    Args:
        candidates: Non-empty, sorted (best first) list of candidates.

    Returns:
        IntentResult with hybrid intent and top-candidate confidence.
    """
    confidence = candidates[0].score if candidates else NO_MATCH_CONFIDENCE
    return IntentResult(
        intent=HYBRID_INTENT,
        confidence=confidence,
        handlers=list(candidates),
        raw_scores={c.name: c.score for c in candidates},
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
