"""Boundary stubs — pure fakes for the lowest-level external boundaries.

These are not spies. They are lightweight, deterministic implementations
used where a real implementation would require external dependencies
(LLM API calls, subprocess spawning, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.policy import ALLOW, PolicyDecision
from nerva.runtime import AgentResult, AgentStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.policy import PolicyAction
    from nerva.runtime import AgentInput


# ---------------------------------------------------------------------------
# StubLLM — canned responses for runtime handlers
# ---------------------------------------------------------------------------


class StubLLMHandler:
    """A handler function that returns canned responses in sequence.

    Use this as a handler registered in ``InProcessRuntime`` to simulate
    LLM responses without hitting a real API.

    Attributes:
        responses: Queue of responses to return (FIFO).
        default_response: Fallback when the queue is empty.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        default_response: str = "stub response",
    ) -> None:
        self._responses = list(responses) if responses else []
        self._default_response = default_response
        self._call_count = 0

    @property
    def call_count(self) -> int:
        """Number of times this handler has been invoked."""
        return self._call_count

    async def __call__(self, input: AgentInput, ctx: ExecContext) -> AgentResult:
        """Return the next canned response or the default.

        Args:
            input: Agent input (ignored — responses are pre-configured).
            ctx: Execution context.

        Returns:
            AgentResult with SUCCESS status and the canned output.
        """
        self._call_count += 1
        if self._responses:
            output = self._responses.pop(0)
        else:
            output = self._default_response
        return AgentResult(status=AgentStatus.SUCCESS, output=output)


# ---------------------------------------------------------------------------
# DenyAllPolicy — always denies
# ---------------------------------------------------------------------------


class DenyAllPolicy:
    """Policy engine that denies every action.

    Useful for testing that policy denial is handled correctly.

    Attributes:
        reason: The denial reason returned in every decision.
    """

    def __init__(self, reason: str = "denied by test policy") -> None:
        self._reason = reason

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Always deny.

        Args:
            action: The action to evaluate.
            ctx: Execution context.

        Returns:
            A denial PolicyDecision with the configured reason.
        """
        return PolicyDecision(allowed=False, reason=self._reason)

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """No-op record.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context.
        """


# ---------------------------------------------------------------------------
# AllowAllPolicy — always allows (alias for NoopPolicyEngine, explicit name)
# ---------------------------------------------------------------------------


class AllowAllPolicy:
    """Policy engine that allows every action.

    Same behavior as ``NoopPolicyEngine`` but with an explicit test-oriented name.
    """

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Always allow.

        Args:
            action: The action to evaluate.
            ctx: Execution context.

        Returns:
            The ``ALLOW`` constant decision.
        """
        return ALLOW

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """No-op record.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context.
        """
