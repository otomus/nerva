"""Policy — layered rules governing execution.

Defines the core protocol (``PolicyEngine``), data classes for actions and
decisions, and convenience constants used across all engine implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nerva.context import ExecContext


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyAction:
    """An action to be evaluated by the policy engine.

    Attributes:
        kind: Action type (invoke_agent, call_tool, delegate, store_memory, route).
        subject: Who is acting (user_id or agent_name).
        target: What they are acting on (agent_name, tool_name).
        metadata: Additional context (token_count, cost_estimate, etc.).
    """

    kind: str
    subject: str
    target: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a policy evaluation.

    Attributes:
        allowed: Whether the action is permitted.
        reason: Why denied (``None`` if allowed).
        require_approval: Whether human approval is needed before proceeding.
        approvers: Who can approve (``None`` if no approval needed).
        budget_remaining: Remaining budget after this action (``None`` if not tracked).
    """

    allowed: bool
    reason: str | None = None
    require_approval: bool = False
    approvers: list[str] | None = None
    budget_remaining: float | None = None


# ---------------------------------------------------------------------------
# Convenience constants
# ---------------------------------------------------------------------------

ALLOW = PolicyDecision(allowed=True)
"""Pre-built decision that permits the action unconditionally."""

DENY_NO_REASON = PolicyDecision(allowed=False, reason="denied by policy")
"""Pre-built denial with a generic reason string."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PolicyEngine(Protocol):
    """Evaluate and record policy decisions at every execution stage.

    Implementations must provide both ``evaluate`` (sync gate) and ``record``
    (audit trail). The runtime calls ``evaluate`` *before* executing an action
    and ``record`` *after* the decision is made, regardless of outcome.
    """

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Evaluate whether an action is allowed under current policies.

        Args:
            action: The action to evaluate.
            ctx: Execution context carrying identity, usage, and metadata.

        Returns:
            A ``PolicyDecision`` indicating allow, deny, or require_approval.
        """
        ...

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """Record a policy decision for the audit trail.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context at the time of decision.
        """
        ...
