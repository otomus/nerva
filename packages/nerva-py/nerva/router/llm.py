"""LLM router — classify intent by asking an LLM to select a handler (N-610).

Sends the full handler catalog (names + descriptions) to an LLM and parses
the JSON response to produce an IntentResult. Falls back gracefully when the
LLM returns unparseable output.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nerva.router import HandlerCandidate, IntentResult

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "LLMFunc",
    "LLMRouter",
    "LLMRouterConfig",
]

_log = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------

NO_MATCH_INTENT: str = "unknown"
NO_MATCH_CONFIDENCE: float = 0.0
LLM_INTENT: str = "llm"
DEFAULT_CONFIDENCE: float = 0.5
"""Confidence assigned when the LLM omits a confidence value."""

JSON_EXTRACT_PATTERN: re.Pattern[str] = re.compile(r"\{[^{}]*\}", re.DOTALL)
"""Regex to locate a JSON object in potentially noisy LLM output."""

DEFAULT_SYSTEM_PROMPT: str = (
    "You are an intent classifier. Given the handler catalog and a user message, "
    "select the single best handler.\n"
    "Respond ONLY with JSON: {\"handler\": \"<name>\", \"confidence\": <0.0-1.0>}\n"
    "If no handler fits, respond: {\"handler\": \"\", \"confidence\": 0.0}"
)
"""Default system prompt template sent to the LLM."""


# -- LLM function protocol --------------------------------------------------


class LLMFunc(Protocol):
    """Async function that sends a system+user prompt pair to an LLM.

    Args:
        system_prompt: Instructions for the LLM.
        user_prompt: The user-facing query to classify.

    Returns:
        Raw text response from the LLM.
    """

    async def __call__(self, system_prompt: str, user_prompt: str) -> str: ...


# -- Config ------------------------------------------------------------------


@dataclass(frozen=True)
class LLMRouterConfig:
    """Configuration for the LLM router.

    Attributes:
        system_prompt: System prompt template for the LLM.
        fallback_confidence: Confidence when LLM omits the field.
    """

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    fallback_confidence: float = DEFAULT_CONFIDENCE


# -- Internal value object ---------------------------------------------------


@dataclass(frozen=True)
class _RegisteredHandler:
    """A handler registered with the LLM router.

    Attributes:
        name: Handler identifier.
        description: Human-readable description shown to the LLM.
    """

    name: str
    description: str


# -- Router ------------------------------------------------------------------


class LLMRouter:
    """Route messages by asking an LLM to pick the best handler from a catalog.

    Builds a prompt containing all registered handler names and descriptions,
    sends it to the injected LLM function, and parses the JSON response into
    an IntentResult. Falls back to an empty result when the LLM returns
    invalid output.

    Args:
        llm: Async LLM function that accepts (system_prompt, user_prompt).
        config: Optional router configuration overrides.
    """

    def __init__(
        self,
        llm: LLMFunc,
        config: LLMRouterConfig | None = None,
    ) -> None:
        self._llm = llm
        self._config = config or LLMRouterConfig()
        self._handlers: list[_RegisteredHandler] = []

    # -- Public API ----------------------------------------------------------

    async def register(self, name: str, description: str) -> None:
        """Register a handler for LLM-based routing.

        Args:
            name: Unique handler identifier.
            description: Human-readable description shown to the LLM.

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

        self._handlers.append(_RegisteredHandler(name=name, description=description))

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify a message by asking the LLM to select a handler.

        Args:
            message: Raw user message text.
            ctx: Execution context (forwarded for observability).

        Returns:
            IntentResult with the LLM's handler selection and confidence.
        """
        if not message or not message.strip():
            return _empty_result()
        if not self._handlers:
            return _empty_result()

        user_prompt = _build_user_prompt(self._handlers, message)
        raw_response = await self._llm(self._config.system_prompt, user_prompt)

        return self._parse_response(raw_response)

    # -- Private -------------------------------------------------------------

    def _parse_response(self, raw: str) -> IntentResult:
        """Parse the LLM's JSON response into an IntentResult.

        Tries full-string JSON parse first, then regex extraction.
        Falls back to an empty result on any parse failure.

        Args:
            raw: Raw text response from the LLM.

        Returns:
            Parsed IntentResult, or empty result on failure.
        """
        parsed = _try_parse_json(raw)
        if parsed is None:
            parsed = _regex_extract_json(raw)
        if parsed is None:
            _log.warning("LLM returned unparseable response: %s", raw[:200])
            return _empty_result()

        return self._build_result_from_parsed(parsed)

    def _build_result_from_parsed(self, parsed: dict[str, object]) -> IntentResult:
        """Convert parsed JSON into an IntentResult.

        Validates the handler name exists in the catalog and clamps confidence.

        Args:
            parsed: Parsed JSON dict from the LLM response.

        Returns:
            IntentResult with the selected handler, or empty if invalid.
        """
        handler_name = str(parsed.get("handler", "")).strip()
        if not handler_name:
            return _empty_result()

        if _find_handler(self._handlers, handler_name) is None:
            _log.warning("LLM selected unknown handler: %s", handler_name)
            return _empty_result()

        confidence = _extract_confidence(parsed, self._config.fallback_confidence)
        candidate = HandlerCandidate(
            name=handler_name,
            score=confidence,
            reason="selected by LLM",
        )
        return IntentResult(
            intent=LLM_INTENT,
            confidence=confidence,
            handlers=[candidate],
            raw_scores={handler_name: confidence},
        )


# -- Pure helpers ------------------------------------------------------------


def _build_user_prompt(handlers: list[_RegisteredHandler], message: str) -> str:
    """Build the user prompt containing the handler catalog and message.

    Args:
        handlers: All registered handlers.
        message: The user message to classify.

    Returns:
        Formatted prompt string for the LLM.
    """
    catalog_lines = [f"- {h.name}: {h.description}" for h in handlers]
    catalog_text = "\n".join(catalog_lines)
    return f"Available handlers:\n{catalog_text}\n\nUser message: {message}"


def _find_handler(
    handlers: list[_RegisteredHandler], name: str
) -> _RegisteredHandler | None:
    """Look up a registered handler by name.

    Args:
        handlers: List of registered handlers.
        name: Handler name to search for.

    Returns:
        The matching handler, or None if not found.
    """
    for handler in handlers:
        if handler.name == name:
            return handler
    return None


def _try_parse_json(text: str) -> dict[str, object] | None:
    """Attempt to parse the entire text as a JSON object.

    Args:
        text: Candidate JSON string.

    Returns:
        Parsed dict if successful, None otherwise.
    """
    stripped = text.strip()
    if not stripped:
        return None
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _regex_extract_json(text: str) -> dict[str, object] | None:
    """Use regex to find and parse the first JSON object in text.

    Args:
        text: Noisy LLM output that may contain a JSON object.

    Returns:
        Parsed dict from the first valid JSON match, or None.
    """
    for match in JSON_EXTRACT_PATTERN.finditer(text):
        parsed = _try_parse_json(match.group())
        if parsed is not None:
            return parsed
    return None


def _extract_confidence(parsed: dict[str, object], fallback: float) -> float:
    """Extract and clamp the confidence value from parsed JSON.

    Args:
        parsed: Parsed LLM response dict.
        fallback: Value to use when confidence is missing or invalid.

    Returns:
        Confidence clamped to [0.0, 1.0].
    """
    raw = parsed.get("confidence")
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(1.0, value))


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
