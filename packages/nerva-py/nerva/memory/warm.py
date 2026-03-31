"""Warm memory — episodes and facts with key-value semantics.

In-memory implementation of the ``WarmTier`` protocol from ``tiered.py``.
Stores episodes (ordered by insertion) and facts (deduplicated by content),
scoped by a session key. Suitable for testing and single-process deployments.
"""

from __future__ import annotations

from collections import defaultdict

DEFAULT_MAX_EPISODES = 50
"""Maximum episodes per scope before oldest are pruned."""

DEFAULT_MAX_FACTS = 200
"""Maximum facts per scope before oldest are pruned."""

MIN_RELEVANCE_SCORE = 0.1
"""Minimum word-overlap score for an entry to be considered relevant."""


class InMemoryWarmMemory:
    """In-memory warm tier storing episodes and extracted facts.

    Scoped by a session key (user_id, session_id, etc).
    Episodes are ordered by insertion. Facts are deduplicated by content.

    Retrieval uses simple word-overlap scoring against the query to return
    the most relevant entries first. All entries above ``MIN_RELEVANCE_SCORE``
    are returned; when no query overlap exists, entries are returned in
    insertion order as a fallback.

    Args:
        max_episodes: Max episodes per scope before pruning oldest.
        max_facts: Max facts per scope before pruning oldest.
    """

    def __init__(
        self,
        max_episodes: int = DEFAULT_MAX_EPISODES,
        max_facts: int = DEFAULT_MAX_FACTS,
    ) -> None:
        self._max_episodes = max_episodes
        self._max_facts = max_facts
        self._episodes: dict[str, list[str]] = defaultdict(list)
        self._facts: dict[str, list[str]] = defaultdict(list)

    async def get_episodes(self, query: str, session_id: str) -> list[str]:
        """Retrieve episodes relevant to *query* for a session.

        Uses word-overlap scoring to rank results. Entries below
        ``MIN_RELEVANCE_SCORE`` are excluded.

        Args:
            query: Search query text.
            session_id: Session scope key.

        Returns:
            List of episode strings, most relevant first.
        """
        return _rank_by_relevance(
            query, self._episodes.get(session_id, []),
        )

    async def get_facts(self, query: str, session_id: str) -> list[str]:
        """Retrieve facts relevant to *query* for a session.

        Uses word-overlap scoring to rank results. Entries below
        ``MIN_RELEVANCE_SCORE`` are excluded.

        Args:
            query: Search query text.
            session_id: Session scope key.

        Returns:
            List of fact strings, most relevant first.
        """
        return _rank_by_relevance(
            query, self._facts.get(session_id, []),
        )

    async def store(
        self, content: str, session_id: str, *, is_fact: bool = False,
    ) -> None:
        """Store content as an episode or fact.

        Episodes are appended in insertion order. Facts are deduplicated
        by exact content match. Both collections are pruned when they
        exceed their configured maximums.

        Args:
            content: Text content to store.
            session_id: Session scope key.
            is_fact: If ``True``, store as a fact (deduplicated).
                     If ``False``, store as an episode (ordered).
        """
        if not content or not content.strip():
            return

        if is_fact:
            self._store_fact(content, session_id)
        else:
            self._store_episode(content, session_id)

    async def clear(self, session_id: str) -> None:
        """Remove all episodes and facts for a session.

        Args:
            session_id: Session scope key to clear.
        """
        self._episodes.pop(session_id, None)
        self._facts.pop(session_id, None)

    # -- Private helpers -------------------------------------------------------

    def _store_episode(self, content: str, session_id: str) -> None:
        """Append an episode and prune if over limit.

        Args:
            content: Episode text.
            session_id: Session scope key.
        """
        episodes = self._episodes[session_id]
        episodes.append(content)
        _prune_oldest(episodes, self._max_episodes)

    def _store_fact(self, content: str, session_id: str) -> None:
        """Add a fact if not already present, pruning if over limit.

        Args:
            content: Fact text.
            session_id: Session scope key.
        """
        facts = self._facts[session_id]
        if content in facts:
            return
        facts.append(content)
        _prune_oldest(facts, self._max_facts)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _prune_oldest(items: list[str], max_size: int) -> None:
    """Remove oldest entries to enforce a size limit.

    Args:
        items: Mutable list to prune in place.
        max_size: Maximum allowed length.
    """
    overflow = len(items) - max_size
    if overflow > 0:
        del items[:overflow]


def _rank_by_relevance(query: str, entries: list[str]) -> list[str]:
    """Rank entries by word-overlap with the query.

    Scores each entry as ``|intersection| / |query_words|``. Entries
    below ``MIN_RELEVANCE_SCORE`` are excluded. Results are sorted
    descending by score (ties broken by insertion order).

    Args:
        query: Search query text.
        entries: Candidate entries to score.

    Returns:
        Entries above the relevance threshold, most relevant first.
    """
    query_words = _to_word_set(query)
    if not query_words:
        return list(entries)

    scored: list[tuple[float, int, str]] = []
    for index, entry in enumerate(entries):
        score = _word_overlap_score(query_words, entry)
        if score >= MIN_RELEVANCE_SCORE:
            scored.append((score, index, entry))

    # Sort by score descending, then by insertion order ascending
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored]


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
