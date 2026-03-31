"""Noop policy engine — allows everything without recording.

Use during development or testing when policy enforcement is not needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.policy import ALLOW, PolicyAction, PolicyDecision

if TYPE_CHECKING:
    from nerva.context import ExecContext


class NoopPolicyEngine:
    """Policy engine that permits every action unconditionally.

    No state is tracked and ``record`` is a silent no-op. Satisfies the
    ``PolicyEngine`` protocol so it can be used as a drop-in replacement
    for any real engine.
    """

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Always returns ``ALLOW``.

        Args:
            action: The action to evaluate (ignored).
            ctx: Execution context (ignored).

        Returns:
            The pre-built ``ALLOW`` decision.
        """
        return ALLOW

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """No-op — nothing to record.

        Args:
            action: The evaluated action (ignored).
            decision: The decision made (ignored).
            ctx: Execution context (ignored).
        """
