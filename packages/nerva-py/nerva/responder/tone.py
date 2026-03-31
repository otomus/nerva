"""Tone responder — rewrite agent output through an LLM with tone instructions (N-614).

Applies a configurable tone (casual, professional, empathetic, etc.) to text
output via an LLM rewrite. Non-text responses (errors, empty output) pass
through without modification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nerva.responder import Channel, Response
from nerva.runtime import AgentResult, AgentStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "ToneLLMFunc",
    "ToneResponder",
    "ToneConfig",
]

# -- Constants ---------------------------------------------------------------

DEFAULT_TONE: str = "professional"
"""Default tone when none is specified."""

DEFAULT_SYSTEM_PROMPT_TEMPLATE: str = (
    "Rewrite the following text in a {tone} tone. "
    "Preserve all factual content and meaning. "
    "Return ONLY the rewritten text, nothing else."
)
"""System prompt template. ``{tone}`` is replaced with the configured tone."""


# -- LLM function protocol --------------------------------------------------


class ToneLLMFunc(Protocol):
    """Async function that sends a system+user prompt pair to an LLM.

    Args:
        system_prompt: Instructions for the LLM.
        user_prompt: The text to rewrite.

    Returns:
        Rewritten text from the LLM.
    """

    async def __call__(self, system_prompt: str, user_prompt: str) -> str: ...


# -- Config ------------------------------------------------------------------


@dataclass(frozen=True)
class ToneConfig:
    """Configuration for the tone responder.

    Attributes:
        tone: Target tone (e.g. ``"casual"``, ``"professional"``, ``"empathetic"``).
        system_prompt_template: Template string with ``{tone}`` placeholder.
    """

    tone: str = DEFAULT_TONE
    system_prompt_template: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE


# -- Responder ---------------------------------------------------------------


class ToneResponder:
    """Rewrite agent output through an LLM with a configurable tone.

    Text output is sent to the LLM for rewriting. Non-text responses
    (errors, empty output) pass through without modification. Truncates
    to ``channel.max_length`` when set.

    Args:
        llm: Async LLM function for tone rewriting.
        config: Optional tone configuration overrides.
    """

    def __init__(
        self,
        llm: ToneLLMFunc,
        config: ToneConfig | None = None,
    ) -> None:
        self._llm = llm
        self._config = config or ToneConfig()

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Format agent output with tone rewriting.

        Passes through without LLM call when the output is empty or the
        agent status indicates an error.

        Args:
            output: Raw agent result from the runtime.
            channel: Target delivery channel.
            ctx: Execution context (forwarded for observability).

        Returns:
            Response with tone-rewritten text, or passthrough for non-text.
        """
        if _should_passthrough(output):
            return _passthrough_response(output, channel)

        rewritten = await self._rewrite(output.output)
        text = _apply_max_length(rewritten, channel.max_length)

        return Response(text=text, channel=channel)

    # -- Private -------------------------------------------------------------

    async def _rewrite(self, text: str) -> str:
        """Send text to the LLM for tone rewriting.

        Falls back to the original text if the LLM returns empty.

        Args:
            text: Original text to rewrite.

        Returns:
            Rewritten text, or original on failure.
        """
        system_prompt = self._config.system_prompt_template.format(
            tone=self._config.tone
        )
        try:
            result = await self._llm(system_prompt, text)
            return result.strip() if result and result.strip() else text
        except Exception:  # noqa: BLE001
            return text


# -- Pure helpers ------------------------------------------------------------


def _should_passthrough(output: AgentResult) -> bool:
    """Determine whether the output should skip tone rewriting.

    Args:
        output: Agent result to inspect.

    Returns:
        True if the output is empty or represents an error.
    """
    if output.status != AgentStatus.SUCCESS:
        return True
    if not output.output or not output.output.strip():
        return True
    return False


def _passthrough_response(output: AgentResult, channel: Channel) -> Response:
    """Build a response without tone rewriting.

    Args:
        output: Raw agent result.
        channel: Target delivery channel.

    Returns:
        Response with the raw output text.
    """
    text = output.output or output.error or ""
    text = _apply_max_length(text, channel.max_length)
    return Response(text=text, channel=channel)


def _apply_max_length(text: str, max_length: int) -> str:
    """Truncate text to max_length if the channel defines a positive limit.

    Args:
        text: Text to truncate.
        max_length: Maximum character count (0 = unlimited).

    Returns:
        Truncated text.
    """
    if max_length > 0:
        return text[:max_length]
    return text
