"""Agent runtime — execute handlers with lifecycle management.

Exports the core protocol (AgentRuntime), value types (AgentInput, AgentResult),
and status enum (AgentStatus) used across the Nerva execution layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext

from nerva.runtime.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

__all__ = [
    "AgentStatus",
    "AgentInput",
    "AgentResult",
    "AgentRuntime",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "StreamChunkType",
    "StreamChunk",
    "StreamingRuntime",
]

class AgentStatus(StrEnum):
    """Outcome status of an agent invocation.

    Members:
        SUCCESS: Handler completed normally.
        ERROR: Handler raised an unrecoverable error.
        TIMEOUT: Handler exceeded its deadline.
        WRONG_HANDLER: Router selected the wrong handler for the input.
        NEEDS_DATA: Handler requires additional structured data to proceed.
        NEEDS_CREDENTIALS: Handler requires credentials not yet provided.
    """

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    WRONG_HANDLER = "wrong_handler"
    NEEDS_DATA = "needs_data"
    NEEDS_CREDENTIALS = "needs_credentials"


@dataclass(frozen=True)
class AgentInput:
    """Immutable input passed to an agent handler.

    Attributes:
        message: The user message or piped output from a previous handler.
        args: Structured arguments extracted by the router.
        tools: Available tool specs for this invocation.
        history: Relevant conversation history entries.
    """

    message: str
    args: dict[str, str] = field(default_factory=dict)
    tools: list[dict[str, str]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AgentResult:
    """Result from an agent handler invocation.

    Attributes:
        status: Outcome status of the invocation.
        output: The agent's response text.
        data: Structured data returned by the agent.
        error: Error message when status is ERROR, None otherwise.
        handler: Name of the handler that produced this result.
    """

    status: AgentStatus
    output: str = ""
    data: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    handler: str = ""


@runtime_checkable
class AgentRuntime(Protocol):
    """Execute agent handlers with lifecycle management.

    Implementations handle timeout enforcement, circuit breaking,
    structured output parsing, error classification, and streaming.
    """

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run a single handler.

        Args:
            handler: Handler name (resolved from registry).
            input: Structured input for the handler.
            ctx: Execution context carrying permissions, trace, and config.

        Returns:
            AgentResult with status and output.
        """
        ...

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run handlers in sequence, piping each output as the next input's message.

        Stops early if any handler returns a non-SUCCESS status.

        Args:
            handlers: Ordered list of handler names.
            input: Initial input for the first handler.
            ctx: Execution context shared across the chain.

        Returns:
            AgentResult from the last successfully executed handler.
        """
        ...

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Invoke a handler from within another handler (agent-to-agent delegation).

        Creates a child ExecContext with inherited permissions and trace lineage.

        Args:
            handler: Handler name to delegate to.
            input: Input for the delegated handler.
            parent_ctx: Parent's execution context.

        Returns:
            AgentResult from the delegated handler.
        """
        ...


# Late import to avoid circular dependency — streaming.py imports from this module
from nerva.runtime.streaming import StreamChunk, StreamChunkType, StreamingRuntime  # noqa: E402
