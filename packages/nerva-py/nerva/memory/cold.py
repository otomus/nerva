"""Cold memory — long-term knowledge with search capability.

In-memory implementation of the ``ColdTier`` protocol from ``tiered.py``.
Uses simple word-overlap scoring instead of vector embeddings. Suitable
for testing; swap with a vector DB implementation for production.
"""

from __future__ import annotations

from collections import defaultdict

DEFAULT_MAX_RESULTS = 10
"""Maximum search results returned per query."""

MIN_RELEVANCE_SCORE = 0.1
"""Minimum word-overlap score for an entry to appear in search results."""


class InMemoryColdMemory:
    """In-memory cold tier with basic keyword search.

    No actual vector embeddings — uses simple word-overlap scoring
    (|query intersection entry| / |query words|) to rank stored entries.
    For production use, swap with a vector DB implementation.

    Args:
        max_results: Maximum number of search results to return.
    """

    def __init__(self, max_results: int = DEFAULT_MAX_RESULTS) -> None:
        self._max_results = max_results
        self._entries: dict[str, list[str]] = defaultdict(list)

    async def search(self, query: str, scope: str) -> list[str]:
        """Search stored entries by word overlap with the query.

        Scores each entry as ``|intersection| / |query_words|``.
        Returns the top-k entries above ``MIN_RELEVANCE_SCORE``,
        sorted by score descending.

        Args:
            query: Search query text.
            scope: Memory scope string for filtering.

        Returns:
            List of matching knowledge strings, most relevant first.
            At most ``max_results`` entries are returned.
        """
        query_words = _to_word_set(query)
        if not query_words:
            return []

        entries = self._entries.get(scope, [])
        scored = _score_entries(query_words, entries)
        return _select_top_results(scored, self._max_results)

    async def store(self, content: str, scope: str) -> None:
        """Store a knowledge entry in the cold tier.

        Duplicate entries (exact match) within the same scope are skipped.

        Args:
            content: Knowledge text to store.
            scope: Memory scope string for isolation.
        """
        if not content or not content.strip():
            return

        entries = self._entries[scope]
        if content not in entries:
            entries.append(content)

    async def clear(self, scope: str) -> None:
        """Remove all entries for a scope.

        Args:
            scope: Memory scope to clear.
        """
        self._entries.pop(scope, None)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _score_entries(
    query_words: set[str], entries: list[str],
) -> list[tuple[float, str]]:
    """Score entries by word overlap with pre-computed query words.

    Args:
        query_words: Lowercase word set from the search query.
        entries: Candidate knowledge strings.

    Returns:
        List of (score, entry) tuples above the relevance threshold.
    """
    scored: list[tuple[float, str]] = []
    for entry in entries:
        score = _word_overlap_score(query_words, entry)
        if score >= MIN_RELEVANCE_SCORE:
            scored.append((score, entry))
    return scored


def _select_top_results(
    scored: list[tuple[float, str]], max_results: int,
) -> list[str]:
    """Sort scored entries descending and return top-k texts.

    Args:
        scored: List of (score, entry) tuples.
        max_results: Maximum entries to return.

    Returns:
        Entry texts sorted by relevance, capped at max_results.
    """
    scored.sort(key=lambda item: -item[0])
    return [entry for _, entry in scored[:max_results]]


def _word_overlap_score(query_words: set[str], text: str) -> float:
    """Compute word-overlap ratio between query words and text.

    Args:
        query_words: Pre-computed lowercase word set from the query.
        text: Text to score against the query.

    Returns:
        Ratio of overlapping words to total query words (0.0 to 1.0).
    """
    text_words = _to_word_set(text)
    if not text_words:
        return 0.0
    overlap = len(query_words & text_words)
    return overlap / len(query_words)


def _to_word_set(text: str) -> set[str]:
    """Split text into a lowercase word set.

    Args:
        text: Input text.

    Returns:
        Set of lowercase words (whitespace-split).
    """
    return set(text.lower().split())
