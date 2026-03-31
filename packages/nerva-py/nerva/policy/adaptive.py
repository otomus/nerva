"""Adaptive policy engine — runtime condition monitoring with dynamic adjustments.

Layers on top of any base ``PolicyEngine`` to add:

* **Timeout extension** — when specific tags appear in ``ctx.metadata``.
* **Cost-based throttling** — emits an event and suggests a cheaper model
  when cumulative cost exceeds a threshold.
* **Cost-based pausing** — halts execution when cumulative cost exceeds a
  hard budget ceiling.

A base denial is **never** overridden. Adaptive logic only adds restrictions
or extends timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nerva.policy import ALLOW, PolicyAction, PolicyDecision

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.policy import PolicyEngine

# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

COST_DISABLED = 0.0
"""Threshold value that means "this cost gate is turned off"."""

REASON_BUDGET_EXCEEDED = "budget_exceeded_adaptive"
"""Denial reason when cumulative cost exceeds the pause threshold."""

REASON_THROTTLED = "cost_throttle_advisory"
"""Advisory reason attached to allow-decisions when throttling."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdaptivePolicyConfig:
    """Configuration for adaptive runtime policy.

    Zero values for cost thresholds mean "disabled" (no enforcement).

    Attributes:
        base_timeout_seconds: Starting timeout before adaptation.
        extend_timeout_on: Tags in ``ctx.metadata`` that trigger timeout extension.
        timeout_extension_factor: Multiplier when extending timeout (e.g. 2.0 = double).
        throttle_after_cost: Cost threshold (USD) that triggers a throttle advisory.
        pause_after_cost: Cost threshold (USD) that halts execution.
        throttle_model_downgrade: Suggested cheaper model when throttling.
    """

    base_timeout_seconds: float = 30.0
    extend_timeout_on: frozenset[str] = field(default_factory=frozenset)
    timeout_extension_factor: float = 2.0
    throttle_after_cost: float = COST_DISABLED
    pause_after_cost: float = COST_DISABLED
    throttle_model_downgrade: str = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AdaptivePolicyEngine:
    """Policy engine with runtime adaptation based on execution conditions.

    Wraps any base ``PolicyEngine`` and applies adaptive conditions after the
    base evaluation. Resolution order: base engine -> adaptive conditions.

    A base ``DENY`` is never overridden. Adaptive logic only adds restrictions
    (pause/throttle) or extensions (timeout).

    Args:
        base: Underlying policy engine (e.g. ``YamlPolicyEngine``).
        config: Adaptive policy configuration.
    """

    def __init__(self, base: PolicyEngine, config: AdaptivePolicyConfig) -> None:
        self._base = base
        self._config = config

    @property
    def config(self) -> AdaptivePolicyConfig:
        """The adaptive policy configuration.

        Returns:
            The immutable ``AdaptivePolicyConfig`` provided at init time.
        """
        return self._config

    @property
    def base(self) -> PolicyEngine:
        """The underlying base policy engine.

        Returns:
            The ``PolicyEngine`` that this adaptive engine wraps.
        """
        return self._base

    # -- Public protocol ----------------------------------------------------

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Evaluate base policy, then apply adaptive conditions.

        Evaluation order:
        1. Check base engine — if denied, return that denial immediately.
        2. Check pause threshold — if exceeded, deny with budget reason.
        3. Check throttle threshold — if exceeded, return allow with advisory.
        4. Otherwise return the base decision (possibly with budget remaining).

        Args:
            action: The action to evaluate.
            ctx: Execution context carrying identity, usage, and metadata.

        Returns:
            A ``PolicyDecision`` reflecting both base and adaptive evaluation.
        """
        base_decision = await self._base.evaluate(action, ctx)
        if not base_decision.allowed:
            return base_decision

        if self.should_pause(ctx):
            return self._build_pause_decision(ctx)

        if self.should_throttle(ctx):
            return self._build_throttle_decision(ctx)

        return base_decision

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """Delegate recording to the base engine.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context at the time of decision.
        """
        await self._base.record(action, decision, ctx)

    # -- Condition checks ---------------------------------------------------

    def should_extend_timeout(self, ctx: ExecContext) -> bool:
        """Check if any ``extend_timeout_on`` tags are present in ``ctx.metadata``.

        Args:
            ctx: Execution context whose metadata keys to inspect.

        Returns:
            ``True`` if at least one configured tag is present as a metadata key.
        """
        if not self._config.extend_timeout_on:
            return False
        return bool(self._config.extend_timeout_on & ctx.metadata.keys())

    def get_extended_timeout(self) -> float:
        """Return the extended timeout value.

        Multiplies ``base_timeout_seconds`` by ``timeout_extension_factor``.

        Returns:
            Extended timeout in seconds.
        """
        return self._config.base_timeout_seconds * self._config.timeout_extension_factor

    def should_throttle(self, ctx: ExecContext) -> bool:
        """Check if cumulative cost has exceeded the throttle threshold.

        A threshold of ``0.0`` means throttling is disabled.

        Args:
            ctx: Execution context carrying accumulated token usage.

        Returns:
            ``True`` if throttling is active and the cost threshold is exceeded.
        """
        if self._config.throttle_after_cost <= COST_DISABLED:
            return False
        return ctx.token_usage.cost_usd >= self._config.throttle_after_cost

    def should_pause(self, ctx: ExecContext) -> bool:
        """Check if cumulative cost has exceeded the pause (hard stop) threshold.

        A threshold of ``0.0`` means pausing is disabled.

        Args:
            ctx: Execution context carrying accumulated token usage.

        Returns:
            ``True`` if pausing is active and the cost threshold is exceeded.
        """
        if self._config.pause_after_cost <= COST_DISABLED:
            return False
        return ctx.token_usage.cost_usd >= self._config.pause_after_cost

    # -- Decision builders --------------------------------------------------

    def _build_pause_decision(self, ctx: ExecContext) -> PolicyDecision:
        """Build a denial decision for budget-exceeded pause.

        Args:
            ctx: Execution context (used for budget remaining calculation).

        Returns:
            A ``PolicyDecision`` denying the action with remaining budget of 0.
        """
        return PolicyDecision(
            allowed=False,
            reason=REASON_BUDGET_EXCEEDED,
            budget_remaining=0.0,
        )

    def _build_throttle_decision(self, ctx: ExecContext) -> PolicyDecision:
        """Build an allow decision with throttle advisory metadata.

        The action is still permitted, but ``budget_remaining`` reflects the
        distance to the pause threshold (or ``None`` if no pause threshold).

        Args:
            ctx: Execution context carrying accumulated cost.

        Returns:
            A ``PolicyDecision`` allowing the action with budget info.
        """
        remaining = self._compute_budget_remaining(ctx)
        return PolicyDecision(
            allowed=True,
            reason=REASON_THROTTLED,
            budget_remaining=remaining,
        )

    def _compute_budget_remaining(self, ctx: ExecContext) -> float | None:
        """Compute remaining budget distance to the pause threshold.

        Args:
            ctx: Execution context carrying accumulated cost.

        Returns:
            Remaining USD before pause, or ``None`` if no pause threshold is set.
        """
        if self._config.pause_after_cost <= COST_DISABLED:
            return None
        remaining = self._config.pause_after_cost - ctx.token_usage.cost_usd
        return max(remaining, 0.0)
