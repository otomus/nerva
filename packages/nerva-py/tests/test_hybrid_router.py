"""Tests for HybridRouter and EmbeddingRouter — N-171.

Covers two-stage hybrid routing (embedding pre-filter + LLM re-rank)
and single-stage embedding routing. Mocks the embedding function and
LLM re-ranker to exercise confidence thresholds, fallbacks, edge cases,
and malformed inputs.
"""

from __future__ import annotations

import math

import pytest

from nerva.router import HandlerCandidate, IntentResult
from nerva.router.embedding import (
    DEFAULT_THRESHOLD,
    DEFAULT_TOP_K,
    EmbeddingRouter,
    _cosine_similarity as emb_cosine_similarity,
)
from nerva.router.hybrid import (
    HYBRID_INTENT,
    NO_MATCH_CONFIDENCE,
    NO_MATCH_INTENT,
    HybridRouter,
    _cosine_similarity as hyb_cosine_similarity,
)

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(dim: int, index: int) -> list[float]:
    """Build a unit vector with a 1.0 at *index*, zeros elsewhere.

    Args:
        dim: Dimensionality.
        index: Position of the non-zero element.

    Returns:
        A list of floats representing the unit vector.
    """
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def _constant_embed(value: list[float]):
    """Return an async embed function that always returns *value*.

    Args:
        value: The embedding vector to return for every input.

    Returns:
        Async callable matching the EmbedFunc protocol.
    """
    async def _embed(text: str) -> list[float]:
        """Return a constant embedding regardless of input."""
        return list(value)
    return _embed


def _mapping_embed(mapping: dict[str, list[float]], default: list[float] | None = None):
    """Return an async embed function that looks up text in a mapping.

    Args:
        mapping: Text-to-embedding lookup table.
        default: Fallback vector for unknown text.

    Returns:
        Async callable matching the EmbedFunc protocol.
    """
    async def _embed(text: str) -> list[float]:
        """Return embedding from mapping, falling back to default."""
        if text in mapping:
            return mapping[text]
        if default is not None:
            return list(default)
        raise KeyError(f"No embedding for: {text!r}")
    return _embed


def _passthrough_reranker():
    """Return a reranker that returns candidates unmodified.

    Returns:
        Async callable matching the RerankFunc protocol.
    """
    async def _rerank(message: str, candidates: list[HandlerCandidate]) -> list[HandlerCandidate]:
        """Return candidates as-is."""
        return candidates
    return _rerank


def _empty_reranker():
    """Return a reranker that always returns an empty list.

    Returns:
        Async callable matching the RerankFunc protocol.
    """
    async def _rerank(message: str, candidates: list[HandlerCandidate]) -> list[HandlerCandidate]:
        """Return no candidates, simulating a reranker failure."""
        return []
    return _rerank


def _boosting_reranker(boost: float = 0.1):
    """Return a reranker that boosts all candidate scores by a fixed amount.

    Args:
        boost: Amount to add to each score (clamped to 1.0).

    Returns:
        Async callable matching the RerankFunc protocol.
    """
    async def _rerank(message: str, candidates: list[HandlerCandidate]) -> list[HandlerCandidate]:
        """Boost every candidate's score."""
        return [
            HandlerCandidate(
                name=c.name,
                score=min(1.0, c.score + boost),
                reason=f"re-ranked: {c.reason}",
            )
            for c in candidates
        ]
    return _rerank


# ---------------------------------------------------------------------------
# Cosine similarity (pure function, shared by both routers)
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    """Tests for the pure _cosine_similarity helper."""

    def test_identical_vectors(self) -> None:
        """Identical non-zero vectors have similarity 1.0."""
        assert hyb_cosine_similarity([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        """Orthogonal unit vectors have similarity 0.0."""
        assert hyb_cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        """Opposite vectors have similarity -1.0."""
        assert hyb_cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_empty_vectors(self) -> None:
        """Empty vectors return 0.0 instead of raising."""
        assert hyb_cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self) -> None:
        """Mismatched vector lengths return 0.0."""
        assert hyb_cosine_similarity([1, 2], [1, 2, 3]) == 0.0

    def test_zero_magnitude_vector(self) -> None:
        """A zero-magnitude vector returns 0.0."""
        assert hyb_cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0

    def test_both_zero_vectors(self) -> None:
        """Two zero vectors return 0.0."""
        assert hyb_cosine_similarity([0, 0], [0, 0]) == 0.0

    def test_embedding_router_cosine_agrees(self) -> None:
        """The EmbeddingRouter's _cosine_similarity matches the HybridRouter's."""
        a, b = [0.5, 0.3, 0.8], [0.1, 0.9, 0.4]
        assert emb_cosine_similarity(a, b) == pytest.approx(hyb_cosine_similarity(a, b))


# ---------------------------------------------------------------------------
# HybridRouter
# ---------------------------------------------------------------------------

class TestHybridRouter:
    """Tests for the two-stage HybridRouter."""

    @pytest.fixture
    def ctx(self):
        """Provide a default ExecContext for routing."""
        return make_ctx()

    # -- Registration -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_register_empty_name_raises(self) -> None:
        """Registering a handler with an empty name must raise ValueError."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        with pytest.raises(ValueError, match="name"):
            await router.register("", "some description")

    @pytest.mark.asyncio
    async def test_register_blank_description_raises(self) -> None:
        """Registering with a whitespace-only description must raise ValueError."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        with pytest.raises(ValueError, match="description"):
            await router.register("handler", "   ")

    @pytest.mark.asyncio
    async def test_register_duplicate_name_raises(self) -> None:
        """Registering the same handler name twice must raise ValueError."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        await router.register("handler_a", "does things")
        with pytest.raises(ValueError, match="already registered"):
            await router.register("handler_a", "does other things")

    # -- Empty / blank inputs -----------------------------------------------

    @pytest.mark.asyncio
    async def test_classify_empty_message(self, ctx) -> None:
        """Empty string message returns unknown intent with zero confidence."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        await router.register("h1", "handler one")
        result = await router.classify("", ctx)
        assert result.intent == NO_MATCH_INTENT
        assert result.confidence == NO_MATCH_CONFIDENCE
        assert result.handlers == []

    @pytest.mark.asyncio
    async def test_classify_whitespace_message(self, ctx) -> None:
        """Whitespace-only message returns unknown intent."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        await router.register("h1", "handler one")
        result = await router.classify("   \t\n  ", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_classify_no_handlers(self, ctx) -> None:
        """Classifying with no registered handlers returns unknown intent."""
        router = HybridRouter(_constant_embed([1.0]), _passthrough_reranker())
        result = await router.classify("hello", ctx)
        assert result.intent == NO_MATCH_INTENT
        assert result.handlers == []

    # -- Normal routing -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_classify_returns_hybrid_intent(self, ctx) -> None:
        """A matching handler produces a hybrid intent result."""
        embed = _constant_embed([1.0, 0.0])
        router = HybridRouter(embed, _passthrough_reranker(), embedding_threshold=0.0)
        await router.register("search", "find documents")
        result = await router.classify("search for cats", ctx)
        assert result.intent == HYBRID_INTENT
        assert len(result.handlers) == 1
        assert result.handlers[0].name == "search"

    @pytest.mark.asyncio
    async def test_classify_respects_embedding_threshold(self, ctx) -> None:
        """Handlers below the embedding threshold are filtered out."""
        embeddings = {
            "find documents": [1.0, 0.0],
            "search for cats": [0.0, 1.0],  # orthogonal -> similarity 0.0
        }
        router = HybridRouter(
            _mapping_embed(embeddings),
            _passthrough_reranker(),
            embedding_threshold=0.5,
        )
        await router.register("search", "find documents")
        result = await router.classify("search for cats", ctx)
        assert result.intent == NO_MATCH_INTENT

    @pytest.mark.asyncio
    async def test_reranker_fallback_on_empty_rerank(self, ctx) -> None:
        """When the reranker returns nothing, fall back to embedding results."""
        embed = _constant_embed([1.0, 0.0])
        router = HybridRouter(embed, _empty_reranker(), embedding_threshold=0.0)
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        # Should still return results from embedding stage
        assert result.intent == HYBRID_INTENT
        assert len(result.handlers) >= 1

    @pytest.mark.asyncio
    async def test_reranker_boosts_scores(self, ctx) -> None:
        """The reranker can modify candidate scores."""
        embed = _constant_embed([1.0, 0.0])
        router = HybridRouter(
            embed, _boosting_reranker(0.05), embedding_threshold=0.0
        )
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert result.handlers[0].score > 0.0

    # -- final_k limit ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_final_k_limits_output(self, ctx) -> None:
        """The final_k parameter limits the number of returned candidates."""
        embed = _constant_embed([1.0, 0.0])
        router = HybridRouter(
            embed, _passthrough_reranker(), embedding_threshold=0.0, final_k=2
        )
        for i in range(5):
            await router.register(f"h{i}", f"handler {i}")
        result = await router.classify("hello", ctx)
        assert len(result.handlers) <= 2

    # -- All handlers identical scores --------------------------------------

    @pytest.mark.asyncio
    async def test_identical_scores_all_returned(self, ctx) -> None:
        """When all handlers have the same embedding, all pass the threshold."""
        embed = _constant_embed([0.5, 0.5])
        router = HybridRouter(
            embed, _passthrough_reranker(), embedding_threshold=0.0, final_k=10
        )
        await router.register("a", "desc a")
        await router.register("b", "desc b")
        await router.register("c", "desc c")
        result = await router.classify("query", ctx)
        names = {c.name for c in result.handlers}
        assert names == {"a", "b", "c"}

    # -- Pre-filter k -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pre_filter_k_limits_reranker_input(self, ctx) -> None:
        """pre_filter_k limits candidates forwarded to the reranker."""
        received_counts: list[int] = []

        async def _counting_reranker(
            message: str, candidates: list[HandlerCandidate]
        ) -> list[HandlerCandidate]:
            """Track how many candidates the reranker receives."""
            received_counts.append(len(candidates))
            return candidates

        embed = _constant_embed([1.0, 0.0])
        router = HybridRouter(
            embed, _counting_reranker, embedding_threshold=0.0, pre_filter_k=2
        )
        for i in range(5):
            await router.register(f"h{i}", f"handler {i}")
        await router.classify("query", ctx)
        assert received_counts[0] <= 2


# ---------------------------------------------------------------------------
# EmbeddingRouter
# ---------------------------------------------------------------------------

class TestEmbeddingRouter:
    """Tests for the single-stage EmbeddingRouter."""

    @pytest.fixture
    def ctx(self):
        """Provide a default ExecContext for routing."""
        return make_ctx()

    # -- Constructor validation ---------------------------------------------

    def test_threshold_below_zero_raises(self) -> None:
        """Negative threshold raises ValueError."""
        with pytest.raises(ValueError, match="threshold"):
            EmbeddingRouter(_constant_embed([1.0]), threshold=-0.1)

    def test_threshold_above_one_raises(self) -> None:
        """Threshold > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="threshold"):
            EmbeddingRouter(_constant_embed([1.0]), threshold=1.5)

    def test_top_k_zero_raises(self) -> None:
        """top_k < 1 raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            EmbeddingRouter(_constant_embed([1.0]), top_k=0)

    def test_top_k_negative_raises(self) -> None:
        """Negative top_k raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            EmbeddingRouter(_constant_embed([1.0]), top_k=-5)

    # -- Registration -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_register_empty_name_raises(self) -> None:
        """Empty handler name raises ValueError."""
        router = EmbeddingRouter(_constant_embed([1.0]))
        with pytest.raises(ValueError, match="name"):
            await router.register("", "description")

    @pytest.mark.asyncio
    async def test_register_blank_description_raises(self) -> None:
        """Blank description raises ValueError."""
        router = EmbeddingRouter(_constant_embed([1.0]))
        with pytest.raises(ValueError, match="blank"):
            await router.register("h1", "   ")

    # -- Classify -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_classify_no_handlers(self, ctx) -> None:
        """No handlers returns unknown intent."""
        router = EmbeddingRouter(_constant_embed([1.0]))
        result = await router.classify("hello", ctx)
        assert result.intent == "unknown"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_classify_empty_message(self, ctx) -> None:
        """Empty message returns unknown intent."""
        router = EmbeddingRouter(_constant_embed([1.0]))
        await router.register("h1", "handler one")
        result = await router.classify("", ctx)
        assert result.intent == "unknown"

    @pytest.mark.asyncio
    async def test_classify_whitespace_message(self, ctx) -> None:
        """Whitespace-only message returns unknown intent."""
        router = EmbeddingRouter(_constant_embed([1.0]))
        await router.register("h1", "handler one")
        result = await router.classify("   ", ctx)
        assert result.intent == "unknown"

    @pytest.mark.asyncio
    async def test_classify_returns_semantic_intent(self, ctx) -> None:
        """Matching handler returns semantic intent."""
        embed = _constant_embed([1.0, 0.0])
        router = EmbeddingRouter(embed, threshold=0.0)
        await router.register("search", "find things")
        result = await router.classify("hello", ctx)
        assert result.intent == "semantic"
        assert len(result.handlers) >= 1

    @pytest.mark.asyncio
    async def test_classify_top_k_limits(self, ctx) -> None:
        """top_k limits the number of returned candidates."""
        embed = _constant_embed([1.0, 0.0])
        router = EmbeddingRouter(embed, threshold=0.0, top_k=2)
        for i in range(5):
            await router.register(f"h{i}", f"handler {i}")
        result = await router.classify("hello", ctx)
        assert len(result.handlers) <= 2

    @pytest.mark.asyncio
    async def test_classify_threshold_filters(self, ctx) -> None:
        """Handlers below threshold are excluded."""
        embeddings = {
            "find documents": [1.0, 0.0],
            "query": [0.0, 1.0],
        }
        router = EmbeddingRouter(
            _mapping_embed(embeddings), threshold=0.5
        )
        await router.register("search", "find documents")
        result = await router.classify("query", ctx)
        assert result.intent == "unknown"

    @pytest.mark.asyncio
    async def test_classify_confidence_is_best_score(self, ctx) -> None:
        """IntentResult.confidence equals the best handler's score."""
        embed = _constant_embed([1.0, 0.0])
        router = EmbeddingRouter(embed, threshold=0.0)
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        if result.handlers:
            assert result.confidence == result.handlers[0].score

    @pytest.mark.asyncio
    async def test_classify_all_identical_scores(self, ctx) -> None:
        """All handlers with the same embedding get the same score."""
        embed = _constant_embed([0.7, 0.3])
        router = EmbeddingRouter(embed, threshold=0.0)
        await router.register("a", "alpha")
        await router.register("b", "beta")
        result = await router.classify("query", ctx)
        scores = [c.score for c in result.handlers]
        assert len(set(scores)) == 1  # all identical

    @pytest.mark.asyncio
    async def test_raw_scores_populated(self, ctx) -> None:
        """IntentResult.raw_scores contains per-handler scores."""
        embed = _constant_embed([1.0, 0.0])
        router = EmbeddingRouter(embed, threshold=0.0)
        await router.register("h1", "handler one")
        result = await router.classify("hello", ctx)
        assert "h1" in result.raw_scores

    @pytest.mark.asyncio
    async def test_threshold_boundary_exact_match(self, ctx) -> None:
        """A handler with similarity exactly at threshold is included (>= check)."""
        # Same vector -> similarity 1.0 -> passes any threshold <= 1.0
        embed = _constant_embed([1.0, 0.0])
        router = EmbeddingRouter(embed, threshold=1.0)
        await router.register("h1", "handler")
        result = await router.classify("hello", ctx)
        assert result.intent == "semantic"
