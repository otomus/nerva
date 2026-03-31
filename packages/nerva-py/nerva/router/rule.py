"""Rule-based router — deterministic regex/keyword matching (N-113).

Routes messages by testing ordered regex rules. First match wins.
Falls back to a default handler (if configured) or an empty result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nerva.router import HandlerCandidate, IntentResult

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "Rule",
    "RuleRouter",
]

# ── Constants ────────────────────────────────────────────────────────

MATCH_CONFIDENCE: float = 1.0
DEFAULT_CONFIDENCE: float = 0.5
NO_MATCH_CONFIDENCE: float = 0.0
DEFAULT_INTENT: str = "default"
NO_MATCH_INTENT: str = "unknown"


# ── Value objects ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    """A routing rule mapping a regex pattern to a handler.

    Attributes:
        pattern: Regex pattern to match against the message.
        handler: Handler name to route to on match.
        intent: Intent label for this rule.
    """

    pattern: str
    handler: str
    intent: str


# ── Router ───────────────────────────────────────────────────────────


class RuleRouter:
    """Deterministic router using regex pattern matching.

    Routes messages by testing each rule's regex pattern in order.
    First match wins.  Falls back to *default_handler* (with reduced
    confidence) or returns an empty result if no rules match.

    Args:
        rules: Ordered list of routing rules.
        default_handler: Optional fallback handler when no rules match.
    """

    def __init__(
        self,
        rules: list[Rule],
        default_handler: str | None = None,
    ) -> None:
        if not isinstance(rules, list):
            raise TypeError(f"rules must be a list, got {type(rules).__name__}")

        self._rules = rules
        self._compiled = _compile_rules(rules)
        self._default_handler = default_handler

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify a message by testing rules in order.

        Args:
            message: Raw user message text.
            ctx: Execution context (unused by rule router, but required
                by the :class:`~nerva.router.IntentRouter` protocol).

        Returns:
            :class:`~nerva.router.IntentResult` for the first matching
            rule, the default handler, or an empty no-match result.
        """
        if not message.strip():
            return _empty_result()

        match = _find_first_match(message, self._rules, self._compiled)
        if match is not None:
            return _result_from_rule(match)

        if self._default_handler is not None:
            return _default_result(self._default_handler)

        return _empty_result()


# ── Helpers ──────────────────────────────────────────────────────────


def _compile_rules(rules: list[Rule]) -> list[re.Pattern[str]]:
    """Pre-compile regex patterns for all rules.

    Args:
        rules: List of routing rules.

    Returns:
        Compiled patterns in the same order as *rules*.

    Raises:
        re.error: If any pattern is not valid regex.
    """
    return [re.compile(rule.pattern, re.IGNORECASE) for rule in rules]


def _find_first_match(
    message: str,
    rules: list[Rule],
    compiled: list[re.Pattern[str]],
) -> Rule | None:
    """Return the first rule whose pattern matches *message*.

    Args:
        message: Text to match against.
        rules: Ordered routing rules.
        compiled: Pre-compiled patterns (same order as *rules*).

    Returns:
        The first matching :class:`Rule`, or ``None``.
    """
    for rule, pattern in zip(rules, compiled):
        if pattern.search(message):
            return rule
    return None


def _result_from_rule(rule: Rule) -> IntentResult:
    """Build an IntentResult from a matched rule.

    Args:
        rule: The matched routing rule.

    Returns:
        IntentResult with full confidence and a single handler candidate.
    """
    candidate = HandlerCandidate(
        name=rule.handler,
        score=MATCH_CONFIDENCE,
        reason=f"Matched pattern: {rule.pattern}",
    )
    return IntentResult(
        intent=rule.intent,
        confidence=MATCH_CONFIDENCE,
        handlers=[candidate],
    )


def _default_result(handler: str) -> IntentResult:
    """Build an IntentResult for the default fallback handler.

    Args:
        handler: Name of the default handler.

    Returns:
        IntentResult with reduced confidence and intent ``"default"``.
    """
    candidate = HandlerCandidate(
        name=handler,
        score=DEFAULT_CONFIDENCE,
        reason="No rules matched; using default handler",
    )
    return IntentResult(
        intent=DEFAULT_INTENT,
        confidence=DEFAULT_CONFIDENCE,
        handlers=[candidate],
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
