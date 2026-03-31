"""Intent routing — classify messages and select handlers.

Defines the ``IntentRouter`` protocol and supporting value types.
Strategy implementations (rule-based, embedding, LLM) live in
separate modules and satisfy this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "IntentRouter",
    "IntentResult",
    "HandlerCandidate",
]

# ── Constants ────────────────────────────────────────────────────────

MIN_CONFIDENCE: float = 0.0
MAX_CONFIDENCE: float = 1.0
MIN_SCORE: float = 0.0
MAX_SCORE: float = 1.0


# ── Value objects ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HandlerCandidate:
    """A candidate handler returned by the router.

    Attributes:
        name: Handler name (must match a registry entry).
        score: Match score between 0.0 and 1.0.
        reason: Why this handler was selected (for observability).
    """

    name: str
    score: float
    reason: str = ""

    def __post_init__(self) -> None:
        """Validate score is within bounds.

        Raises:
            ValueError: If *score* is outside [0.0, 1.0].
        """
        if not (MIN_SCORE <= self.score <= MAX_SCORE):
            raise ValueError(
                f"score must be between {MIN_SCORE} and {MAX_SCORE}, got {self.score}"
            )


@dataclass(frozen=True)
class IntentResult:
    """Result of intent classification.

    Attributes:
        intent: Classified intent label (e.g. ``"book_flight"``).
        confidence: Classification confidence between 0.0 and 1.0.
        handlers: Ranked list of handler candidates, best first.
        raw_scores: Optional per-handler scores for debugging.
    """

    intent: str
    confidence: float
    handlers: list[HandlerCandidate]
    raw_scores: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate confidence is within bounds.

        Raises:
            ValueError: If *confidence* is outside [0.0, 1.0].
        """
        if not (MIN_CONFIDENCE <= self.confidence <= MAX_CONFIDENCE):
            raise ValueError(
                f"confidence must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}, "
                f"got {self.confidence}"
            )

    @property
    def best_handler(self) -> HandlerCandidate | None:
        """Return the top-ranked handler, or ``None`` if no candidates.

        Returns:
            The first element of *handlers*, or ``None`` when the list is empty.
        """
        return self.handlers[0] if self.handlers else None


# ── Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class IntentRouter(Protocol):
    """Classify a user message and select the best handler.

    Every router strategy implements this protocol.  The orchestrator
    calls :meth:`classify` and uses the result to dispatch to the
    appropriate handler in the runtime.
    """

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify intent and return ranked handler candidates.

        Args:
            message: Raw user message text.
            ctx: Execution context carrying permissions, trace id, and
                session metadata.

        Returns:
            An :class:`IntentResult` with confidence and ranked handlers.
        """
        ...
