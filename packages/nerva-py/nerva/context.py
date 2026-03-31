"""ExecContext — the connective tissue that flows through every Nerva primitive.

Every operation in Nerva receives an ExecContext. It carries identity, permissions,
observability (spans/events), token accounting, cancellation, and streaming state.
This is primitive #0: the context object that all other primitives depend on.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import uuid4


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MEMORY_SCOPE = "session"
"""Default scope for memory isolation when none is specified."""


# ---------------------------------------------------------------------------
# Scope (N-102)
# ---------------------------------------------------------------------------


class Scope(StrEnum):
    """Memory isolation boundary for context data.

    Determines how far stored facts and state are visible:
    - USER: persists across sessions for the same user
    - SESSION: scoped to a single conversation session
    - AGENT: private to the agent handling the request
    - GLOBAL: visible to all users and agents
    """

    USER = "user"
    SESSION = "session"
    AGENT = "agent"
    GLOBAL = "global"


# ---------------------------------------------------------------------------
# Permissions (N-102)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Permissions:
    """Immutable capability set governing what a context is allowed to do.

    Uses allowlists for tools and agents. A value of ``None`` means
    "no restriction" (all allowed). An empty frozenset means "none allowed".

    Attributes:
        roles: Set of role names assigned to this context (e.g. ``{"admin", "user"}``).
        allowed_tools: Tool names this context may invoke, or ``None`` for unrestricted.
        allowed_agents: Agent names this context may delegate to, or ``None`` for unrestricted.
    """

    roles: frozenset[str] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] | None = None
    allowed_agents: frozenset[str] | None = None

    def can_use_tool(self, tool_name: str) -> bool:
        """Check whether the given tool is permitted.

        Args:
            tool_name: Fully-qualified tool name to check.

        Returns:
            ``True`` if the tool is allowed (or if no restriction is set).
        """
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def can_use_agent(self, agent_name: str) -> bool:
        """Check whether delegation to the given agent is permitted.

        Args:
            agent_name: Agent identifier to check.

        Returns:
            ``True`` if the agent is allowed (or if no restriction is set).
        """
        if self.allowed_agents is None:
            return True
        return agent_name in self.allowed_agents

    def has_role(self, role: str) -> bool:
        """Check whether the context carries a specific role.

        Args:
            role: Role name to look for.

        Returns:
            ``True`` if the role is present.
        """
        return role in self.roles


# ---------------------------------------------------------------------------
# TokenUsage (N-102)
# ---------------------------------------------------------------------------


@dataclass
class TokenUsage:
    """Accumulator for LLM token consumption and estimated cost.

    Attributes:
        prompt_tokens: Number of tokens in the prompt/input.
        completion_tokens: Number of tokens in the completion/output.
        total_tokens: Combined prompt + completion tokens.
        cost_usd: Estimated cost in US dollars.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: TokenUsage) -> TokenUsage:
        """Return a new ``TokenUsage`` that is the sum of *self* and *other*.

        Neither operand is mutated.

        Args:
            other: Token usage to add.

        Returns:
            A fresh ``TokenUsage`` with summed fields.
        """
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


# ---------------------------------------------------------------------------
# Span & Event (N-102)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A timed segment of work within a request's lifecycle.

    Spans form a tree: each span may have a ``parent_id`` pointing to
    the span that created it. A ``None`` parent means a root span.

    Attributes:
        span_id: Unique identifier for this span.
        name: Human-readable label (e.g. ``"llm.call"``, ``"tool.invoke"``).
        parent_id: Span ID of the parent, or ``None`` for root spans.
        started_at: Unix timestamp when the span started.
        ended_at: Unix timestamp when the span ended, or ``None`` if still open.
        attributes: Arbitrary key-value metadata attached to the span.
    """

    span_id: str
    name: str
    parent_id: str | None
    started_at: float
    ended_at: float | None
    attributes: dict[str, str]


@dataclass(frozen=True)
class Event:
    """A point-in-time occurrence recorded within a context.

    Events are simpler than spans — they have no duration, just a timestamp
    and descriptive metadata.

    Attributes:
        timestamp: Unix timestamp of the event.
        name: Human-readable label (e.g. ``"policy.denied"``, ``"stream.started"``).
        attributes: Arbitrary key-value metadata.
    """

    timestamp: float
    name: str
    attributes: dict[str, str]


# ---------------------------------------------------------------------------
# StreamSink (N-103)
# ---------------------------------------------------------------------------


class StreamSink(Protocol):
    """Protocol for pushing incremental output chunks to a consumer.

    Implementations may write to an HTTP response, a WebSocket, a queue,
    or an in-memory buffer (for testing).
    """

    async def push(self, chunk: str) -> None:
        """Send a single chunk of output.

        Args:
            chunk: Text fragment to push downstream.
        """
        ...

    async def close(self) -> None:
        """Signal that no more chunks will be sent."""
        ...


class InMemoryStreamSink:
    """In-memory ``StreamSink`` implementation for testing.

    Collects all pushed chunks into a list so tests can assert
    on the full output without I/O.

    Attributes:
        chunks: All chunks pushed so far, in order.
        closed: Whether ``close()`` has been called.
    """

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.closed: bool = False

    async def push(self, chunk: str) -> None:
        """Append *chunk* to the internal buffer.

        Args:
            chunk: Text fragment to record.

        Raises:
            RuntimeError: If the sink has already been closed.
        """
        if self.closed:
            raise RuntimeError("Cannot push to a closed StreamSink")
        self.chunks.append(chunk)

    async def close(self) -> None:
        """Mark the sink as closed. Subsequent pushes will raise.

        Raises:
            RuntimeError: If the sink has already been closed.
        """
        if self.closed:
            raise RuntimeError("StreamSink is already closed")
        self.closed = True


# ---------------------------------------------------------------------------
# ExecContext (N-101)
# ---------------------------------------------------------------------------


@dataclass
class ExecContext:
    """Execution context that flows through every Nerva primitive.

    Carries identity, permissions, observability (spans and events),
    token accounting, cancellation signalling, and an optional stream sink.
    Contexts are created via the ``create`` factory and can spawn children
    for sub-operations via ``child``.

    Attributes:
        request_id: Unique identifier for this individual request.
        trace_id: Groups related requests into a single trace.
        user_id: Authenticated user, or ``None`` for anonymous.
        session_id: Conversation/session identifier, or ``None``.
        permissions: Capability set governing tool/agent access.
        memory_scope: Isolation boundary for stored state.
        spans: Ordered list of timed work segments.
        events: Ordered list of point-in-time occurrences.
        token_usage: Accumulated LLM token consumption.
        created_at: Unix timestamp when this context was created.
        timeout_at: Unix timestamp after which the context is timed out, or ``None``.
        cancelled: Async event that signals cancellation when set.
        stream: Optional sink for incremental output.
        metadata: Arbitrary string tags for policy conditions and routing.
    """

    request_id: str
    trace_id: str
    user_id: str | None
    session_id: str | None
    permissions: Permissions
    memory_scope: Scope
    spans: list[Span]
    events: list[Event]
    token_usage: TokenUsage
    created_at: float
    timeout_at: float | None
    cancelled: asyncio.Event
    stream: StreamSink | None
    metadata: dict[str, str]
    depth: int = 0

    @classmethod
    def create(
        cls,
        user_id: str | None = None,
        session_id: str | None = None,
        permissions: Permissions | None = None,
        memory_scope: Scope = Scope.SESSION,
        timeout_seconds: float | None = None,
        stream: StreamSink | None = None,
    ) -> ExecContext:
        """Create a new root execution context.

        Generates fresh ``request_id`` and ``trace_id``, sets the clock,
        and initialises all accumulators to empty.

        Args:
            user_id: Authenticated user identifier, or ``None`` for anonymous.
            session_id: Conversation/session identifier, or ``None``.
            permissions: Capability set. Defaults to an unrestricted ``Permissions``.
            memory_scope: Memory isolation boundary. Defaults to ``Scope.SESSION``.
            timeout_seconds: Seconds from now until the context times out, or ``None``.
            stream: Optional sink for incremental output.

        Returns:
            A fully initialised ``ExecContext`` ready for use.
        """
        now = time.time()
        timeout_at = (now + timeout_seconds) if timeout_seconds is not None else None

        return cls(
            request_id=uuid4().hex,
            trace_id=uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            permissions=permissions or Permissions(),
            memory_scope=memory_scope,
            spans=[],
            events=[],
            token_usage=TokenUsage(),
            created_at=now,
            timeout_at=timeout_at,
            cancelled=asyncio.Event(),
            stream=stream,
            metadata={},
        )

    def child(self, handler_name: str) -> ExecContext:
        """Create a child context for delegation to a sub-handler.

        The child inherits the parent's trace, permissions, memory scope,
        timeout, cancellation signal, and stream — but gets a fresh
        ``request_id``, a new root span named after *handler_name*,
        and a depth incremented by one.

        Args:
            handler_name: Label for the child operation (used as the span name).

        Returns:
            A new ``ExecContext`` linked to the same trace as the parent.
        """
        child_request_id = uuid4().hex
        root_span = Span(
            span_id=uuid4().hex,
            name=handler_name,
            parent_id=self.request_id,
            started_at=time.time(),
            ended_at=None,
            attributes={},
        )

        return ExecContext(
            request_id=child_request_id,
            trace_id=self.trace_id,
            user_id=self.user_id,
            session_id=self.session_id,
            permissions=self.permissions,
            memory_scope=self.memory_scope,
            spans=[root_span],
            events=[],
            token_usage=TokenUsage(),
            created_at=time.time(),
            timeout_at=self.timeout_at,
            cancelled=self.cancelled,
            stream=self.stream,
            metadata=dict(self.metadata),
            depth=self.depth + 1,
        )

    # -- Query helpers -----------------------------------------------------

    def is_timed_out(self) -> bool:
        """Check whether the context has exceeded its timeout.

        Returns:
            ``True`` if a timeout was set and the current time is past it.
        """
        if self.timeout_at is None:
            return False
        return time.time() > self.timeout_at

    def is_cancelled(self) -> bool:
        """Check whether cancellation has been signalled.

        Returns:
            ``True`` if the ``cancelled`` event has been set.
        """
        return self.cancelled.is_set()

    def elapsed_seconds(self) -> float:
        """Seconds elapsed since this context was created.

        Returns:
            Wall-clock seconds since ``created_at``.
        """
        return time.time() - self.created_at

    # -- Mutation helpers (append-only) ------------------------------------

    def add_span(self, name: str) -> Span:
        """Start a new span and append it to this context's span list.

        The span is created with ``ended_at=None`` (still open). Callers
        are responsible for closing it when the work completes.

        Args:
            name: Human-readable label for the span.

        Returns:
            The newly created ``Span``.
        """
        span = Span(
            span_id=uuid4().hex,
            name=name,
            parent_id=self.request_id,
            started_at=time.time(),
            ended_at=None,
            attributes={},
        )
        self.spans.append(span)
        return span

    def add_event(self, name: str, **attributes: str) -> Event:
        """Record a point-in-time event in this context.

        Args:
            name: Human-readable label for the event.
            **attributes: Arbitrary string key-value pairs attached to the event.

        Returns:
            The newly created ``Event``.
        """
        event = Event(
            timestamp=time.time(),
            name=name,
            attributes=dict(attributes),
        )
        self.events.append(event)
        return event

    def record_tokens(self, usage: TokenUsage) -> None:
        """Accumulate token usage into this context's running total.

        Args:
            usage: Token counts and cost to add.
        """
        self.token_usage = self.token_usage.add(usage)
