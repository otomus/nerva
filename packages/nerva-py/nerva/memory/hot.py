"""Hot memory — current session conversation and working state (in-memory).

Provides a simple in-memory store for conversation messages, scoped by
session ID. Suitable for testing and single-process deployments where
persistence across restarts is not required.
"""

from __future__ import annotations

from collections import defaultdict

DEFAULT_MAX_MESSAGES = 100
"""Maximum conversation messages per session before oldest messages are pruned."""


class InMemoryHotMemory:
    """In-memory hot tier for session state.

    Stores conversation messages in plain lists, keyed by session ID.
    When the message count for a session exceeds ``max_messages``, the
    oldest messages are pruned to stay within the limit.

    No persistence — data is lost when the process exits.

    Args:
        max_messages: Maximum conversation messages per session before pruning.
    """

    def __init__(self, max_messages: int = DEFAULT_MAX_MESSAGES) -> None:
        self._max_messages = max_messages
        self._conversations: dict[str, list[dict[str, str]]] = defaultdict(list)

    async def add_message(self, role: str, content: str, session_id: str) -> None:
        """Append a message to a session's conversation history.

        If the conversation exceeds ``max_messages`` after insertion,
        the oldest messages are removed to enforce the limit.

        Args:
            role: Message role (e.g. ``"user"``, ``"assistant"``).
            content: Message content text.
            session_id: Session to store the message under.

        Raises:
            ValueError: If role or content is empty.
        """
        if not role or not role.strip():
            raise ValueError("role must be a non-empty string")
        if not content or not content.strip():
            raise ValueError("content must be a non-empty string")

        messages = self._conversations[session_id]
        messages.append({"role": role, "content": content})
        self._prune_if_needed(session_id)

    async def get_conversation(self, session_id: str) -> list[dict[str, str]]:
        """Return a copy of the conversation history for a session.

        Args:
            session_id: Session to retrieve messages for.

        Returns:
            List of role/content dicts, ordered oldest-first.
            Returns an empty list if the session has no history.
        """
        return list(self._conversations.get(session_id, []))

    async def clear(self, session_id: str) -> None:
        """Remove all messages for a session.

        Args:
            session_id: Session to clear.
        """
        self._conversations.pop(session_id, None)

    def _prune_if_needed(self, session_id: str) -> None:
        """Remove oldest messages if the session exceeds the limit.

        Args:
            session_id: Session to check and prune.
        """
        messages = self._conversations[session_id]
        overflow = len(messages) - self._max_messages
        if overflow > 0:
            del messages[:overflow]
